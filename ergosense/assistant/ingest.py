"""Load the reference documents into the RAG store (PostgreSQL + pgvector).

    python -m ergosense.assistant.ingest

Each Markdown file in assistant/docs is one document; every "## " section becomes one chunk
with the id "<file name>#<section number>", e.g. "safe_lifting_niosh#3". Sections are short
(one idea each), so they make natural retrieval units and readable citations.
"""
import re

import psycopg

from ergosense import config
from ergosense.assistant.embeddings import embed
from ergosense.backend.db import vector_literal

DOCS_DIR = config.PACKAGE_DIR / "assistant" / "docs"


def chunk_markdown(text, doc_id):
    """Split a Markdown document into (chunk_id, section title, body) by '## ' headings."""
    title_match = re.search(r"^# (.+)$", text, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else doc_id
    chunks = []
    for n, part in enumerate(re.split(r"^## ", text, flags=re.MULTILINE)[1:], start=1):
        heading, _, body = part.partition("\n")
        body = " ".join(body.split())                    # collapse newlines and extra spaces
        if body:
            chunks.append((f"{doc_id}#{n}", heading.strip(), body))
    return title, chunks


def ingest(dsn=None):
    files = sorted(DOCS_DIR.glob("*.md"))
    total = 0
    with psycopg.connect(dsn or config.DATABASE_URL) as conn:
        for path in files:
            doc_id = path.stem
            title, chunks = chunk_markdown(path.read_text(encoding="utf-8"), doc_id)
            # the section title is embedded together with the text, it carries a lot of meaning
            vectors = embed([f"{title}. {section}. {body}" for _, section, body in chunks])
            with conn.transaction():
                conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))    # cascades to chunks
                conn.execute("INSERT INTO documents (doc_id, title, source_file) VALUES (%s, %s, %s)",
                             (doc_id, title, f"ergosense/assistant/docs/{path.name}"))
                for (chunk_id, section, body), vector in zip(chunks, vectors):
                    conn.execute(
                        "INSERT INTO doc_chunks (chunk_id, doc_id, section, content, embedding) "
                        "VALUES (%s, %s, %s, %s, %s::vector)",
                        (chunk_id, doc_id, section, body, vector_literal(vector)),
                    )
            total += len(chunks)
            print(f"{doc_id}: {len(chunks)} chunks  ({title})")
    print(f"ingested {total} chunks from {len(files)} documents")
    return total


if __name__ == "__main__":
    ingest()
