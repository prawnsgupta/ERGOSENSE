"""Draw the workstation zones for one camera and save them as a zones file.

    python -m ergosense.edge.zone_setup --camera 0 --out zones/cam1.json

1. The live camera opens. Arrange it so every workstation is in view, then press SPACE to freeze.
2. Drag a rectangle around each worker's standing/working area (head to feet, with room to bend).
3. After each rectangle, type the station id (e.g. ST01) and the load handled there in the terminal.
4. U = undo the last zone, S = save and quit, Q = quit without saving.
"""
import argparse
import json
from pathlib import Path

import cv2

WINDOW = "ERGOSENSE zone setup - SPACE freeze, drag zones, U undo, S save, Q quit"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--image", help="draw on a saved photo instead of the live camera")
    parser.add_argument("--out", required=True, help="zones file to write, e.g. zones/cam1.json")
    parser.add_argument("--no-mirror", action="store_true", help="do not mirror the image (must match the pipeline)")
    args = parser.parse_args()
    mirror = not args.no_mirror

    if args.image:
        frame = cv2.imread(args.image)
    else:
        cap = cv2.VideoCapture(args.camera)
        frame = None
        while True:
            ok, img = cap.read()
            if not ok:
                continue
            frame = cv2.flip(img, 1) if mirror else img
            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord(" "):
                break
            if key in (ord("q"), 27):
                return
        cap.release()
    h, w = frame.shape[:2]
    zones, drag = [], {}

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            drag["start"], drag["end"] = (x, y), (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and "start" in drag:
            drag["end"] = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and "start" in drag:
            (x1, y1), (x2, y2) = drag.pop("start"), (x, y)
            drag.pop("end", None)
            if abs(x2 - x1) < 20 or abs(y2 - y1) < 20:
                return
            rect = [round(min(x1, x2) / w, 4), round(min(y1, y2) / h, 4),
                    round(max(x1, x2) / w, 4), round(max(y1, y2) / h, 4)]
            sid = input("Station id for this zone (e.g. ST01): ").strip()
            if not sid:
                return
            load = input("  load handled there in kg [0]: ").strip() or "0"
            coupling = input("  grip/coupling 0 good, 1 fair, 2 poor, 3 unacceptable [0]: ").strip() or "0"
            zones.append({"station_id": sid, "rect": rect, "load_kg": float(load), "coupling": int(coupling)})
            print(f"  added {sid} {rect}")

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)
    while True:
        view = frame.copy()
        for z in zones:
            x1, y1, x2, y2 = z["rect"]
            cv2.rectangle(view, (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h)), (40, 200, 40), 2)
            cv2.putText(view, z["station_id"], (int(x1 * w) + 6, int(y1 * h) + 22), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (40, 200, 40), 2)
        if "start" in drag:
            cv2.rectangle(view, drag["start"], drag["end"], (0, 200, 255), 2)
        cv2.imshow(WINDOW, view)
        key = cv2.waitKey(20) & 0xFF
        if key == ord("u") and zones:
            print("  removed", zones.pop()["station_id"])
        elif key == ord("s"):
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"camera": args.camera, "mirror": mirror, "zones": zones}, indent=2),
                           encoding="utf-8")
            print(f"saved {len(zones)} zones to {out}")
            break
        elif key in (ord("q"), 27):
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
