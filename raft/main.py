from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from raft.analysis import AnalysisManager
from raft.config import Settings, get_settings
from raft.db import Database
from raft.demo import build_dataset, dataset_is_current, reset_dataset, seed_demo
from raft.mcp import ReadOnlyTraceTools, TOOL_DEFINITIONS, mcp_response
from raft.otari import check_models
from raft.query import DIMENSIONS, METRICS
from raft.schemas import ConfirmRunRequest, CreateRunRequest, QuestionPlan


settings: Settings = get_settings()
db = Database(settings.raft_database_path)
manager = AnalysisManager(settings, db)
mcp_tools = ReadOnlyTraceTools(db)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.initialize()
    outcome = await asyncio.to_thread(
        seed_demo, db, settings.raft_demo_trace_count, settings.raft_demo_seed
    )
    print(f"[raft] demo dataset {outcome}; {db.trace_count()} traces", flush=True)
    await asyncio.to_thread(manager.index.ensure)
    yield
    pending = [task for task in manager.tasks.values() if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(title="Raft", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def restrict_public_mcp_hostname(request: Request, call_next):
    public_host = urlparse(settings.raft_public_mcp_url).netloc.casefold()
    request_host = request.headers.get("host", "").casefold()
    if public_host and request_host == public_host and request.url.path not in {"/mcp", "/api/health"}:
        return JSONResponse({"detail": "This hostname exposes only Raft's read-only MCP endpoint."}, status_code=404)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _trace_detail(trace_id: str) -> dict:
    trace = db.fetch_one("SELECT * FROM traces WHERE id=?", (trace_id,))
    if not trace:
        raise HTTPException(404, "Trace not found")
    trace.pop("embedding", None)
    trace["user_gave_up"] = bool(trace["user_gave_up"])
    trace["distinct_tools"] = json.loads(trace.get("distinct_tools") or "[]")
    spans = db.fetch_all("SELECT * FROM spans WHERE trace_id=? ORDER BY idx", (trace_id,))
    for span in spans:
        span["index"] = span.pop("idx")
    trace["spans"] = spans
    # Repeats are collapsed in the UI, so the API says which steps are identical.
    seen: dict[tuple[str, str], int] = {}
    for span in spans:
        key = (span["type"], span["content_redacted"])
        seen[key] = seen.get(key, 0) + 1
    for span in spans:
        span["repeat_count"] = seen[(span["type"], span["content_redacted"])]
    return trace


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mode": settings.raft_otari_mode, "traces": db.trace_count()}


@app.get("/api/home")
def home() -> dict:
    """Pre-made insights, computed from the current data on every request."""
    total = db.trace_count()
    outcome_rows = db.fetch_all(
        "SELECT outcome AS key, COUNT(*) AS count, ROUND(SUM(cost_usd),4) AS cost FROM traces GROUP BY outcome ORDER BY count DESC"
    )
    failure_rows = db.fetch_all(
        """
        SELECT failure_mode AS key, COUNT(*) AS count, ROUND(SUM(cost_usd),4) AS cost
        FROM traces WHERE failure_mode != 'none'
        GROUP BY failure_mode ORDER BY count DESC LIMIT 5
        """
    )
    unmet = db.fetch_all(
        """
        SELECT intent_label AS key, app, COUNT(*) AS count
        FROM traces WHERE outcome = 'unmet request'
        GROUP BY intent_label, app ORDER BY count DESC LIMIT 5
        """
    )
    gave_up = db.fetch_one("SELECT COUNT(*) AS count FROM traces WHERE user_gave_up = 1")
    unsatisfied = db.fetch_one("SELECT COUNT(*) AS count FROM traces WHERE intent_satisfied = 'no'")
    spend = db.fetch_one("SELECT SUM(cost_usd) AS total FROM traces") or {"total": 0}
    apps = db.fetch_all("SELECT app AS key, COUNT(*) AS count FROM traces GROUP BY app ORDER BY count DESC")
    window = db.fetch_one("SELECT MIN(started_at) AS first, MAX(started_at) AS last FROM traces")
    return {
        "trace_count": total,
        "total_spend_usd": round(float(spend["total"] or 0), 2),
        "outcomes": outcome_rows,
        "top_failures": failure_rows,
        "top_unmet_requests": unmet,
        "apps": apps,
        "window": window,
        "headline_stats": [
            {
                "label": "did not get what they wanted",
                "value": int(unsatisfied["count"] if unsatisfied else 0),
                "share": round(float(unsatisfied["count"] if unsatisfied else 0) / total, 4) if total else 0,
                "question": "Where do conversations end without the user getting what they wanted?",
            },
            {
                "label": "users gave up mid-conversation",
                "value": int(gave_up["count"] if gave_up else 0),
                "share": round(float(gave_up["count"] if gave_up else 0) / total, 4) if total else 0,
                "question": "Which apps do users give up on most?",
            },
            {
                "label": "asked for something the agent cannot do",
                "value": sum(int(row["count"]) for row in outcome_rows if row["key"] == "unmet request"),
                "share": round(
                    sum(int(row["count"]) for row in outcome_rows if row["key"] == "unmet request") / total, 4
                )
                if total
                else 0,
                "question": "What are people asking for that my agent cannot do?",
            },
        ],
        "example_questions": [
            "What do my users struggle with most?",
            "What are people asking for that my agent cannot do?",
            "How many people are getting product suggestions?",
            "Where do conversations end without the user getting what they wanted?",
            "Which failure mode costs me the most?",
            "Which app do users give up on most?",
            "What do people ask the developer assistant about?",
            "How often does the agent claim to do something it cannot?",
        ],
        "vocabulary": {
            "dimensions": [
                {"key": key, "label": dimension.label} for key, dimension in DIMENSIONS.items()
            ],
            "metrics": [{"key": key, "label": metric.label} for key, metric in METRICS.items()],
        },
    }


@app.get("/api/traces")
def traces(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: str = "",
    semantic: str = "",
    outcome: str = "",
    failure_mode: str = "",
    app_name: str = Query("", alias="app"),
    trace_ids: str = "",
) -> dict:
    clauses: list[str] = []
    values: list[object] = []
    if search:
        clauses.append("(id LIKE ? OR summary LIKE ? OR user_request LIKE ? OR what_happened LIKE ?)")
        needle = f"%{search}%"
        values.extend([needle] * 4)
    for column, value in (("outcome", outcome), ("failure_mode", failure_mode), ("app", app_name)):
        if value:
            clauses.append(f"{column}=?")
            values.append(value)
    selected_ids = [item for item in trace_ids.split(",") if item]
    if selected_ids:
        clauses.append(f"id IN ({','.join('?' for _ in selected_ids)})")
        values.extend(selected_ids)

    if semantic.strip():
        # Meaning-based search: rank every candidate, then page the ranking.
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        candidates = [row["id"] for row in db.fetch_all(f"SELECT id FROM traces {where}", tuple(values))]
        ranked = manager.index.rank(semantic.strip(), candidates)
        ordered = [trace_id for trace_id, score in ranked if score > 0.02]
        total = len(ordered)
        window = ordered[(page - 1) * page_size : page * page_size]
        if not window:
            return {"items": [], "page": page, "page_size": page_size, "total": total, "ranked_by": "semantic"}
        placeholders = ",".join("?" for _ in window)
        rows = db.fetch_all(
            f"SELECT {_TRACE_COLUMNS} FROM traces WHERE id IN ({placeholders})", tuple(window)
        )
        by_id = {row["id"]: row for row in rows}
        scores = dict(ranked)
        items = []
        for trace_id in window:
            row = by_id.get(trace_id)
            if row:
                row["relevance"] = round(float(scores.get(trace_id, 0)), 4)
                items.append(row)
        return {"items": items, "page": page, "page_size": page_size, "total": total, "ranked_by": "semantic"}

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    count_row = db.fetch_one(f"SELECT COUNT(*) AS count FROM traces {where}", tuple(values))
    offset = (page - 1) * page_size
    rows = db.fetch_all(
        f"SELECT {_TRACE_COLUMNS} FROM traces {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
        tuple(values + [page_size, offset]),
    )
    return {
        "items": rows,
        "page": page,
        "page_size": page_size,
        "total": int(count_row["count"]),
        "ranked_by": "recency",
    }


_TRACE_COLUMNS = (
    "id,app,started_at,duration_ms,status,cost_usd,tokens_input,tokens_output,outcome,failure_mode,"
    "summary,user_request,intent_satisfied,user_gave_up,sentiment_end,turns,tool_calls,"
    "redaction_status,guardrail_status,capture_completeness"
)


@app.get("/api/traces/facets")
def trace_facets() -> dict:
    return {
        "outcome": db.fetch_all("SELECT outcome AS value, COUNT(*) AS count FROM traces GROUP BY outcome ORDER BY count DESC"),
        "failure_mode": db.fetch_all(
            "SELECT failure_mode AS value, COUNT(*) AS count FROM traces GROUP BY failure_mode ORDER BY count DESC"
        ),
        "app": db.fetch_all("SELECT app AS value, COUNT(*) AS count FROM traces GROUP BY app ORDER BY count DESC"),
    }


@app.get("/api/traces/{trace_id}")
def trace_detail(trace_id: str) -> dict:
    return _trace_detail(trace_id)


@app.post("/api/questions/plan")
async def plan_question(payload: dict) -> QuestionPlan:
    question = str(payload.get("question") or "").strip()
    if len(question) < 3:
        raise HTTPException(422, "Question must contain at least three characters")
    if len(question) > 400:
        raise HTTPException(422, "Question must be shorter than 400 characters")
    return await manager.plan_question(question, str(payload.get("parent_run_id") or "") or None)


@app.post("/api/analysis-runs")
async def create_run(payload: CreateRunRequest):
    if payload.question != payload.plan.question:
        raise HTTPException(422, "Question and plan question must match")
    return manager.create_run(payload.plan, payload.parent_run_id)


@app.get("/api/analysis-runs/{run_id}")
def get_run(run_id: str):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    spec = json.loads(row.pop("spec_json", "{}") or "{}")
    row["spec"] = spec
    row["filters"] = [item["label"] for item in spec.get("predicates", [])]
    row["rationale"] = spec.get("rationale", [])
    row["group_by_label"] = (
        DIMENSIONS[spec["group_by"]].label if spec.get("group_by") in DIMENSIONS else None
    )
    row["metric_label"] = METRICS[spec["metric"]].label if spec.get("metric") in METRICS else None
    if row.get("aspect_id"):
        aspect = db.fetch_one("SELECT question,type,version FROM aspects WHERE id=?", (row["aspect_id"],))
        row["aspect"] = aspect
    return row


@app.post("/api/analysis-runs/{run_id}/confirm")
async def confirm_run(run_id: str, payload: ConfirmRunRequest):
    try:
        return manager.confirm_run(run_id, payload.aspect_question)
    except KeyError as error:
        raise HTTPException(404, "Run not found") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/analysis-runs/{run_id}/resume")
async def resume_run(run_id: str):
    try:
        return manager.resume_run(run_id)
    except KeyError as error:
        raise HTTPException(404, "Run not found") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/analysis-runs/{run_id}/events")
async def run_events(request: Request, run_id: str) -> StreamingResponse:
    if not db.get_run(run_id):
        raise HTTPException(404, "Run not found")

    async def stream() -> AsyncIterator[str]:
        last_payload = ""
        terminal = {"complete", "paused_budget", "paused_provider", "guardrail_blocked", "failed"}
        while not await request.is_disconnected():
            row = db.get_run(run_id)
            if not row:
                break
            payload = json.dumps(row, separators=(",", ":"))
            if payload != last_payload:
                yield f"event: progress\ndata: {payload}\n\n"
                last_payload = payload
            if row["status"] in terminal:
                yield f"event: done\ndata: {payload}\n\n"
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/analysis-runs/{run_id}/answer")
def run_answer(run_id: str):
    answer = db.get_answer_for_run(run_id)
    if not answer:
        row = db.get_run(run_id)
        if not row:
            raise HTTPException(404, "Run not found")
        raise HTTPException(409, f"Answer is not ready; run is {row['status']}")
    return answer


@app.get("/api/analysis-runs/{run_id}/work")
def run_work(run_id: str):
    answer = db.get_answer_for_run(run_id)
    if not answer:
        raise HTTPException(404, "Work record not found")
    return answer["work"]


@app.get("/api/aspects")
def list_aspects():
    return db.fetch_all(
        """
        SELECT a.id,a.question,a.type,a.version,a.created_from,a.created_at,
               COUNT(av.trace_id) AS evaluated,
               SUM(CASE WHEN av.value_json='true' THEN 1 ELSE 0 END) AS yes_count
        FROM aspects a LEFT JOIN aspect_values av ON av.aspect_id = a.id AND av.status='complete'
        GROUP BY a.id
        -- An aspect nobody confirmed has no rows and no answer: it is a draft a
        -- visitor abandoned at the estimate, not a saved aspect. Listing it puts
        -- near-identical 0-evaluated rows beside the real one, which reads as a
        -- bug. HAVING, not WHERE: `evaluated` is an aggregate.
        HAVING COUNT(av.trace_id) > 0
        ORDER BY a.created_at DESC
        """
    )


@app.get("/api/aspects/{aspect_id}/verification")
def aspect_verification(aspect_id: str):
    return manager.verification(aspect_id)


@app.post("/api/spans/{span_id}/explain")
async def span_explain(span_id: str):
    try:
        return await manager.explain_span(span_id)
    except KeyError as error:
        raise HTTPException(404, "Span not found") from error


@app.post("/api/spans/{span_id}/web-search")
async def span_web_search(span_id: str):
    try:
        return await manager.web_search_span(span_id)
    except KeyError as error:
        raise HTTPException(404, "Span not found") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/settings/status")
def settings_status() -> dict:
    roles = settings.load_model_roles()
    role_rows = []
    for role_name, role in roles.items():
        observation = db.fetch_one(
            "SELECT * FROM model_observations WHERE role=? ORDER BY id DESC LIMIT 1",
            (role_name,),
        )
        role_rows.append(
            {
                **role.model_dump(),
                "configured": role.configured,
                "observed_final_model": observation.get("final_model") if observation else None,
                "last_request_id": observation.get("request_id") if observation else None,
                "last_status": observation.get("status") if observation else "pending_live_evidence",
                "last_error": observation.get("error") if observation else None,
            }
        )
    feature_rows = db.fetch_all("SELECT * FROM feature_evidence ORDER BY feature")
    dataset_provenance = {
        row["key"]: row["value"] for row in db.fetch_all("SELECT key,value FROM dataset_provenance")
    }
    labelled = db.fetch_one("SELECT COUNT(*) AS count FROM trace_label_provenance")
    cluster_run = db.fetch_one("SELECT * FROM cluster_runs ORDER BY created_at DESC LIMIT 1")
    # The same predicate the Saved aspects list uses, as a subquery rather than a
    # restatement: a counter that says 2 above a list of 1 is worse than no counter.
    aspects = db.fetch_one(
        """
        SELECT COUNT(*) AS count FROM (
          SELECT a.id
          FROM aspects a LEFT JOIN aspect_values av ON av.aspect_id = a.id AND av.status='complete'
          GROUP BY a.id
          HAVING COUNT(av.trace_id) > 0
        )
        """
    )
    return {
        "mode": settings.raft_otari_mode,
        "roles": role_rows,
        "features": feature_rows,
        "budget": {
            "local_run_allowance_usd": settings.raft_local_run_allowance_usd,
            "otari_headroom_usd": None,
            "note": "Otari headroom appears after a live budget response is observed.",
        },
        "mcp": {
            "status": "ready_read_only",
            "local_url": f"{settings.raft_app_url.rstrip('/')}/mcp",
            "public_url": settings.raft_public_mcp_url or None,
            "tools": [tool["name"] for tool in TOOL_DEFINITIONS],
        },
        "public_app_url": settings.raft_public_app_url or None,
        "analysis": _analysis_status(int(aspects["count"] if aspects else 0)),
        "dataset": {
            "trace_count": db.trace_count(),
            "is_current": dataset_is_current(db)[0],
            "version_note": dataset_is_current(db)[1],
            "live_labelled_count": int(labelled["count"] if labelled else 0),
            "provenance": dataset_provenance,
            "cluster_run": cluster_run,
            "truth_status": (
                "accepted_live_pipeline"
                if int(labelled["count"] if labelled else 0) == db.trace_count() and cluster_run
                else "generated_demo_dataset"
            ),
        },
    }


def _role_was_called(role: str) -> bool:
    return bool(db.fetch_one("SELECT 1 AS ok FROM model_observations WHERE role=? LIMIT 1", (role,)))


def _observed_planner() -> str:
    """What the planner actually did, not what live mode intends it to do."""
    if not _role_was_called("planner"):
        return "not yet observed"
    # mcp_server_ids is only ever sent when the server id is configured, so a
    # planner call plus a configured id is the only combination that proves the
    # tools were offered. A request id alone proves nothing about MCP.
    if settings.otari_planner_mcp_server_id:
        return "Otari planner + Raft MCP tools"
    return "Otari planner (no MCP server registered)"


def _observed_aggregation() -> str:
    row = db.fetch_one("SELECT status, notes FROM feature_evidence WHERE feature='Code Execution'")
    if not row:
        return "not yet observed"
    if "failed" in str(row["status"]):
        # The exact body belongs in the feature-evidence row and the log, not in a
        # settings cell: a raw JSON payload here reads as a crash rather than as a
        # degradation Raft handled on purpose.
        code = re.search(r"HTTP (\d{3})", str(row["notes"]))
        return (
            "the same Python, executed in this process (the Otari sandbox was tried and "
            + (f"returned HTTP {code.group(1)})" if code else "failed)")
        )
    if "observed" in str(row["status"]):
        return "Otari sandbox session"
    return "not yet observed"


def _observed_aspect_judge() -> str:
    run = db.fetch_one(
        "SELECT id, status, completed, total, aspect_id FROM runs "
        "WHERE aspect_id IS NOT NULL AND aspect_id != '' ORDER BY rowid DESC LIMIT 1"
    )
    if not run:
        return "not yet observed"
    judges = [
        str(row["model_id"])
        for row in db.fetch_all(
            "SELECT DISTINCT model_id FROM aspect_values WHERE aspect_id=? ORDER BY model_id",
            (run["aspect_id"],),
        )
    ]
    if not judges:
        return "not yet observed"
    label = ", ".join(judges)
    if str(run["status"]).startswith("paused"):
        # Judged rows are judged rows, but the reader should know the run is
        # not finished before they read the model name as the whole story.
        return f"{label} (most recent run paused at {int(run['completed']):,} of {int(run['total']):,})"
    return label


def _observed_cluster_naming() -> str:
    if _role_was_called("cluster_namer"):
        return "routed model"
    return "named from member wording (no live cluster-naming call recorded)"


def _analysis_status(aspects_defined: int) -> dict:
    live = settings.raft_otari_mode == "live"
    otari = live and settings.raft_llm_provider == "otari"
    return {
        "mode": settings.raft_otari_mode,
        "provider": settings.raft_llm_provider if live else "none",
        "planner": _observed_planner() if otari else ("model planner" if live else "local question compiler"),
        "aggregation": _observed_aggregation() if otari else "local execution of the same Python source",
        "aspect_judge": _observed_aspect_judge() if live else "local:semantic-judge-v1",
        "cluster_naming": _observed_cluster_naming() if live else "class-based TF-IDF over member wording",
        "embeddings": manager.index.backend,
        "aspects_defined": aspects_defined,
        "otari_features_available": otari,
        "note": (
            "Every number in every answer is executed Python over real rows in all modes; only who writes the "
            "prose and who judges each trace changes."
        ),
    }


@app.get("/api/settings/preflight")
async def settings_preflight() -> dict:
    """Verify every configured model is actually callable by its workspace key."""
    return await check_models(settings, settings.load_model_roles())


@app.post("/api/settings/reset-demo")
async def reset_demo(payload: dict | None = None) -> dict:
    """Rebuild the demo dataset. Every derived answer is discarded with it."""
    count = int((payload or {}).get("trace_count") or settings.raft_demo_trace_count)
    seed = int((payload or {}).get("seed") or settings.raft_demo_seed)
    count = max(50, min(count, 5000))
    await asyncio.to_thread(reset_dataset, db)
    summary = await asyncio.to_thread(build_dataset, db, count=count, seed=seed, use_bge=settings.raft_use_bge)
    await asyncio.to_thread(manager.index.refresh)
    return {"ok": True, **summary}


@app.post("/mcp")
async def mcp_endpoint(message: dict) -> JSONResponse:
    return JSONResponse(mcp_response(mcp_tools, message))


@app.get("/mcp")
def mcp_metadata() -> dict:
    return {"name": "raft-redacted-traces", "read_only": True, "tools": TOOL_DEFINITIONS}


web_dist = settings.raft_web_dist_path
assets_dir = web_dist / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/") or full_path == "mcp":
        raise HTTPException(404)
    index = Path(web_dist) / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {"message": "Raft API is running. Build the frontend with `cd web && npm run build`."},
        status_code=200,
    )
