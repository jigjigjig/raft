"""What the no-credentials aspect judge can and cannot do, measured.

The corpus records which intent produced each conversation, so an aspect
question aimed at one intent has a checkable answer. These bounds are
deliberately loose enough to survive corpus regeneration and tight enough to
fail if the judge degenerates into matching everything or nothing.

`python scripts/eval_judge.py` prints the same measurement per question.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from raft.judge import SemanticAspectJudge
from tests.conftest import answer_for


ROOT = Path(__file__).resolve().parents[1]


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


def test_the_gateways_own_prefix_is_not_a_reroute() -> None:
    """Raft asks for `mzai:X`; the gateway answers `X`. That is the same model.

    Comparing them raw recorded a Routing fallback on every single call and put
    "Routing fallback: mzai:Qwen/... → Qwen/..." on screen mid-run.
    """
    from raft.otari import route_key, same_model

    assert same_model("Qwen/Qwen3-30B-A3B-Instruct-2507", "mzai:Qwen/Qwen3-30B-A3B-Instruct-2507")
    assert same_model("mzai:openai/gpt-oss-120b", "mzai:openai/gpt-oss-120b")
    assert same_model(None, None)
    # A real reroute still reads as one.
    assert not same_model("mzai:Qwen/Qwen3-30B-A3B-Instruct-2507", "mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
    assert not same_model("NousResearch/Hermes-4-405B", "mzai:meta-llama/Llama-3.3-70B-Instruct")
    assert route_key("mzai:google/gemma-3-27b-it") == "google/gemma-3-27b-it"


@pytest.mark.asyncio
async def test_a_model_missing_from_the_catalog_walks_to_the_roles_fallback(tmp_path) -> None:
    """A 70B vanishing from the catalog killed two roles for a whole evening.

    "The model X does not exist" is the wrong candidate, not a failed request,
    so the role's declared fallbacks are tried before the call is lost.
    """
    import httpx

    from raft.config import Settings
    from raft.db import Database
    from raft.otari import OtariClient

    settings = Settings(
        raft_otari_mode="live",
        raft_database_path=tmp_path / "raft.db",
        raft_model_roles_path=ROOT / "model-roles.yaml",
    )
    database = Database(settings.raft_database_path)
    database.initialize()
    client = OtariClient(settings, database)
    role = client.role("cluster_namer")
    asked: list[str] = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            asked.append(model)
            if model == role.primary:
                return httpx.Response(
                    404, json={"detail": f"The model `{model}` does not exist."}
                )
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "model": model,
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                },
            )

    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = Transport()
        return original(*args, **kwargs)

    # Deliberately not shaped like a real workspace key: anything carrying the
    # live key prefix would trip the repo's own secret scan for the rest of time.
    os.environ[role.api_key_env] = "dummy-key-for-this-test-only"
    try:
        httpx.AsyncClient = patched  # type: ignore[misc]
        result, _ = await client.complete("cluster_namer", [{"role": "user", "content": "hi"}])
    finally:
        httpx.AsyncClient = original  # type: ignore[misc]
        os.environ.pop(role.api_key_env, None)

    assert asked == [role.primary, role.fallbacks[0]], asked
    assert result.model == role.fallbacks[0]
    evidence = database.fetch_one("SELECT status, notes FROM feature_evidence WHERE feature='Routing'")
    assert evidence and "raft_side_fallback" in evidence["status"]
    assert "does not exist on this deployment" in evidence["notes"]
