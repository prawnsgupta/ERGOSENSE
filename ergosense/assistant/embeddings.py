"""Sentence embeddings with a small local model (all-MiniLM-L6-v2, 384 dimensions).

The model (~90 MB) is downloaded from Hugging Face on first use and cached; after that it
runs offline on the CPU. Vectors are L2-normalised, so cosine similarity = dot product.
"""
import logging
import threading

from ergosense import config

log = logging.getLogger("assistant.embeddings")
_model = None
_lock = threading.Lock()


def get_model():
    global _model
    with _lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            logging.getLogger("httpx").setLevel(logging.WARNING)
            try:
                # use the cached copy without asking Hugging Face (works offline after the first run)
                _model = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu", local_files_only=True)
            except Exception:
                log.info("downloading embedding model %s (first run only)", config.EMBEDDING_MODEL)
                _model = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu")
    return _model


def embed(texts):
    """List of strings -> list of 384-float lists."""
    vectors = get_model().encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]
