"""The compiler has to read questions, not match three keywords.

Each case here is a different phrasing of a different kind of question. If the
compiler regresses to routing everything down one path, these fail.
"""

from __future__ import annotations

import pytest

from raft.query import DatasetCatalog, QuestionCompiler, aggregation_code, format_metric
from raft.otari import SandboxClient


CATALOG = DatasetCatalog(
    {
        "app": ["storefront-support", "checkout-agent", "developer-assistant", "billing-bot"],
        "tool": ["lookup_order", "process_refund", "cancel_subscription", "search_docs"],
        "model": ["mzai:Qwen/Qwen3-32B", "mzai:openai/gpt-oss-120b"],
        "error_code": ["provider_rate_limit_429"],
    }
)


@pytest.fixture(scope="module")
def compiler() -> QuestionCompiler:
    return QuestionCompiler(CATALOG)


@pytest.mark.parametrize(
    ("question", "path"),
    [
        # Shape questions: every concept maps to a recorded column.
        ("Which failure mode costs me the most?", "layer1"),
        ("Which app do users give up on most?", "layer1"),
        ("what breaks most often", "layer1"),
        ("how much did the billing bot cost me", "layer1"),
        ("which model is slowest", "layer1"),
        ("show me spend by app over the last 7 days", "layer1"),
        ("Where do conversations end without the user getting what they wanted?", "layer1"),
        ("Which tools are called the most?", "layer1"),
        # Content questions: the answer is in what people wrote.
        ("What do my users struggle with most?", "layer2_cluster"),
        ("What are people asking for that my agent cannot do?", "layer2_cluster"),
        ("What are the most common topics in the checkout agent?", "layer2_cluster"),
        ("what are people frustrated about", "layer2_cluster"),
        # Judgment questions: a number about something no column records.
        ("How many people are getting product suggestions?", "layer3_aspect"),
        ("How many conversations mention shipping delays?", "layer3_aspect"),
        ("Do users ever ask about visas?", "layer3_aspect"),
        ("What share of conversations mention a competitor?", "layer3_aspect"),
    ],
)
def test_routes_by_meaning(compiler: QuestionCompiler, question: str, path: str) -> None:
    assert compiler.compile(question).path == path


def test_extracts_metric_grouping_and_filter(compiler: QuestionCompiler) -> None:
    spec = compiler.compile("Which failure mode costs me the most?")
    assert spec.metric == "cost"
    assert spec.aggregate == "sum"
    assert spec.group_by == "failure_mode"
    assert any("went wrong" in item.label for item in spec.predicates)


def test_recognises_app_names_from_the_dataset(compiler: QuestionCompiler) -> None:
    spec = compiler.compile("how much did the billing bot cost me")
    assert [item.params for item in spec.predicates] == [("billing-bot",)]


def test_duration_defaults_to_average_not_total(compiler: QuestionCompiler) -> None:
    # Ranking models by total latency would just rank them by traffic volume.
    assert compiler.compile("which model is slowest").aggregate == "avg"
    assert compiler.compile("what is the total duration").aggregate == "sum"


def test_time_window_becomes_a_bound_parameter(compiler: QuestionCompiler) -> None:
    spec = compiler.compile("spend in the last 3 days")
    started = [item for item in spec.predicates if item.field == "started_at"]
    assert started and started[0].sql == "t.started_at >= ?"
    assert len(started[0].params) == 1


def test_aspect_question_is_rewritten_per_trace(compiler: QuestionCompiler) -> None:
    spec = compiler.compile("How many people are getting product suggestions?")
    assert spec.aspect_question
    assert spec.aspect_question.lower().startswith("did ")
    assert "how many" not in spec.aspect_question.lower()


def test_aspect_drops_tool_filters(compiler: QuestionCompiler) -> None:
    # The tool name is a guess about handling; the aspect reads the content.
    spec = compiler.compile("How many users asked to cancel a subscription?")
    assert spec.path == "layer3_aspect"
    assert not [item for item in spec.predicates if item.field == "tool"]


def test_unknown_wording_still_compiles(compiler: QuestionCompiler) -> None:
    spec = compiler.compile("what do people say about the marmalade subscription box")
    assert spec.path in {"layer2_cluster", "layer3_aspect"}
    assert spec.focus_terms  # the unrecognised words survive as semantic focus


def test_sql_is_parameterised(compiler: QuestionCompiler) -> None:
    spec = compiler.compile("how much did the billing bot cost me in the last 7 days")
    where, params = spec.where()
    assert "billing-bot" not in where
    assert len(params) == 2


def test_generated_aggregation_matches_hand_computed_values(compiler: QuestionCompiler) -> None:
    spec = compiler.compile("Which failure mode costs me the most?")
    rows = [
        {"trace_id": "a", "group": "tool_loop", "value": 2.0, "eligible": True},
        {"trace_id": "b", "group": "tool_loop", "value": 1.0, "eligible": True},
        {"trace_id": "c", "group": "refusal", "value": 4.0, "eligible": True},
        {"trace_id": "d", "group": "refusal", "value": 0.0, "eligible": False},
    ]
    result = SandboxClient.local_reference(rows, aggregation_code(spec))
    assert result["denominator"] == 3
    assert result["value_sum"] == 7.0
    assert result["groups"][0] == {"key": "refusal", "count": 1, "share": pytest.approx(1 / 3), "value": 4.0}
    assert sum(group["count"] for group in result["groups"]) == result["denominator"]


def test_currency_precision_is_shared_across_one_answer() -> None:
    assert format_metric(0.0026, "usd", reference=0.04) == "$0.0026"
    assert format_metric(0.04, "usd", reference=0.04) == "$0.0400"
    assert format_metric(12.5, "usd", reference=12.5) == "$12.50"


def test_app_name_containing_a_lead_word_is_not_a_grouping(compiler: QuestionCompiler) -> None:
    # "developer assistant" contains the substring "per assistant"; without word
    # boundaries that reads as "per <dimension>" and turns a content question
    # into a one-group breakdown.
    spec = compiler.compile("What do people ask the developer assistant about?")
    assert spec.path == "layer2_cluster"
    assert spec.group_by is None


def test_does_not_group_by_a_field_the_question_already_pins(compiler: QuestionCompiler) -> None:
    spec = compiler.compile("how much did the billing bot cost me in the last 7 days")
    assert any(item.field == "app" for item in spec.predicates)
    # Grouping by app here would render one group at 100%.
    assert spec.group_by != "app"
