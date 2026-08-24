"""The model planner translates wording; Raft still owns the query.

Nobody writes "I am complaining" in a support chat, so matching a question's
words against the traces finds nothing and every vaguely worded question
collapses onto the same dataset-wide answer. A model bridges that. These tests
run the planner path with a stubbed model, so they check the contract - what
Raft accepts, what it refuses, and that the plan actually changes the answer -
without needing a workspace key.
"""

from __future__ import annotations

import pytest

from raft.analysis import PlannerResult
from raft.otari import OtariError
from raft.query import filter_catalog, predicate_by_id
from raft.schemas import CompletionResult
from tests.conftest import answer_for


def stub_planner(manager, result: PlannerResult):
    """Replace the model call with a fixed decision."""

    async def complete(role, messages, **kwargs):
        if role != "planner":
            # Every other role behaves as if the gateway were down, so these
            # tests also prove the rest of the run does not depend on it.
            raise OtariError(f"no stub for role {role}")
        complete.messages = messages
        return CompletionResult(content="{}", request_id="req_stub"), result

    complete.messages = None
    manager.otari.complete = complete  # type: ignore[assignment]
    manager.settings.raft_otari_mode = "live"
    return complete


@pytest.fixture
def live(manager):
    original = manager.otari.complete
    yield manager
    manager.otari.complete = original
    manager.settings.raft_otari_mode = "mock"


@pytest.mark.asyncio
async def test_planner_turns_wording_into_recorded_filters(live) -> None:
    # "Complain" appears nowhere in the conversations; the model maps it.
    stub_planner(
        live,
        PlannerResult(
            path="layer2_cluster",
            explanation="complaints are conversations that ended badly",
            filter_ids=["the_user_ended_frustrated_or_angry"],
            focus="",
        ),
    )
    plan = await live.plan_question("What do customers complain about?")
    assert [item.label for item in plan.filters] == ["the user ended frustrated or angry"]
    assert plan.eligible_count < plan.total_count
    assert any("planner read this as" in line for line in plan.rationale)


@pytest.mark.asyncio
async def test_planner_is_given_a_menu_and_the_real_dataset(live) -> None:
    stub = stub_planner(live, PlannerResult(path="layer1", explanation="shape"))
    await live.plan_question("what is going on")
    payload = stub.messages[1]["content"]
    assert "available_filters" in payload
    assert "the_user_gave_up" in payload
    # It must see what is actually in the store, not a generic description.
    assert "storefront-support" in payload
    assert "example_requests" in payload
    # And it must never be handed SQL to edit.
    assert "SELECT" not in payload and "t.user_gave_up" not in payload


@pytest.mark.asyncio
async def test_an_invented_filter_id_is_refused(live) -> None:
    stub_planner(
        live,
        PlannerResult(
            path="layer1",
            explanation="made up",
            filter_ids=["drop table traces", "the_user_gave_up", "no_such_filter"],
        ),
    )
    plan = await live.plan_question("who is unhappy")
    labels = [item.label for item in plan.filters]
    assert labels == ["the user gave up"]  # only the real one survived
    assert any("unrecognised filter" in line for line in plan.rationale)


@pytest.mark.asyncio
async def test_planner_focus_narrows_to_the_subject(live) -> None:
    stub_planner(
        live,
        PlannerResult(
            path="layer2_cluster",
            explanation="about delivery",
            filter_ids=[],
            # The corpus's words, not the asker's.
            focus="shipped parcel arrive tracking address delivered",
        ),
    )
    focused = await answer_for(live, "How is our delivery experience?")
    assert focused["denominator"] < live.db.trace_count()
    assert any("Searching the conversations" in line for line in focused["_plan"].rationale)


@pytest.mark.asyncio
async def test_different_readings_produce_different_answers(live) -> None:
    """The point of the whole exercise."""
    stub_planner(
        live,
        PlannerResult(path="layer2_cluster", explanation="a", filter_ids=["the_user_gave_up"]),
    )
    abandoned = await answer_for(live, "Where are we losing people?")

    stub_planner(
        live,
        PlannerResult(path="layer2_cluster", explanation="b", filter_ids=["the_agent_looped_on_one_tool"]),
    )
    looping = await answer_for(live, "What should I fix first?")

    assert abandoned["denominator"] != looping["denominator"]
    assert abandoned["headline"] != looping["headline"]


@pytest.mark.asyncio
async def test_planner_failure_falls_back_to_the_compiled_plan(live) -> None:
    async def failing(role, messages, **kwargs):
        raise OtariError("gateway unreachable")

    live.otari.complete = failing  # type: ignore[assignment]
    live.settings.raft_otari_mode = "live"
    plan = await live.plan_question("Which app do users give up on most?")
    assert plan.path == "layer1"
    assert any("gave up" in item.label for item in plan.filters)
    assert any("planner was unavailable" in line for line in plan.rationale)


def test_every_menu_entry_resolves_to_a_real_predicate(manager) -> None:
    catalog = manager.index.catalog
    menu = filter_catalog(catalog)
    assert len(menu) > 15
    for entry in menu:
        predicate = predicate_by_id(catalog, entry["id"])
        assert predicate is not None, entry
        assert predicate.sql


@pytest.mark.asyncio
async def test_category_aspect_groups_by_its_own_labels(live) -> None:
    """The question that exposed all of this: "which language do my clients speak?"

    `category` was always a legal aspect type, but grouping was hardcoded to
    yes/no, so it could never be produced - the question fell through to a
    shape query and came back as a breakdown by failure mode.
    """
    import itertools

    from raft.analysis import CategoryJudgment

    labels = ["English", "Spanish", "German"]
    cycle = itertools.cycle(labels)

    async def complete(role, messages, **kwargs):
        if role == "planner":
            return CompletionResult(content="{}", request_id="req_plan"), PlannerResult(
                path="layer3_aspect",
                explanation="language is a property of each conversation",
                aspect_question="What language is this conversation written in?",
                aspect_type="category",
                aspect_labels=labels,
            )
        if role == "aspect_evaluator":
            return (
                CompletionResult(content="{}", request_id="req_eval", usage={"cost": 0.0}),
                CategoryJudgment(label=next(cycle), confidence=0.9, evidence_quote=""),
            )
        raise OtariError(f"no stub for role {role}")

    live.otari.complete = complete  # type: ignore[assignment]
    live.settings.raft_otari_mode = "live"

    answer = await answer_for(live, "which language do my clients speak?")
    assert answer["path"] == "layer3_aspect"
    keys = {group["key"] for group in answer["groups"]}
    assert keys <= set(labels) and len(keys) > 1, keys
    assert sum(group["count"] for group in answer["groups"]) == answer["denominator"]
    assert answer["is_overview"] is False


@pytest.mark.asyncio
async def test_a_label_outside_the_agreed_set_is_folded_into_other(live) -> None:
    from raft.analysis import CategoryJudgment

    async def complete(role, messages, **kwargs):
        if role == "planner":
            return CompletionResult(content="{}"), PlannerResult(
                path="layer3_aspect",
                explanation="x",
                aspect_question="What language is this?",
                aspect_type="category",
                aspect_labels=["English", "Spanish"],
            )
        if role == "aspect_evaluator":
            # A free-text answer would otherwise create a group of one.
            return CompletionResult(content="{}"), CategoryJudgment(label="Klingon", confidence=0.5)
        raise OtariError("no stub")

    live.otari.complete = complete  # type: ignore[assignment]
    live.settings.raft_otari_mode = "live"
    answer = await answer_for(live, "which language do my clients speak?")
    assert {group["key"] for group in answer["groups"]} == {"other"}


@pytest.mark.asyncio
async def test_a_category_without_an_answer_set_falls_back_to_boolean(live) -> None:
    stub_planner(
        live,
        PlannerResult(
            path="layer3_aspect",
            explanation="x",
            aspect_question="What language is this?",
            aspect_type="category",
            aspect_labels=["English"],  # one label cannot group anything
        ),
    )
    plan = await live.plan_question("which language do my clients speak?")
    assert plan.aspect is not None and plan.aspect.type == "boolean"
    assert any("no answer set" in line for line in plan.rationale)


@pytest.mark.asyncio
async def test_planner_can_declare_a_question_unanswerable(live) -> None:
    stub_planner(
        live,
        PlannerResult(
            path="layer2_cluster",
            explanation="x",
            unanswerable=True,
            reason="these conversations record no pricing data",
        ),
    )
    plan = await live.plan_question("what is our gross margin?")
    assert any("cannot answer" in line for line in plan.rationale)
    assert plan.spec["broad"] is True


@pytest.mark.asyncio
async def test_a_gateway_failure_never_mixes_category_answers_with_yes_no(live) -> None:
    """Observed live: the gateway 502'd at conversation 770 of 847.

    The local judge only decides yes/no, so finishing a category run with it
    produced groups reading `English 768, No 76, Yes 1` - three of those are the
    same question answered on two different scales, and the total is meaningless.
    A partial answer over the judged set is correct; a blended one is not.
    """
    import itertools

    from raft.analysis import CategoryJudgment
    from raft.otari import OtariError

    calls = itertools.count()

    async def complete(role, messages, **kwargs):
        if role == "planner":
            return CompletionResult(content="{}"), PlannerResult(
                path="layer3_aspect",
                explanation="language",
                # A distinct question: aspects are cached by wording, so reusing
                # one from an earlier test would replay its stored values.
                aspect_question="Which written language does this conversation use?",
                aspect_type="category",
                aspect_labels=["English", "Spanish"],
            )
        if role == "aspect_evaluator":
            if next(calls) > 40:
                raise OtariError("Authorization service unavailable", status_code=502)
            return CompletionResult(content="{}"), CategoryJudgment(label="English", confidence=0.9)
        raise OtariError("no stub")

    live.otari.complete = complete  # type: ignore[assignment]
    live.settings.raft_otari_mode = "live"

    answer = await answer_for(live, "which language do my clients speak?")
    keys = {group["key"] for group in answer["groups"]}
    assert not (keys & {"yes", "no"}), f"boolean verdicts leaked into a category answer: {keys}"
    assert keys <= {"English", "Spanish", "other"}
    # The unjudged conversations are declared, not guessed.
    assert answer["denominator"] < live.db.trace_count()
    assert answer["excluded_count"] > 0
    assert any("excluded rather than guessed" in note for note in answer["work"]["method_notes"])
