"""End-to-end behaviour of the three answering paths.

These assert the product's actual promises: numbers come from executed code,
quotes are literal substrings of stored spans, drill-down lands on exactly the
traces that were counted, and different questions produce different answers.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from tests.conftest import answer_for, make_manager


@pytest.mark.asyncio
async def test_shape_question_groups_and_sums_from_sql(manager) -> None:
    answer = await answer_for(manager, "Which failure mode costs me the most?")
    assert answer["path"] == "layer1"
    assert answer["metric"] == "cost"
    assert answer["unit"] == "usd"

    # The stated total must equal the sum of the same rows read straight from SQL.
    expected = manager.db.fetch_one(
        "SELECT ROUND(SUM(cost_usd), 6) AS total FROM traces WHERE failure_mode != 'none'"
    )
    computed = json.loads(answer["work"]["stdout_json"])
    assert computed["value_sum"] == pytest.approx(float(expected["total"]), abs=1e-4)
    assert sum(group["count"] for group in answer["groups"]) == answer["denominator"]


@pytest.mark.asyncio
async def test_content_question_clusters_and_names_from_member_wording(manager) -> None:
    answer = await answer_for(manager, "What do my users struggle with most?")
    assert answer["path"] == "layer2_cluster"
    assert len(answer["groups"]) >= 3
    # Names are derived, never copied from a seeded topic column.
    topics = {row["topic"] for row in manager.db.fetch_all("SELECT DISTINCT topic FROM traces")}
    assert not {group["label"] for group in answer["groups"]} & topics
    assert all(group["exemplar"] for group in answer["groups"] if group["key"] != "__other__")


@pytest.mark.asyncio
async def test_unanticipated_question_becomes_a_cached_aspect(manager) -> None:
    answer = await answer_for(manager, "How many people are getting product suggestions?")
    assert answer["path"] == "layer3_aspect"
    assert answer["aspect_id"]
    assert {group["key"] for group in answer["groups"]} <= {"yes", "no"}
    assert sum(group["count"] for group in answer["groups"]) == answer["denominator"]

    verification = manager.verification(answer["aspect_id"])
    assert verification["yes"] or verification["no"]
    assert verification["judge"]

    # The values are per-trace judgments, not a lookup of one prebuilt column.
    values = manager.db.fetch_all(
        "SELECT value_json, score FROM aspect_values WHERE aspect_id=?", (answer["aspect_id"],)
    )
    assert len(values) == manager.db.trace_count()
    assert len({row["score"] for row in values}) > 10


@pytest.mark.asyncio
async def test_two_different_aspects_produce_different_answers(manager) -> None:
    visas = await answer_for(manager, "How many conversations mention visas?")
    refunds = await answer_for(manager, "How many conversations mention a refund?")
    assert visas["aspect_id"] != refunds["aspect_id"]
    visa_yes = {
        group["trace_ids"][0] for group in visas["groups"] if group["key"] == "yes" and group["trace_ids"]
    }
    refund_yes = {
        group["trace_ids"][0] for group in refunds["groups"] if group["key"] == "yes" and group["trace_ids"]
    }
    assert visa_yes != refund_yes


@pytest.mark.asyncio
async def test_every_quote_is_a_literal_substring_of_its_trace(manager) -> None:
    for question in (
        "Which failure mode costs me the most?",
        "What do my users struggle with most?",
    ):
        answer = await answer_for(manager, question)
        assert answer["evidence"]
        for item in answer["evidence"]:
            spans = manager.db.fetch_all(
                "SELECT content_redacted FROM spans WHERE trace_id=?", (item["trace_id"],)
            )
            haystack = "\n".join(span["content_redacted"] for span in spans)
            assert item["quote"] in haystack, f"{item['quote']!r} is not in {item['trace_id']}"


@pytest.mark.asyncio
async def test_drill_down_ids_are_exactly_the_counted_traces(manager) -> None:
    answer = await answer_for(manager, "Which app do users give up on most?")
    for group in answer["groups"]:
        if group["key"] == "__other__":
            continue
        assert len(group["trace_ids"]) == group["count"]
        rows = manager.db.fetch_all(
            f"SELECT app, user_gave_up FROM traces WHERE id IN ({','.join('?' * len(group['trace_ids']))})",
            tuple(group["trace_ids"]),
        )
        assert {row["app"] for row in rows} == {group["key"]}
        assert all(row["user_gave_up"] == 1 for row in rows)


@pytest.mark.asyncio
async def test_filters_actually_restrict_the_denominator(manager) -> None:
    everything = await answer_for(manager, "How many conversations are there by app?")
    gave_up = await answer_for(manager, "Which app do users give up on most?")
    assert gave_up["denominator"] < everything["denominator"]
    expected = manager.db.fetch_one("SELECT COUNT(*) AS count FROM traces WHERE user_gave_up = 1")
    assert gave_up["denominator"] == int(expected["count"])


@pytest.mark.asyncio
async def test_follow_up_inherits_the_previous_scope(manager) -> None:
    first = await answer_for(manager, "Which app do users give up on most?")
    plan = await manager.plan_question("now only the checkout agent", first["_run"]["id"])
    labels = {item.label for item in plan.filters}
    assert any("gave up" in label for label in labels)
    assert any("checkout-agent" in label for label in labels)


@pytest.mark.asyncio
async def test_impossible_filter_widens_instead_of_answering_nothing(manager) -> None:
    plan = await manager.plan_question(
        "what did users of the storefront support ask about while the developer assistant was refusing them"
    )
    assert plan.eligible_count > 0


@pytest.mark.asyncio
async def test_aspect_is_reused_rather_than_re_evaluated(manager) -> None:
    question = "How many conversations mention a discount code?"
    first = await answer_for(manager, question)
    before = manager.db.fetch_all(
        "SELECT trace_id, value_json FROM aspect_values WHERE aspect_id=?", (first["aspect_id"],)
    )
    second = await answer_for(manager, question)
    assert second["aspect_id"] == first["aspect_id"]
    after = manager.db.fetch_all(
        "SELECT trace_id, value_json FROM aspect_values WHERE aspect_id=?", (first["aspect_id"],)
    )
    assert before == after


@pytest.mark.asyncio
async def test_editing_an_aspect_creates_a_new_version(manager) -> None:
    plan = await manager.plan_question("How many conversations mention a wool coat?")
    run = manager.create_run(plan)
    original = manager.db.get_run(run.id)["aspect_id"]
    manager.confirm_run(run.id, "Did the user mention a specific garment they already own?")
    await manager.tasks[run.id]
    updated = manager.db.get_run(run.id)["aspect_id"]
    assert updated != original
    versions = manager.db.fetch_all("SELECT version FROM aspects WHERE id IN (?,?)", (original, updated))
    assert sorted(row["version"] for row in versions) == [1, 2]


@pytest.mark.asyncio
async def test_span_explanation_is_derived_from_the_trace(manager) -> None:
    loop = manager.db.fetch_one("SELECT id FROM traces WHERE repeated_identical_calls >= 3 LIMIT 1")
    assert loop, "the corpus should contain tool loops"
    spans = manager.db.fetch_all(
        "SELECT id, type FROM spans WHERE trace_id=? AND type='tool_call' ORDER BY idx", (loop["id"],)
    )
    result = await manager.explain_span(spans[0]["id"])
    assert "no model was called" in result["mode"]
    assert "times" in result["explanation"]  # it counted the repeats itself


@pytest.mark.asyncio
async def test_budget_pause_preserves_rows_and_resumes(tmp_path: Path) -> None:
    tight = make_manager(tmp_path, count=120, allowance=0.0)
    tight.settings.raft_otari_mode = "live"  # force the metered path
    plan = await tight.plan_question("How many conversations mention a refund?")
    run = tight.create_run(plan)
    tight.confirm_run(run.id, plan.aspect.question if plan.aspect else None)
    await tight.tasks[run.id]
    paused = tight.db.get_run(run.id)
    assert paused["status"] == "paused_budget"
    assert paused["completed"] == 0

    tight.settings.raft_otari_mode = "mock"
    tight.resume_run(run.id)
    await tight.tasks[run.id]
    finished = tight.db.get_run(run.id)
    assert finished["status"] == "complete"
    answer = tight.db.get_answer_for_run(run.id)
    assert answer["denominator"] == tight.db.trace_count()


@pytest.mark.asyncio
async def test_clusters_are_coherent_not_grab_bags(manager) -> None:
    """A group must be about one thing.

    The corpus records which intent produced each conversation, so purity is
    checkable. Flat k-means over these vectors scores about 0.35 - groups held
    together by a shared common word, then named after it - which is exactly
    what makes an answer read as generated rather than observed.
    """
    answer = await answer_for(manager, "What do my users struggle with most?")
    real = [group for group in answer["groups"] if group["key"] != "__other__"]
    assert len(real) >= 4

    weighted = 0
    counted = 0
    for group in real[:6]:
        rows = manager.db.fetch_all(
            f"SELECT intent_key FROM traces WHERE id IN ({','.join('?' * len(group['trace_ids']))})",
            tuple(group["trace_ids"]),
        )
        dominant = Counter(row["intent_key"] for row in rows).most_common(1)[0][1]
        weighted += dominant
        counted += len(rows)
    purity = weighted / counted
    assert purity >= 0.6, f"top groups are only {purity:.0%} pure; they are grab-bags"


@pytest.mark.asyncio
async def test_each_group_reports_what_happened_to_it(manager) -> None:
    # A count with no outcome attached is a statistic, not an insight, and it is
    # what made the answer page look like a mock.
    answer = await answer_for(manager, "What do my users struggle with most?")
    for group in answer["groups"]:
        if group["key"] == "__other__":
            continue
        profile = group["profile"]
        assert profile is not None
        assert profile["top_failure"] or profile["top_outcome"]
        assert profile["apps"]
        assert profile["cost_usd"] >= 0
        assert group["quotes"], "a group with no quotes cannot be checked by the reader"
        assert len(set(group["quotes"])) == len(group["quotes"]), "repeated quotes read as filler"


@pytest.mark.asyncio
async def test_group_labels_are_real_sentences_from_the_data(manager) -> None:
    answer = await answer_for(manager, "What do my users struggle with most?")
    for group in answer["groups"]:
        if group["key"] == "__other__":
            continue
        # The label must appear verbatim in one of the group's own traces.
        rows = manager.db.fetch_all(
            f"SELECT user_request FROM traces WHERE id IN ({','.join('?' * len(group['trace_ids']))})",
            tuple(group["trace_ids"]),
        )
        stem = group["label"].rstrip("…")
        assert any(stem in row["user_request"] for row in rows), group["label"]


@pytest.mark.asyncio
async def test_a_shape_question_that_ignores_your_words_is_flagged(manager) -> None:
    """The exact defect behind the failure-mode answer.

    "which language do my clients speak?" extracted `language`, `clients` and
    `speak`, dropped all three, grouped by failure mode and presented that as
    the answer. The overview guard existed only on the clustering path.
    """
    plan = await manager.plan_question("which language do my clients speak?")
    assert plan.spec["broad"] is True
    assert not any(line.startswith("Every part of this question") for line in plan.rationale)

    answer = await answer_for(manager, "which language do my clients speak?")
    assert answer["is_overview"] is True
    assert answer["suggestions"], "an overview must offer a way forward"
    # And it must not narrow to a handful of incidental matches.
    assert answer["denominator"] == manager.db.trace_count()


@pytest.mark.asyncio
async def test_a_question_that_does_name_a_subject_is_not_flagged(manager) -> None:
    answer = await answer_for(manager, "what do people ask the storefront support about")
    assert answer["is_overview"] is False
    assert answer["denominator"] < manager.db.trace_count()
