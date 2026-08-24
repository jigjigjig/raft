"""Aggregation runs the same source it displays, and no model writes it."""

from __future__ import annotations

import pytest

from raft.otari import SandboxClient
from raft.query import DatasetCatalog, QuestionCompiler, aggregation_code


def code_for(question: str) -> str:
    return aggregation_code(QuestionCompiler(DatasetCatalog({})).compile(question))


def test_local_reference_counts_and_sums_only_eligible_rows() -> None:
    result = SandboxClient.local_reference(
        [
            {"trace_id": "a", "group": "yes", "value": 0.02, "eligible": True},
            {"trace_id": "b", "group": "no", "value": 0.03, "eligible": True},
            {"trace_id": "c", "group": "no", "value": 50, "eligible": False},
        ],
        code_for("Which failure mode costs me the most?"),
    )
    assert result["denominator"] == 2
    assert result["value_sum"] == pytest.approx(0.05)
    assert {group["key"]: group["count"] for group in result["groups"]} == {"yes": 1, "no": 1}
    assert result["execution"] == "local_reference"


def test_shares_sum_to_one_and_groups_sum_to_the_denominator() -> None:
    rows = [{"trace_id": str(index), "group": f"g{index % 4}", "value": index, "eligible": True} for index in range(37)]
    result = SandboxClient.local_reference(rows, code_for("how many conversations by app"))
    assert sum(group["count"] for group in result["groups"]) == result["denominator"] == 37
    assert sum(group["share"] for group in result["groups"]) == pytest.approx(1.0)


def test_average_differs_from_total() -> None:
    rows = [
        {"trace_id": "a", "group": "x", "value": 10, "eligible": True},
        {"trace_id": "b", "group": "x", "value": 20, "eligible": True},
        {"trace_id": "c", "group": "y", "value": 90, "eligible": True},
    ]
    totals = SandboxClient.local_reference(rows, code_for("total duration by app"))
    means = SandboxClient.local_reference(rows, code_for("average duration by app"))
    assert {group["key"]: group["value"] for group in totals["groups"]}["x"] == 30
    assert {group["key"]: group["value"] for group in means["groups"]}["x"] == 15


def test_displayed_code_is_the_code_that_ran() -> None:
    source = code_for("Which failure mode costs me the most?")
    result = SandboxClient.local_reference(
        [{"trace_id": "a", "group": "x", "value": 1, "eligible": True}], source
    )
    # "Show the work" renders this same string, so the two must not drift.
    assert "print(json.dumps(result" in source
    assert result["stdout_json"].startswith("{")
    assert "def " not in source or "summarise" in source


def test_no_model_writes_the_aggregation() -> None:
    source = code_for("how many conversations by outcome")
    assert "Written by Raft, never by a model" in source
