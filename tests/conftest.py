from __future__ import annotations

from pathlib import Path

import pytest

from raft.analysis import AnalysisManager
from raft.config import Settings
from raft.db import Database
from raft.demo import build_dataset


ROOT = Path(__file__).resolve().parents[1]


def make_manager(tmp_path: Path, *, count: int = 220, allowance: float = 0.10) -> AnalysisManager:
    settings = Settings(
        raft_otari_mode="mock",
        raft_database_path=tmp_path / "raft.db",
        raft_model_roles_path=ROOT / "model-roles.yaml",
        raft_demo_trace_count=count,
        raft_local_run_allowance_usd=allowance,
    )
    database = Database(settings.raft_database_path)
    database.initialize()
    build_dataset(database, count=count, seed=42)
    manager = AnalysisManager(settings, database)
    manager.index.ensure()
    return manager


@pytest.fixture(scope="module")
def manager(tmp_path_factory) -> AnalysisManager:
    """One built dataset per test module; building it is the slow part."""
    return make_manager(tmp_path_factory.mktemp("raft"))


async def answer_for(manager: AnalysisManager, question: str) -> dict:
    """Plan, run and return the finished answer for a question."""
    plan = await manager.plan_question(question)
    run = manager.create_run(plan)
    if plan.requires_confirmation:
        manager.confirm_run(run.id, plan.aspect.question if plan.aspect else None)
    task = manager.tasks.get(run.id)
    if task:
        await task
    row = manager.db.get_run(run.id)
    assert row, f"run vanished for {question!r}"
    assert row["status"] == "complete", f"{question!r} ended {row['status']}: {row['error']}"
    answer = manager.db.get_answer_for_run(run.id)
    assert answer
    answer["_run"] = row
    answer["_plan"] = plan
    return answer
