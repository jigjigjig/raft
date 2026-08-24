"""Measure the local aspect judge against the corpus's own ground truth.

The generated corpus knows which intent produced each conversation, so an aspect
question that targets one intent has a checkable answer. This is the honest way
to state what the no-credentials judge can and cannot do, and the thresholds in
`raft/judge.py` were tuned against it.

    python scripts/eval_judge.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raft.analysis import AnalysisManager
from raft.config import Settings
from raft.db import Database
from raft.demo import build_dataset


CASES: list[tuple[str, set[str]]] = [
    ("Did the assistant suggest a specific product to the user?", {"recommend-product", "compare-two-products"}),
    ("Did the user ask to cancel an order or subscription?", {"cancel-just-placed", "cancel-subscription"}),
    ("Did the user ask about a visa or entry requirement?", {"visa-requirements"}),
    ("Did the user ask about shipping or delivery?", {"where-is-my-order", "change-address-after-dispatch"}),
    ("Did the user ask about pricing or a discount?", {"discount-code", "price-match", "pricing-and-contract"}),
    ("Did the user ask the agent to deploy or open a pull request?", {"deploy-or-open-pr"}),
    ("Did the user report a damaged or missing item?", {"damaged-on-arrival", "missing-item"}),
    ("Did the user ask about single sign-on?", {"configure-sso"}),
    ("Did the user ask for a refund?", {"issue-a-refund", "refund-cancelled-flight"}),
    ("Did the user ask how a garment fits?", {"sizing-and-fit"}),
]


async def evaluate(manager: AnalysisManager, question: str, truth: set[str]) -> dict:
    manager.db.execute("DELETE FROM aspect_values")
    manager.db.execute("DELETE FROM aspects")
    plan = await manager.plan_question(question)
    run = manager.create_run(plan)
    if plan.requires_confirmation:
        manager.confirm_run(run.id, plan.aspect.question if plan.aspect else None)
    task = manager.tasks.get(run.id)
    if task:
        await task
    row = manager.db.get_run(run.id)
    if not row or row["status"] != "complete":
        return {"question": question, "error": (row or {}).get("error") or "did not complete"}
    rows = manager.db.fetch_all(
        "SELECT av.value_json, t.intent_key FROM aspect_values av JOIN traces t ON t.id = av.trace_id "
        "WHERE av.aspect_id = ?",
        (row["aspect_id"],),
    )
    tp = fp = fn = 0
    for record in rows:
        predicted = json.loads(record["value_json"]) is True
        actual = record["intent_key"] in truth
        tp += predicted and actual
        fp += predicted and not actual
        fn += (not predicted) and actual
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "question": question,
        "path": plan.path,
        "predicted_yes": tp + fp,
        "actual_yes": tp + fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


async def main() -> int:
    database_path = Path("data/eval.db")
    database_path.unlink(missing_ok=True)
    settings = Settings(
        raft_otari_mode="mock",
        raft_database_path=database_path,
        raft_model_roles_path=Path("model-roles.yaml"),
    )
    db = Database(database_path)
    db.initialize()
    build_dataset(db, count=847, seed=20260821)
    manager = AnalysisManager(settings, db)
    manager.index.ensure()

    results = [await evaluate(manager, question, truth) for question, truth in CASES]
    print(f"{'question':60} {'yes':>5} {'true':>5} {'P':>5} {'R':>5} {'F1':>5}")
    for result in results:
        if "error" in result:
            print(f"{result['question'][:60]:60} ERROR {result['error']}")
            continue
        print(
            f"{result['question'][:60]:60} {result['predicted_yes']:5} {result['actual_yes']:5} "
            f"{result['precision']:5.2f} {result['recall']:5.2f} {result['f1']:5.2f}"
        )
    scored = [result for result in results if "f1" in result]
    mean = sum(result["f1"] for result in scored) / len(scored) if scored else 0.0
    print(f"\nmean F1 {mean:.3f} over {len(scored)} labelled aspect questions")
    database_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
