"""The HTTP surface a person actually drives.

Every request here is one a visitor makes by typing a question and clicking
through the answer, so a break in this file is a break in the demo.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    directory = tmp_path_factory.mktemp("api")
    import os

    # Settings reads .env, which on a configured machine points at a live
    # gateway. A test suite that changes behaviour depending on who runs it is
    # worthless, so pin every field this fixture depends on.
    os.environ["RAFT_OTARI_MODE"] = "mock"
    os.environ["RAFT_LLM_PROVIDER"] = "otari"
    os.environ["RAFT_EMBEDDING_BACKEND"] = "local"
    for role in ("TRACE_LABELER", "ASPECT_EVALUATOR", "CLUSTER_NAMER", "AUTOPSY_WRITER", "SPAN_EXPLAINER", "PLANNER"):
        os.environ.pop(f"OTARI_{role}_API_KEY", None)
    os.environ["RAFT_DATABASE_PATH"] = str(directory / "raft.db")
    os.environ["RAFT_DEMO_TRACE_COUNT"] = "200"
    os.environ["RAFT_MODEL_ROLES_PATH"] = str(Path(__file__).resolve().parents[1] / "model-roles.yaml")
    from raft import config

    config.get_settings.cache_clear()
    import raft.main

    module = importlib.reload(raft.main)
    with TestClient(module.app) as test_client:
        yield test_client


def ask(client: TestClient, question: str, parent: str | None = None) -> dict:
    plan = client.post("/api/questions/plan", json={"question": question, "parent_run_id": parent}).json()
    run = client.post(
        "/api/analysis-runs", json={"question": question, "plan": plan, "parent_run_id": parent}
    ).json()
    if plan["requires_confirmation"]:
        client.post(f"/api/analysis-runs/{run['id']}/confirm", json={"aspect_question": plan["aspect"]["question"]})
    for _ in range(300):
        current = client.get(f"/api/analysis-runs/{run['id']}").json()
        if current["status"] in {"complete", "failed", "paused_budget"}:
            break
        time.sleep(0.05)
    assert current["status"] == "complete", current.get("error")
    answer = client.get(f"/api/analysis-runs/{run['id']}/answer").json()
    answer["_plan"] = plan
    answer["_run"] = current
    return answer


def test_home_is_populated_without_configuring_anything(client: TestClient) -> None:
    body = client.get("/api/home").json()
    assert body["trace_count"] > 0
    assert body["headline_stats"] and all(stat["value"] >= 0 for stat in body["headline_stats"])
    assert body["top_failures"] and body["top_unmet_requests"]
    assert len(body["example_questions"]) >= 5


@pytest.mark.parametrize(
    "question",
    [
        "What do my users struggle with most?",
        "What are people asking for that my agent cannot do?",
        "How many people are getting product suggestions?",
        "Where do conversations end without the user getting what they wanted?",
        "Which failure mode costs me the most?",
    ],
)
def test_every_example_question_answers(client: TestClient, question: str) -> None:
    answer = ask(client, question)
    assert answer["headline"]
    assert answer["interpretation"]
    assert answer["groups"]
    assert answer["work"]["stdout_json"]


def test_the_five_examples_span_all_three_paths(client: TestClient) -> None:
    paths = {
        client.post("/api/questions/plan", json={"question": question}).json()["path"]
        for question in client.get("/api/home").json()["example_questions"]
    }
    assert paths == {"layer1", "layer2_cluster", "layer3_aspect"}


def test_free_text_nobody_anticipated_still_answers(client: TestClient) -> None:
    for question in (
        "what are people saying about wool coats",
        "how expensive is the research agent per conversation",
        "which tool fails most",
        "do people complain about delivery times",
    ):
        answer = ask(client, question)
        assert answer["denominator"] > 0


def test_plan_exposes_what_the_compiler_understood(client: TestClient) -> None:
    plan = client.post("/api/questions/plan", json={"question": "Which app do users give up on most?"}).json()
    assert plan["group_by_label"] == "App"
    assert any("gave up" in item["label"] for item in plan["filters"])
    assert plan["plan_summary"]
    assert plan["rationale"]


def test_drill_down_returns_exactly_the_counted_traces(client: TestClient) -> None:
    answer = ask(client, "Which app do users give up on most?")
    group = answer["groups"][0]
    listed = client.get(f"/api/traces?trace_ids={','.join(group['trace_ids'])}&page_size=100").json()
    assert listed["total"] == group["count"]


def test_semantic_search_ranks_by_meaning(client: TestClient) -> None:
    body = client.get("/api/traces?semantic=my parcel never turned up&page_size=5").json()
    assert body["ranked_by"] == "semantic"
    assert body["items"]
    scores = [item["relevance"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True)


def test_follow_up_narrows_the_previous_question(client: TestClient) -> None:
    first = ask(client, "Which app do users give up on most?")
    second = ask(client, "now only the checkout agent", parent=first["_run"]["id"])
    assert second["denominator"] <= first["denominator"]
    assert any("checkout-agent" in item["label"] for item in second["_plan"]["filters"])


def test_trace_detail_marks_repeats_and_unknown_timing(client: TestClient) -> None:
    listing = client.get("/api/traces?failure_mode=tool_loop&page_size=1").json()
    if not listing["items"]:
        pytest.skip("no tool loop in this sample")
    detail = client.get(f"/api/traces/{listing['items'][0]['id']}").json()
    tool_calls = [span for span in detail["spans"] if span["type"] == "tool_call"]
    assert any(span["repeat_count"] > 1 for span in tool_calls)
    assert all(span["duration_ms"] is None for span in tool_calls)


def test_span_tools_are_labelled_honestly(client: TestClient) -> None:
    detail = client.get(f"/api/traces/{client.get('/api/traces?page_size=1').json()['items'][0]['id']}").json()
    explained = client.post(f"/api/spans/{detail['spans'][0]['id']}/explain", json={}).json()
    assert "no model was called" in explained["mode"]

    listing = client.get("/api/traces?failure_mode=provider_error&page_size=1").json()
    if listing["items"]:
        errors = client.get(f"/api/traces/{listing['items'][0]['id']}").json()["spans"]
        span = next(span for span in errors if span["error_code"])
        looked_up = client.post(f"/api/spans/{span['id']}/web-search", json={}).json()
        assert "no web search was performed" in looked_up["mode"]


def test_settings_states_what_is_computing_the_answers(client: TestClient) -> None:
    body = client.get("/api/settings/status").json()
    assert body["analysis"]["aggregation"].startswith("local execution")
    assert body["analysis"]["aspect_judge"] == "local:semantic-judge-v1"
    assert body["analysis"]["otari_features_available"] is False
    assert body["dataset"]["provenance"]["generator"]


def test_mcp_server_lists_and_calls_read_only_tools(client: TestClient) -> None:
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"describe_dataset", "get_representative_traces"} <= names
    called = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "describe_dataset", "arguments": {}}},
    ).json()
    assert called["result"]["structuredContent"]["result"]["trace_count"] > 0


def test_short_or_huge_questions_are_rejected(client: TestClient) -> None:
    assert client.post("/api/questions/plan", json={"question": "a"}).status_code == 422
    assert client.post("/api/questions/plan", json={"question": "x" * 500}).status_code == 422


def test_answer_carries_everything_the_page_renders(client: TestClient) -> None:
    answer = ask(client, "What do my users struggle with most?")
    # Each of these is read directly by the Answer page; a missing key renders
    # as a silently empty section rather than an error.
    for key in ("headline", "interpretation", "groups", "evidence", "work", "follow_ups", "metric", "unit"):
        assert key in answer, key
    assert answer["follow_ups"], "follow-up suggestions drive the next question"
    for key in ("sql", "sql_params", "code", "stdout_json", "method_notes", "execution"):
        assert key in answer["work"], key


def test_different_questions_give_different_answers(client: TestClient) -> None:
    """The failure this guards against looked exactly like a mock.

    When a question's filters matched nothing, every filter was dropped and the
    whole dataset was clustered - so unrelated questions returned one identical
    headline. Any two of these must differ in what they selected.
    """
    questions = [
        "What do my users struggle with most?",
        "Which failure mode costs me the most?",
        "Which app do users give up on most?",
        "what do people ask the storefront support about",
        "how much did the developer assistant cost me",
        "what are people frustrated about",
    ]
    answers = [ask(client, question) for question in questions]
    headlines = [answer["headline"] for answer in answers]
    assert len(set(headlines)) == len(headlines), f"repeated headline: {headlines}"

    fingerprints = [
        (answer["denominator"], tuple(sorted(group["key"] for group in answer["groups"])))
        for answer in answers
    ]
    assert len(set(fingerprints)) >= len(questions) - 1


def test_a_question_matching_nothing_says_so(client: TestClient) -> None:
    plan = client.post(
        "/api/questions/plan",
        json={"question": "what did users of the storefront support ask the research agent about"},
    ).json()
    joined = " ".join(plan["rationale"]).lower()
    # It must never quietly answer a different question than the one asked.
    assert "ignored" in joined or "could not honour" in joined or plan["eligible_count"] > 0


def test_live_mode_failures_degrade_instead_of_breaking(client: TestClient, monkeypatch) -> None:
    """A gateway hiccup mid-demo must not end a run.

    Live mode is pointed at an unroutable host so every Otari call fails. The
    answer must still arrive, computed locally, and say that it did.
    """
    import raft.main as module

    manager = module.manager
    monkeypatch.setattr(manager.settings, "raft_otari_mode", "live")
    monkeypatch.setattr(manager.settings, "otari_generation_base_url", "http://127.0.0.1:9")
    monkeypatch.setattr(manager.settings, "otari_platform_base_url", "http://127.0.0.1:9")
    monkeypatch.setenv("OTARI_PLANNER_API_KEY", "test-key")
    monkeypatch.setenv("OTARI_ASPECT_EVALUATOR_API_KEY", "test-key")
    monkeypatch.setenv("OTARI_TRACE_LABELER_API_KEY", "test-key")
    monkeypatch.setenv("OTARI_CLUSTER_NAMER_API_KEY", "test-key")
    monkeypatch.setenv("OTARI_SPAN_EXPLAINER_API_KEY", "test-key")
    try:
        cluster = ask(client, "What do my users struggle with most?")
        assert cluster["groups"] and cluster["denominator"] > 0

        shape = ask(client, "Which failure mode costs me the most?")
        assert shape["work"]["execution"] == "local_reference"

        detail = client.get(f"/api/traces/{shape['groups'][0]['trace_ids'][0]}").json()
        explained = client.post(f"/api/spans/{detail['spans'][0]['id']}/explain", json={}).json()
        assert explained["explanation"]
        assert "unavailable" in explained["mode"] or "locally" in explained["mode"]
    finally:
        monkeypatch.setattr(manager.settings, "raft_otari_mode", "mock")


def test_preflight_reports_configuration_rather_than_failing(client: TestClient) -> None:
    body = client.get("/api/settings/preflight").json()
    assert body["checked"] is False
    assert "not live" in body["reason"]


def test_a_broad_question_is_labelled_and_offers_a_way_forward(client: TestClient) -> None:
    """The behaviour that made the product look broken.

    "What should I fix first?" uses no word these conversations contain, so it
    cannot be narrowed. Returning the dataset-wide grouping is correct; passing
    it off as a precise answer is not. It must be flagged, and it must offer
    questions built from the data's own words.
    """
    answer = ask(client, "What should I fix first?")
    assert answer["is_overview"] is True
    assert answer["headline"].startswith("Overview of")
    assert answer["suggestions"], "a dead end must offer a way forward"

    # And those suggestions must actually work when clicked.
    followed = ask(client, answer["suggestions"][0])
    assert followed["is_overview"] is False
    assert followed["denominator"] < answer["denominator"]


def test_a_specific_question_is_not_flagged_as_an_overview(client: TestClient) -> None:
    answer = ask(client, "Which app do users give up on most?")
    assert answer["is_overview"] is False
