"""What the no-credentials aspect judge can and cannot do, measured.

The corpus records which intent produced each conversation, so an aspect
question aimed at one intent has a checkable answer. These bounds are
deliberately loose enough to survive corpus regeneration and tight enough to
fail if the judge degenerates into matching everything or nothing.

`python scripts/eval_judge.py` prints the same measurement per question.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from raft.judge import SemanticAspectJudge
from tests.conftest import answer_for


# Floors are set below the scores observed on this fixture's 220-trace corpus,
# which is smaller and noisier than the 847-trace demo `scripts/eval_judge.py`
# measures. They exist to catch collapse, not to pin an exact number.
CASES: list[tuple[str, set[str], float]] = [
    # question, intents that make it true, minimum acceptable F1
    ("Did the user ask about a visa or entry requirement?", {"visa-requirements"}, 0.6),
    ("Did the user ask the agent to deploy or open a pull request?", {"deploy-or-open-pr"}, 0.5),
    (
        "Did the user ask to cancel an order or subscription?",
        {"cancel-just-placed", "cancel-subscription"},
        0.6,
    ),
    ("Did the user ask how a garment fits?", {"sizing-and-fit"}, 0.6),
    ("Did the user ask about single sign-on?", {"configure-sso"}, 0.35),
]


def score(manager, aspect_id: str, truth: set[str]) -> tuple[float, float, float]:
    rows = manager.db.fetch_all(
        "SELECT av.value_json, t.intent_key FROM aspect_values av JOIN traces t ON t.id = av.trace_id "
        "WHERE av.aspect_id = ?",
        (aspect_id,),
    )
    assert rows, "the aspect was never evaluated"
    true_positive = false_positive = false_negative = 0
    for row in rows:
        predicted = json.loads(row["value_json"]) is True
        actual = row["intent_key"] in truth
        true_positive += predicted and actual
        false_positive += predicted and not actual
        false_negative += (not predicted) and actual
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "truth", "floor"), CASES)
async def test_judge_beats_its_measured_floor(manager, question: str, truth: set[str], floor: float) -> None:
    answer = await answer_for(manager, question)
    precision, recall, f1 = score(manager, answer["aspect_id"], truth)
    assert f1 >= floor, f"{question!r}: P={precision:.2f} R={recall:.2f} F1={f1:.2f}"


@pytest.mark.asyncio
async def test_judge_does_not_answer_yes_to_everything(manager) -> None:
    answer = await answer_for(manager, "Did the user mention a nuclear reactor coolant schedule?")
    yes = next((group for group in answer["groups"] if group["key"] == "yes"), None)
    # Nothing in this corpus is about that, so a healthy judge stays quiet.
    assert yes is None or yes["share"] < 0.2


def test_threshold_returns_nothing_when_there_is_no_signal(manager) -> None:
    judge = SemanticAspectJudge(manager.index.index)
    cut, separation, method = judge.threshold(np.zeros(200, dtype=np.float32))
    assert cut == float("inf")
    assert separation == 0.0
    assert method == "no_signal"


def test_relevance_feedback_needs_a_quorum_before_expanding(manager) -> None:
    judge = SemanticAspectJudge(manager.index.index)
    _, _, expanded = judge.analyse("Did the user ask about a refund?")
    # One lone strong hit must not be allowed to redefine the question.
    scores = np.zeros(200, dtype=np.float32)
    scores[0] = 1.0
    merged, added, _ = judge.relevance_feedback(scores, [""] * 200, expanded)
    assert not added
    assert merged == expanded


def test_scope_follows_the_subject_of_the_question(manager) -> None:
    judge = SemanticAspectJudge(manager.index.index)
    assert judge.analyse("Did the assistant give the user a product suggestion?")[0].scope == "assistant"
    assert judge.analyse("Did the user ask about shipping?")[0].scope == "user"
    assert judge.analyse("Was a refund mentioned?")[0].scope == "conversation"


def test_negated_questions_invert(manager) -> None:
    judge = SemanticAspectJudge(manager.index.index)
    assert judge.analyse("Did the assistant not answer the question?")[0].negated
    assert not judge.analyse("Did the assistant answer the question?")[0].negated
