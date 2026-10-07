"""ERGOSENSE edge pipeline: camera -> pose -> REBA -> MQTT, as three cooperating processes.

    [capture] --frames_q--> [scoring] --events_q--> [publisher] --MQTT QoS 1--> broker
        ^                       |
        +------ display_q ------+   (small REBA result for the on-screen overlay)

Producer-consumer design (the OS part of the project):
  * Each stage is a separate OS process, so MediaPipe, REBA scoring and network I/O run in
    parallel on different CPU cores and a slow network can never freeze the camera.
  * Stages talk only through BOUNDED multiprocessing queues (the classic bounded buffer).
    The bound caps memory use and gives back-pressure.
  * frames_q / display_q hold live data: when full, the oldest item is dropped (a stale frame
    is useless). events_q holds data we must keep: the producer blocks instead of dropping.
  * Shutdown: Ctrl+C or Q sets a shared Event; each stage then pushes a None "poison pill"
    downstream, so every queue is drained in order before the processes exit.

Usage (from the repo root, venv active):
    python -m ergosense.edge.pipeline --station S4                    # webcam
    python -m ergosense.edge.pipeline --station S4 --demo-speed 60    # 1 s on camera = 1 min of work
    python -m ergosense.edge.pipeline --station S4 --replay dataset/BentOverRow   # FitCV recordings
    python -m ergosense.edge.pipeline --simulate --stations S1,S2,S3,S4 --speed 60
"""
import argparse
import logging
import multiprocessing as mp
import queue
import time
from pathlib import Path

from ergosense import config
from ergosense.edge.outbox import Outbox
from ergosense.edge.proc import child_init
from ergosense.edge.publisher import EventPublisher
from ergosense.events import encode, topic_for

log = logging.getLogger("edge.pipeline")
OUTBOX_DIR = Path(__file__).resolve().parent / "outbox"


def run_publisher(events_q, outbox_path, status, stop_event):
    """Process target: events_q -> SQLite outbox -> MQTT."""
    child_init()
    outbox = Outbox(outbox_path)
    publisher = EventPublisher(config.MQTT_HOST, config.MQTT_PORT, outbox)
    publisher.start()
    if len(outbox):
        log.info("%d events left over from a previous run will be sent first", len(outbox))

    total_bytes, count, done = 0, 0, False
    last_report = time.time()
    while True:
        try:
            event = events_q.get(timeout=0.1)
            if event is None:
                done = True
            else:
                payload = encode(event)
                outbox.append(event["event_id"], topic_for(event["station_id"]), payload)
                total_bytes += len(payload)
                count += 1
        except queue.Empty:
            pass

        publisher.pump()
        status["connected"].value = 1 if publisher.connected else 0
        status["pending"].value = publisher.pending
        status["sent"].value = publisher.sent
        if count:
            status["avg_event_bytes"].value = total_bytes / count

        if time.time() - last_report > 10:
            last_report = time.time()
            log.info("%s | published %d, waiting in outbox %d",
                     "online" if publisher.connected else "OFFLINE (buffering to disk)",
                     publisher.sent, publisher.pending)
            if count:
                log_bandwidth(total_bytes / count, status)

        if done:
            # give the broker a few seconds to acknowledge what is left, then exit;
            # anything still unacknowledged stays in the outbox file for next time
            deadline = time.time() + 5
            while publisher.pending and publisher.connected and time.time() < deadline:
                publisher.pump()
                time.sleep(0.05)
            break

    if count:
        log_bandwidth(total_bytes / count, status)
    log.info("publisher stopped: %d events published, %d left in %s", publisher.sent, publisher.pending,
             outbox_path.name)
    publisher.stop()
    outbox.close()


def log_bandwidth(avg_event_bytes, status):
    raw = status["raw_frame_bytes"].value or 640 * 480 * 3
    jpeg = status["jpeg_frame_bytes"].value
    msg = (f"bandwidth: avg event {avg_event_bytes:.0f} B vs raw frame {raw:,.0f} B "
           f"({raw / avg_event_bytes:,.0f}x smaller)")
    if jpeg:
        msg += f", vs JPEG frame {jpeg:,.0f} B ({jpeg / avg_event_bytes:,.0f}x smaller)"
    msg += f"; per second at 1 event/s vs 15 FPS raw video: {avg_event_bytes:.0f} B/s vs {raw * 15 / 1e6:.1f} MB/s"
    log.info(msg)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--station", default="S4", help="station this camera watches (default S4)")
    parser.add_argument("--worker", default=None,
                        help="worker id; omit to let the backend use the station's current assignment")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index (default 0)")
    parser.add_argument("--replay", metavar="DIR_OR_CSV", help="replay FitCV landmark recordings instead of a camera")
    parser.add_argument("--replay-fps", type=int, default=15)
    parser.add_argument("--replay-loops", type=int, default=1, help="0 = loop forever")
    parser.add_argument("--simulate", action="store_true", help="publish simulated events, no camera needed")
    parser.add_argument("--stations", default="S1,S2,S3,S4", help="stations to simulate (with --simulate)")
    parser.add_argument("--speed", type=float, default=60,
                        help="simulation: seconds of work per real second (60 = 8 h shift in 8 min)")
    parser.add_argument("--rate", type=float, default=1.0, help="simulation: events per second per station")
    parser.add_argument("--hours", type=float, default=8.0, help="simulation: hours of work to simulate")
    parser.add_argument("--seed", type=int, default=None, help="simulation: random seed")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between published events")
    parser.add_argument("--demo-speed", type=float, default=1.0,
                        help="multiply event duration, e.g. 60 so 1 s on camera counts as 1 min of work")
    parser.add_argument("--load-kg", type=float, default=0.0, help="load handled by the worker (REBA load/force)")
    parser.add_argument("--coupling", type=int, default=0, choices=range(4),
                        help="REBA coupling: 0 good, 1 fair, 2 poor, 3 unacceptable")
    parser.add_argument("--smooth-window", type=int, default=15, help="frames in the median smoothing window")
    parser.add_argument("--no-window", action="store_true", help="do not open the OpenCV window")
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-15s %(message)s", datefmt="%H:%M:%S")
    args = parse_args()
    OUTBOX_DIR.mkdir(exist_ok=True)

    stop_event = mp.Event()
    status = {
        "connected": mp.Value("b", 0), "pending": mp.Value("i", 0), "sent": mp.Value("i", 0),
        "avg_event_bytes": mp.Value("d", 0.0), "raw_frame_bytes": mp.Value("d", 0.0),
        "jpeg_frame_bytes": mp.Value("d", 0.0),
    }
    events_q = mp.Queue(maxsize=1000)
    processes = []

    if args.simulate:
        from ergosense.edge.simulate import run_simulator
        stations = [s.strip() for s in args.stations.split(",") if s.strip()]
        outbox_path = OUTBOX_DIR / "simulator.db"
        processes.append(mp.Process(target=run_simulator, name="simulator",
                                    args=(events_q, stations, args.speed, args.rate, args.hours, args.seed,
                                          stop_event)))
    else:
        from ergosense.edge.capture import run_replay, run_webcam
        from ergosense.edge.scoring import run_scoring
        frames_q = mp.Queue(maxsize=30)
        display_q = mp.Queue(maxsize=2)
        outbox_path = OUTBOX_DIR / f"{args.station}.db"
        settings = {
            "station_id": args.station, "worker_id": args.worker,
            "source": "replay" if args.replay else "webcam",
            "interval": args.interval, "demo_speed": args.demo_speed, "smooth_window": args.smooth_window,
            "load_kg": args.load_kg, "coupling": args.coupling,
        }
        if args.replay:
            target = Path(args.replay)
            paths = sorted(target.glob("*.csv")) if target.is_dir() else [target]
            if not paths:
                raise SystemExit(f"no CSV recordings found in {target}")
            capture = mp.Process(target=run_replay, name="capture",
                                 args=(paths, args.replay_fps, args.replay_loops, frames_q, display_q, status,
                                       stop_event, not args.no_window))
        else:
            capture = mp.Process(target=run_webcam, name="capture",
                                 args=(args.camera, frames_q, display_q, status, stop_event, not args.no_window))
        processes.append(capture)
        processes.append(mp.Process(target=run_scoring, name="scoring",
                                    args=(frames_q, events_q, display_q, settings, stop_event)))

    processes.append(mp.Process(target=run_publisher, name="publisher",
                                args=(events_q, outbox_path, status, stop_event)))
    for p in processes:
        p.start()
    log.info("started %s (pids %s) - press Ctrl+C to stop",
             ", ".join(p.name for p in processes), ", ".join(str(p.pid) for p in processes))

    try:
        while any(p.is_alive() for p in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("stopping... (draining queues)")
        stop_event.set()
        for p in processes:
            p.join(timeout=15)
    for p in processes:
        if p.is_alive():
            log.warning("%s did not stop in time, terminating", p.name)
            p.terminate()


if __name__ == "__main__":
    main()
