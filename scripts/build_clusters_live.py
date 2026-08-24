#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter, defaultdict

import numpy as np

from raft.analysis import ClusterNamingResult
from raft.clustering import choose_k, kmeans_cosine
from raft.config import get_settings
from raft.db import Database, utc_now
from raft.otari import OtariClient


async def main() -> None:
    settings = get_settings()
    if settings.raft_otari_mode != "live":
        raise SystemExit("Set RAFT_OTARI_MODE=live; accepted cluster names must come from the assigned Otari model.")
    database = Database(settings.raft_database_path)
    database.initialize()
    provenance = database.fetch_one("SELECT value FROM dataset_provenance WHERE key='embedding_revision'")
    if not provenance:
        raise SystemExit("Run scripts/embed_demo.py after the live label gate first.")
    rows = database.fetch_all("SELECT id,user_request,topic,embedding FROM traces WHERE outcome!='resolved' ORDER BY id")
    vectors = np.stack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
    k = choose_k(vectors)
    labels, _ = kmeans_cosine(vectors, k)
    representatives: dict[str, list[str]] = defaultdict(list)
    for row, label in zip(rows, labels, strict=True):
        if len(representatives[str(int(label))]) < 5:
            representatives[str(int(label))].append(row["user_request"])
    client = OtariClient(settings, database)
    completion, naming = await client.complete(
        "cluster_namer",
        [{"role": "system", "content": "Name every emergent cluster in concise app-neutral language. Do not count."}, {"role": "user", "content": json.dumps(representatives)}],
        response_schema=ClusterNamingResult,
    )
    assert naming
    run_id = f"cluster_{uuid.uuid4().hex[:12]}"
    database.execute(
        "INSERT INTO cluster_runs(id,model_id,prompt_version,request_id,interpretation,created_at) VALUES(?,?,?,?,?,?)",
        (run_id, completion.model or client.role("cluster_namer").primary, "cluster-name-v1", completion.request_id, naming.interpretation, utc_now()),
    )
    database.execute_many(
        "INSERT INTO cluster_members(cluster_run_id,trace_id,cluster_key,cluster_label) VALUES(?,?,?,?)",
        [(run_id, row["id"], str(int(label)), naming.names.get(str(int(label)), f"Cluster {int(label) + 1}")) for row, label in zip(rows, labels, strict=True)],
    )
    print(json.dumps({"cluster_run_id": run_id, "request_id": completion.request_id, "model": completion.model, "clusters": Counter(map(int, labels))}, default=dict, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
