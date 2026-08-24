"""Measure how well a question's focus selects the conversations it is about.

The failure this guards against is subtle: when a question's wording matches
nothing, the answer silently becomes a grouping of the entire dataset, which is
identical for every such question. Narrowing has to be both accurate and
generous - miss the topic and the question was ignored, over-narrow and the
shares stop meaning anything.

    python scripts/eval_focus.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raft.analysis import AnalysisManager
from raft.config import Settings
from raft.db import Database
from raft.demo import build_dataset


CASES: list[tuple[str, set[str]]] = [
    ("shipped parcel arrive tracking address delivered", {"where-is-my-order", "change-address-after-dispatch"}),
    ("dry cleaner wash shrink care label", {"fabric-care"}),
    ("flight cancelled refund airline owed", {"refund-cancelled-flight", "issue-a-refund"}),
    ("deploy staging pull request branch review", {"deploy-or-open-pr"}),
    ("visa passport entry stay country", {"visa-requirements"}),
    ("size chart fit true to size between sizes", {"sizing-and-fit"}),
    ("split payment two cards gift card", {"split-payment"}),
]


def main() -> int:
    path = Path("data/focus-eval.db")
    path.unlink(missing_ok=True)
    settings = Settings(
        raft_otari_mode="mock",
        raft_database_path=path,
        raft_model_roles_path=Path("model-roles.yaml"),
    )
    db = Database(path)
    db.initialize()
    build_dataset(db, count=847, seed=20260821)
    manager = AnalysisManager(settings, db)
    manager.index.ensure()

    rows = db.fetch_all("SELECT id, intent_key FROM traces ORDER BY id")
    every = [row["id"] for row in rows]
    truth = {row["id"]: row["intent_key"] for row in rows}

    print(f"{'focus':46} {'kept':>5} {'P':>5} {'R':>5}")
    precisions, recalls = [], []
    for focus, intents in CASES:
        kept, note = manager.focus_on_question(focus, every)
        if note is None:
            print(f"{focus[:46]:46} {'-':>5}  did not narrow")
            precisions.append(0.0)
            recalls.append(0.0)
            continue
        hits = sum(1 for trace_id in kept if truth[trace_id] in intents)
        expected = sum(1 for trace_id in every if truth[trace_id] in intents)
        precision = hits / len(kept)
        recall = hits / expected if expected else 0.0
        precisions.append(precision)
        recalls.append(recall)
        print(f"{focus[:46]:46} {len(kept):5} {precision:5.2f} {recall:5.2f}")

    print(
        f"\nmean precision {sum(precisions) / len(precisions):.2f}, "
        f"mean recall {sum(recalls) / len(recalls):.2f} over {len(CASES)} topics"
    )
    path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
