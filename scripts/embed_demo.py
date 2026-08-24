#!/usr/bin/env python3
from __future__ import annotations

import argparse

import numpy as np

from raft.config import get_settings
from raft.db import Database, utc_now


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze BGE embeddings for the accepted live-labelled demo dataset.")
    parser.add_argument("--allow-unlabelled-mock", action="store_true", help="Development only; never use this corpus as competition truth.")
    args = parser.parse_args()
    settings = get_settings()
    database = Database(settings.raft_database_path)
    database.initialize()
    total = database.trace_count()
    proven = database.fetch_one("SELECT COUNT(*) AS count FROM trace_label_provenance")
    if not args.allow_unlabelled_mock and int(proven["count"] if proven else 0) != total:
        raise SystemExit("Refusing to freeze embeddings before every trace has live label provenance.")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise SystemExit("Install the pinned embedding extra first: pip install -e '.[ml]'") from error
    rows = database.fetch_all("SELECT id,user_request,summary FROM traces ORDER BY id")
    model = SentenceTransformer(settings.raft_embedding_model, revision=settings.raft_embedding_revision)
    vectors = model.encode(
        [f"{row['summary']} {row['user_request']}" for row in rows],
        batch_size=64,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    database.execute_many(
        "UPDATE traces SET embedding=? WHERE id=?",
        [(np.asarray(vector, dtype=np.float32).tobytes(), row["id"]) for row, vector in zip(rows, vectors, strict=True)],
    )
    for key, value in {
        "embedding_model": settings.raft_embedding_model,
        "embedding_revision": settings.raft_embedding_revision,
        "embedding_dimensions": str(vectors.shape[1]),
        "embedding_trace_count": str(len(rows)),
    }.items():
        database.execute(
            """
            INSERT INTO dataset_provenance(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
            """,
            (key, value, utc_now()),
        )
    print(f"Frozen {len(rows)} embeddings from {settings.raft_embedding_model}@{settings.raft_embedding_revision}.")


if __name__ == "__main__":
    main()
