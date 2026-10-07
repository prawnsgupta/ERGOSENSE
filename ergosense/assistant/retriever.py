"""Retrieve the most relevant reference chunks for a query.

Primary: semantic search with pgvector (cosine distance, HNSW index).
Fallback: PostgreSQL full-text search, used only if the embedding model cannot be loaded
(for example on a machine that has never been online to download it).
"""
import logging

from ergosense import config
from ergosense.backend.db import get_pool, vector_literal

log = logging.getLogger("assistant.retriever")


def retrieve(query, k=None):
    """Top-k chunks: [{chunk_id, doc_id, title, section, content, score, method}], best first."""
    k = k or config.RAG_TOP_K
    try:
        from ergosense.assistant.embeddings import embed
        vector = vector_literal(embed([query])[0])
    except Exception as exc:                            # model download/load failed
        log.warning("embedding model unavailable (%s) - using keyword search", exc)
        return _keyword_search(query, k)

    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT c.chunk_id, c.doc_id, d.title, c.section, c.content,
                      1 - (c.embedding <=> %s::vector) AS score
               FROM doc_chunks c JOIN documents d USING (doc_id)
               ORDER BY c.embedding <=> %s::vector
               LIMIT %s""",
            (vector, vector, k),
        ).fetchall()
    return [{**r, "score": round(float(r["score"]), 3), "method": "vector"} for r in rows]


def _keyword_search(query, k):
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT c.chunk_id, c.doc_id, d.title, c.section, c.content,
                      ts_rank(c.tsv, websearch_to_tsquery('english', %s)) AS score
               FROM doc_chunks c JOIN documents d USING (doc_id)
               ORDER BY score DESC
               LIMIT %s""",
            (query.replace("#", " "), k),
        ).fetchall()
    return [{**r, "score": round(float(r["score"]), 3), "method": "keyword"} for r in rows]
