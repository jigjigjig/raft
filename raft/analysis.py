from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections import defaultdict
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from raft.clustering import graph_clusters, label_from_exemplar, name_clusters
from raft.config import Settings
from raft.dataset import DatasetIndex
from raft.db import Database, utc_now
from raft.judge import JUDGE_ID, SemanticAspectJudge, scope_text
from raft.otari import OtariClient, OtariError, SandboxClient
from raft.query import (
    DIMENSIONS,
    METRICS,
    QuerySpec,
    QuestionCompiler,
    aggregation_code,
    format_metric,
    spec_to_json,
)
from raft.redaction import verify_literal_quote
from raft.schemas import (
    AnalysisRun,
    Answer,
    AnswerGroup,
    GroupProfile,
    Standout,
    AspectDefinition,
    CompiledFilter,
    EvidenceQuote,
    QuestionPlan,
    WorkRecord,
)


MAX_GROUPS = 12
MAX_TRACE_GROUPS = 10


class AspectJudgment(BaseModel):
    value: bool | float | str
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str


class PlannerResult(BaseModel):
    path: str
    explanation: str
    aspect_question: str | None = None
    aspect_type: str | None = None
    tools_used: list[str] = Field(default_factory=list)


class ClusterNamingResult(BaseModel):
    names: dict[str, str]
    interpretation: str


def run_to_schema(row: dict[str, Any]) -> AnalysisRun:
    return AnalysisRun.model_validate(row)


class AnalysisManager:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.otari = OtariClient(settings, db)
        self.sandbox = SandboxClient(settings, self.otari)
        self.index = DatasetIndex(
            db,
            use_bge=settings.raft_use_bge,
            bge_model=settings.raft_embedding_model,
            bge_revision=settings.raft_embedding_revision,
        )
        self.tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------
    async def plan_question(self, question: str, parent_run_id: str | None = None) -> QuestionPlan:
        self.index.ensure()
        compiler = QuestionCompiler(self.index.catalog)
        spec = compiler.compile(question)

        parent_spec = self._parent_spec(parent_run_id)
        if parent_spec:
            self._inherit(spec, parent_spec)

        if self.settings.raft_otari_mode == "live":
            await self._consult_planner_model(spec)

        eligible, total = self.count_eligible(spec)
        if eligible == 0 and spec.predicates:
            eligible, total = self._relax(spec)

        plan = QuestionPlan(
            question=question,
            path=spec.path,
            explanation=" ".join(spec.rationale),
            requires_confirmation=spec.path == "layer3_aspect",
            eligible_count=eligible,
            total_count=total,
            metric=spec.metric,
            aggregate=spec.aggregate,
            group_by=spec.group_by,
            group_by_label=DIMENSIONS[spec.group_by].label if spec.group_by else None,
            filters=[CompiledFilter(field=item.field, label=item.label) for item in spec.predicates],
            focus_terms=spec.focus_terms[:10],
            rationale=spec.rationale,
            plan_summary=spec.describe(),
            spec=self.serialize_spec(spec),
            planner_mode="otari_mcp" if getattr(spec, "planner_mode", "") == "otari_mcp" else "local",
            inspected_tools=getattr(spec, "inspected_tools", []),
            parent_run_id=parent_run_id,
        )
        if spec.path == "layer3_aspect":
            plan.aspect = AspectDefinition(
                question=spec.aspect_question or question,
                type=spec.aspect_type,
                created_from=question,
            )
            plan.estimate_usd = self._estimate_cost(eligible)
            seconds = self._estimate_seconds(eligible)
            plan.estimate_seconds_min, plan.estimate_seconds_max = seconds
        return plan

    def _relax(self, spec: QuerySpec) -> tuple[int, int]:
        """Drop the fewest filters needed to find anything, and say which.

        Dropping all of them was worse than useless: every question whose
        filters missed became a cluster of the entire dataset, so a hundred
        different questions returned one identical answer. Filters are removed
        one at a time, least-certain first, and whatever is dropped is reported
        on the answer rather than absorbed silently.
        """
        # A value read straight out of the dataset (an app or tool name the user
        # typed) is the most likely to be what they meant; a phrase inferred
        # from the lexicon is the most likely to be wrong.
        certainty = {"app": 3, "tool": 3, "model": 3, "error_code": 3, "started_at": 2}
        ordered = sorted(spec.predicates, key=lambda item: certainty.get(item.field, 1))

        kept = list(spec.predicates)
        dropped: list[str] = []
        for candidate in ordered:
            if len(kept) <= 1:
                break
            kept = [item for item in kept if item is not candidate]
            dropped.append(candidate.label)
            spec.predicates = kept
            eligible, total = self.count_eligible(spec)
            if eligible:
                spec.rationale.append(
                    "Nothing matched every part of that question, so Raft ignored "
                    + " and ".join(f"“{label}”" for label in dropped)
                    + f" and answered over the remaining {eligible:,} conversations."
                )
                return eligible, total

        # Even one filter finds nothing. Answer the whole set, but say plainly
        # that the question's own constraints were not met.
        spec.predicates = []
        spec.rationale.append(
            "No conversation in this dataset matches that question — Raft could not honour "
            + " or ".join(f"“{item.label}”" for item in ordered)
            + ". The answer below is over every captured conversation instead, so treat it as context, "
            "not as an answer to what you asked."
        )
        spec.unmatched = True  # type: ignore[attr-defined]
        return self.count_eligible(spec)

    async def _consult_planner_model(self, spec: QuerySpec) -> None:
        """Let the Otari planner override the compiled route when configured."""
        try:
            server_ids = (
                [self.settings.otari_planner_mcp_server_id]
                if self.settings.otari_planner_mcp_server_id
                else None
            )
            completion, parsed = await self.otari.complete(
                "planner",
                [
                    {
                        "role": "system",
                        "content": (
                            "You route a question about a redacted LLM trace dataset. Use Raft's MCP tools to "
                            "inspect the dataset. Choose layer1 when recorded shape columns answer it, "
                            "layer2_cluster when the answer is in what people wrote, and layer3_aspect only when "
                            "a new per-trace judgment is required. Raft has already compiled a candidate plan; "
                            "keep it unless it is wrong."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"question": spec.question, "compiled_plan": json.loads(spec_to_json(spec))}
                        ),
                    },
                ],
                response_schema=PlannerResult,
                mcp_server_ids=server_ids,
            )
            if not parsed:
                return
            if parsed.tools_used:
                self.db.record_feature(
                    "MCP Servers",
                    "planner_reported_tool_use_log_pending",
                    completion.request_id,
                    "Planner reported using: " + ", ".join(parsed.tools_used),
                )
            if parsed.path in ("layer1", "layer2_cluster", "layer3_aspect"):
                if parsed.path != spec.path:
                    spec.rationale.append(
                        f"The Otari planner re-routed this from {spec.path} to {parsed.path}: {parsed.explanation}"
                    )
                spec.path = parsed.path  # type: ignore[assignment]
            if parsed.path == "layer3_aspect":
                spec.aspect_question = parsed.aspect_question or spec.aspect_question or spec.question
                spec.aspect_type = parsed.aspect_type or "boolean"
            spec.planner_mode = "otari_mcp"  # type: ignore[attr-defined]
            spec.inspected_tools = parsed.tools_used  # type: ignore[attr-defined]
        except OtariError as error:
            # The compiled plan already stands on its own; Settings shows the failure.
            spec.rationale.append(f"The Otari planner was unavailable ({error}); Raft used its own compiled plan.")

    def _parent_spec(self, parent_run_id: str | None) -> dict[str, Any] | None:
        if not parent_run_id:
            return None
        row = self.db.get_run(parent_run_id)
        if not row:
            return None
        try:
            return json.loads(row["spec_json"] or "{}") or None
        except json.JSONDecodeError:
            return None

    def _inherit(self, spec: QuerySpec, parent: dict[str, Any]) -> None:
        """Follow-ups keep the previous question's scope unless they replace it."""
        existing = {item.sql for item in spec.predicates}
        inherited = 0
        for raw in parent.get("predicates", []):
            if raw["sql"] in existing:
                continue
            if any(item.field == raw["field"] for item in spec.predicates):
                continue  # the follow-up replaced this dimension
            from raft.query import Predicate

            spec.predicates.append(Predicate(raw["field"], raw["sql"], tuple(raw["params"]), raw["label"]))
            inherited += 1
        if inherited:
            spec.rationale.insert(
                0, f"Carried {inherited} filter(s) forward from the previous question."
            )

    @staticmethod
    def serialize_spec(spec: QuerySpec) -> dict[str, Any]:
        return {
            "question": spec.question,
            "path": spec.path,
            "metric": spec.metric,
            "aggregate": spec.aggregate,
            "group_by": spec.group_by,
            "focus": spec.focus,
            "focus_terms": spec.focus_terms,
            "aspect_question": spec.aspect_question,
            "aspect_type": spec.aspect_type,
            "limit": spec.limit,
            "rationale": spec.rationale,
            "predicates": [
                {"field": item.field, "sql": item.sql, "params": list(item.params), "label": item.label}
                for item in spec.predicates
            ],
        }

    @staticmethod
    def deserialize_spec(raw: dict[str, Any]) -> QuerySpec:
        from raft.query import Predicate

        spec = QuerySpec(question=raw.get("question", ""))
        spec.path = raw.get("path", "layer1")
        spec.metric = raw.get("metric", "traces")
        spec.aggregate = raw.get("aggregate", "count")
        spec.group_by = raw.get("group_by")
        spec.focus = raw.get("focus", "")
        spec.focus_terms = raw.get("focus_terms", [])
        spec.aspect_question = raw.get("aspect_question")
        spec.aspect_type = raw.get("aspect_type", "boolean")
        spec.limit = raw.get("limit", 8)
        spec.rationale = raw.get("rationale", [])
        spec.predicates = [
            Predicate(item["field"], item["sql"], tuple(item["params"]), item["label"])
            for item in raw.get("predicates", [])
        ]
        return spec

    # ------------------------------------------------------------------
    # Eligible-set SQL
    # ------------------------------------------------------------------
    def eligible_sql(self, spec: QuerySpec, columns: str) -> tuple[str, list[Any]]:
        where, params = spec.where()
        clause = f"WHERE {where}" if where else ""
        return f"SELECT {columns} FROM traces t {clause}", params

    def count_eligible(self, spec: QuerySpec) -> tuple[int, int]:
        sql, params = self.eligible_sql(spec, "COUNT(*) AS count")
        row = self.db.fetch_one(sql, tuple(params))
        return int(row["count"] if row else 0), self.db.trace_count()

    def _estimate_cost(self, eligible: int) -> float:
        if self.settings.raft_otari_mode != "live":
            return 0.0
        estimate = eligible * ((700 * 0.10 / 1_000_000) + (40 * 0.30 / 1_000_000))
        return round(max(0.01, estimate), 2)

    def _estimate_seconds(self, eligible: int) -> tuple[int, int]:
        if self.settings.raft_otari_mode != "live":
            per_trace = 0.0012  # measured local judge throughput
            base = max(1.0, eligible * per_trace)
            return int(base), int(base * 2.5) + 2
        return max(10, int(eligible * 0.045)), max(20, int(eligible * 0.075))

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    def create_run(self, plan: QuestionPlan, parent_run_id: str | None = None) -> AnalysisRun:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        aspect_id: str | None = None
        status = "awaiting_confirmation" if plan.requires_confirmation else "queued"
        if plan.aspect:
            aspect_id = self._aspect_for(plan.aspect)
        now = utc_now()
        self.db.execute(
            """
            INSERT INTO runs(
              id,question,path,status,aspect_id,estimate_usd,estimate_seconds_min,
              estimate_seconds_max,completed,total,message,planner_note,spec_json,parent_run_id,
              created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                plan.question,
                plan.path,
                status,
                aspect_id,
                plan.estimate_usd,
                plan.estimate_seconds_min,
                plan.estimate_seconds_max,
                0,
                plan.eligible_count,
                "Waiting for your approval" if plan.requires_confirmation else "Queued",
                plan.plan_summary,
                json.dumps(plan.spec),
                plan.parent_run_id or parent_run_id,
                now,
                now,
            ),
        )
        if not plan.requires_confirmation:
            self._start(run_id)
        row = self.db.get_run(run_id)
        assert row
        return run_to_schema(row)

    def _aspect_for(self, aspect: AspectDefinition) -> str:
        """Reuse an identical aspect so asking twice does not pay twice."""
        existing = self.db.fetch_one(
            "SELECT id FROM aspects WHERE question=? AND type=? ORDER BY version DESC LIMIT 1",
            (aspect.question, aspect.type),
        )
        if existing:
            return str(existing["id"])
        aspect_id = f"asp_{uuid.uuid4().hex[:10]}_v1"
        self.db.execute(
            "INSERT INTO aspects(id,question,type,created_from,version,created_at) VALUES(?,?,?,?,?,?)",
            (aspect_id, aspect.question, aspect.type, aspect.created_from, 1, utc_now()),
        )
        return aspect_id

    def confirm_run(self, run_id: str, aspect_question: str | None = None) -> AnalysisRun:
        row = self.db.get_run(run_id)
        if not row:
            raise KeyError(run_id)
        if row["status"] not in ("awaiting_confirmation", "paused_budget"):
            raise ValueError(f"Run {run_id} cannot be confirmed from {row['status']}")
        if aspect_question and row["aspect_id"]:
            current = self.db.fetch_one("SELECT * FROM aspects WHERE id=?", (row["aspect_id"],))
            if current and current["question"] != aspect_question:
                version = int(current["version"]) + 1
                new_id = f"asp_{uuid.uuid4().hex[:10]}_v{version}"
                self.db.execute(
                    "INSERT INTO aspects(id,question,type,created_from,version,created_at) VALUES(?,?,?,?,?,?)",
                    (new_id, aspect_question, current["type"], current["created_from"], version, utc_now()),
                )
                self.db.update_run(run_id, aspect_id=new_id)
        if row["status"] == "paused_budget" and self.settings.raft_otari_mode == "live":
            self.db.record_feature(
                "Budgets",
                "live_resume_started_log_pending",
                None,
                f"Resume started for {run_id}; only missing aspect rows will be evaluated.",
            )
        self.db.update_run(run_id, status="queued", message="Approved. Preparing the evaluation.", error=None)
        self._start(run_id)
        updated = self.db.get_run(run_id)
        assert updated
        return run_to_schema(updated)

    def resume_run(self, run_id: str) -> AnalysisRun:
        return self.confirm_run(run_id)

    def _start(self, run_id: str) -> None:
        active = self.tasks.get(run_id)
        if active and not active.done():
            return
        self.tasks[run_id] = asyncio.create_task(self._execute(run_id))

    async def _execute(self, run_id: str) -> None:
        row = self.db.get_run(run_id)
        if not row:
            return
        spec = self.deserialize_spec(json.loads(row["spec_json"] or "{}"))
        try:
            if row["path"] == "layer3_aspect":
                await self._run_aspect(row, spec)
            elif row["path"] == "layer2_cluster":
                await self._run_clusters(row, spec)
            else:
                await self._run_layer1(row, spec)
        except Exception as error:  # noqa: BLE001 - persisted so the UI can show it
            self.db.update_run(run_id, status="failed", message="Analysis failed", error=str(error))

    # ------------------------------------------------------------------
    # Layer 1: recorded shape
    # ------------------------------------------------------------------
    async def _run_layer1(self, run: dict[str, Any], spec: QuerySpec) -> None:
        self.db.update_run(run["id"], status="evaluating", message="Selecting the eligible conversations")
        metric = spec.metric_definition
        dimension = spec.group_dimension

        select_group = dimension.sql if dimension else "'all'"
        # Grouping by conversation is only readable if each group is named after
        # what that conversation was, not by its id.
        by_trace = spec.group_by == "trace"
        where, params = spec.where()
        # Grouping by tool counts tool attributions, not conversations, so the
        # join is an inner one and the denominator is labelled accordingly.
        join = " JOIN trace_tools tt ON tt.trace_id = t.id" if spec.needs_tool_join else ""
        clause = f" WHERE {where}" if where else ""
        sql = (
            f"SELECT t.id AS trace_id, {select_group} AS group_key, {metric.sql} AS value,"
            f" t.verbatim_quote AS quote, t.user_request AS user_request, t.summary AS summary"
            f" FROM traces t{join}{clause} ORDER BY t.id"
        )
        selected = self.db.fetch_all(sql, tuple(params))

        rows: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for record in selected:
            key = str(record["group_key"] if record["group_key"] is not None else "unknown")
            pair = (record["trace_id"], key)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            rows.append(
                {
                    "trace_id": record["trace_id"],
                    "group": key,
                    "value": float(record["value"] or 0),
                    "eligible": True,
                }
            )
        await self._finish_answer(
            run,
            spec,
            rows,
            {record["trace_id"]: record for record in selected},
            names=(
                {record["trace_id"]: record["summary"] for record in selected} if by_trace else None
            ),
            rank_hint={row["trace_id"]: row["value"] for row in rows} if spec.metric != "traces" else None,
            method_notes=[
                f"Selected {len(rows):,} rows with parameterised SQL over the trace table.",
                f"Grouping: {dimension.label if dimension else 'none (single total)'}.",
                f"Metric: {metric.label} ({spec.aggregate}).",
            ]
            + (
                ["One row per (conversation, tool) pair, so the denominator counts tool attributions."]
                if spec.needs_tool_join
                else []
            ),
            sql=sql,
            sql_params=list(params),
        )

    # ------------------------------------------------------------------
    # Layer 2: emergent clusters
    # ------------------------------------------------------------------
    async def _run_clusters(self, run: dict[str, Any], spec: QuerySpec) -> None:
        run_id = run["id"]
        self.db.update_run(run_id, status="evaluating", message="Embedding the eligible conversations")
        self.index.ensure()

        sql, params = self.eligible_sql(
            spec, "t.id AS trace_id, t.verbatim_quote AS quote, t.user_request AS user_request, t.summary AS summary"
        )
        sql += " ORDER BY t.id"
        selected = self.db.fetch_all(sql, tuple(params))
        if not selected:
            raise RuntimeError("No conversation matches that question")

        trace_ids = [record["trace_id"] for record in selected]
        notes: list[str] = [f"{len(trace_ids):,} conversations matched the question's filters."]

        # A topical question narrows to what it is actually about first. Only
        # terms that are both in the corpus and reasonably specific can do that;
        # narrowing on a word like "asking" would shrink the answer set for no
        # reason and make the shares meaningless.
        topical = self._topical_query(spec)
        if topical:
            ranked = self.index.rank(topical, trace_ids)
            strong = [trace_id for trace_id, score in ranked if score >= 0.12]
            floor = max(20, int(len(trace_ids) * 0.15))
            if floor <= len(strong) < len(trace_ids):
                trace_ids = sorted(strong)
                notes.append(
                    f"Narrowed to the {len(trace_ids):,} conversations related to “{topical}” "
                    "(cosine ≥ 0.12 in the local embedding space)."
                )
            elif strong:
                notes.append(
                    f"“{topical}” did not separate this set cleanly, so every matching conversation was clustered."
                )

        # Grouping runs over what the person asked plus which tools were
        # involved. Including the agent's prose measurably degrades the groups -
        # conversations that merely share an error message end up together -
        # and naming still comes only from the user's own turns.
        vectors = self.index.intent_vectors_for(trace_ids)
        user_documents = self.index.user_documents_for(trace_ids)
        self.db.update_run(run_id, message=f"Clustering {len(trace_ids):,} redacted conversations")
        labels, centers = graph_clusters(vectors)
        clusters = name_clusters(self.index.index, user_documents, labels, vectors)
        notes.append(
            f"Grouped {len(trace_ids):,} conversations into {len(centers)} by mutual nearest neighbours over what "
            f"each person asked and which tools were called ({self.index.backend}), then merged the groups that "
            "were still close. "
            "Each group is named after the request made by the conversation nearest its centre; the recurring "
            "wording is class-based TF-IDF over the members' own words."
        )
        rank_hint = self._centroid_ranks(vectors, labels, centers, trace_ids)

        record_by_id = {record["trace_id"]: record for record in selected}
        names: dict[str, str] = {}
        terms: dict[str, list[str]] = {}
        exemplars: dict[str, str] = {}
        for cluster in clusters:
            exemplar_id = trace_ids[cluster.medoid]
            exemplar_text = record_by_id[exemplar_id]["user_request"]
            names[cluster.key] = label_from_exemplar(cluster.label, exemplar_text)
            terms[cluster.key] = cluster.terms
            exemplars[cluster.key] = exemplar_text

        interpretation = ""
        if self.settings.raft_otari_mode == "live":
            named = await self._name_clusters_with_model(clusters, trace_ids, record_by_id)
            if named:
                names.update(named[0])
                interpretation = named[1]
                notes.append("Cluster names rewritten by the Otari cluster-namer model; counts unchanged.")

        rows = [
            {"trace_id": trace_ids[position], "group": str(int(label)), "value": 1.0, "eligible": True}
            for position, label in enumerate(labels)
        ]
        await self._finish_answer(
            run,
            spec,
            rows,
            record_by_id,
            method_notes=notes,
            sql=sql,
            sql_params=list(params),
            names=names,
            terms=terms,
            exemplars=exemplars,
            interpretation=interpretation,
            rank_hint=rank_hint,
        )

    def _topical_query(self, spec: QuerySpec) -> str:
        """The part of the question that names a subject the corpus knows about."""
        index = self.index.index
        if index is None:
            return ""
        weights = index.lexical(" ".join(spec.focus_terms))
        if not weights:
            return ""
        specific = [
            (float(index.idf[position]), index.reverse_vocabulary[position])
            for position in sorted(weights, key=lambda item: -weights[item])
            if "_" not in index.reverse_vocabulary[position]
        ]
        # Narrowing throws conversations out of the denominator, so it has to be
        # earned: either several fairly rare words, or one very rare one.
        strong = [term for score, term in specific if score >= 4.0]
        if len(strong) >= 2 or any(score >= 5.5 for score, _ in specific):
            return " ".join(term.replace("_", " ") for term in strong[:5])
        return ""

    @staticmethod
    def _centroid_ranks(vectors, labels, centers, trace_ids: list[str]) -> dict[str, float]:
        """How representative each conversation is of its own group."""
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        normalized = vectors / np.clip(norms, 1e-8, None)
        similarity = normalized @ centers.T
        return {
            trace_ids[position]: float(similarity[position, int(label)])
            for position, label in enumerate(labels)
        }

    async def _name_clusters_with_model(self, clusters, trace_ids, record_by_id):
        try:
            payload = [
                {
                    "cluster": cluster.key,
                    "examples": [record_by_id[trace_ids[member]]["user_request"] for member in cluster.members[:5]],
                    "distinctive_terms": cluster.terms,
                }
                for cluster in clusters
            ]
            _, parsed = await self.otari.complete(
                "cluster_namer",
                [
                    {
                        "role": "system",
                        "content": (
                            "Name each cluster in five words or fewer using the users' own framing, and write one "
                            "interpretation sentence. Never state a count or a share."
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload)},
                ],
                response_schema=ClusterNamingResult,
            )
            if parsed:
                return parsed.names, parsed.interpretation
        except OtariError:
            return None
        return None

    # ------------------------------------------------------------------
    # Layer 3: aspects
    # ------------------------------------------------------------------
    async def _run_aspect(self, run: dict[str, Any], spec: QuerySpec) -> None:
        run_id = run["id"]
        aspect = self.db.fetch_one("SELECT * FROM aspects WHERE id=?", (run["aspect_id"],))
        if not aspect:
            raise RuntimeError("Aspect definition is missing")
        self.index.ensure()

        sql, params = self.eligible_sql(
            spec, "t.id AS trace_id, t.verbatim_quote AS quote, t.user_request AS user_request, t.summary AS summary"
        )
        sql += " ORDER BY t.id"
        selected = self.db.fetch_all(sql, tuple(params))
        trace_ids = [record["trace_id"] for record in selected]
        record_by_id = {record["trace_id"]: record for record in selected}

        existing = {
            row["trace_id"]
            for row in self.db.fetch_all(
                "SELECT trace_id FROM aspect_values WHERE aspect_id=?", (aspect["id"],)
            )
        }
        pending = [trace_id for trace_id in trace_ids if trace_id not in existing]
        self.db.update_run(
            run_id,
            status="evaluating",
            completed=len(trace_ids) - len(pending),
            total=len(trace_ids),
            message=f"Evaluating {len(pending):,} conversations against the aspect",
        )

        notes: list[str]
        if self.settings.raft_otari_mode == "live":
            try:
                notes = await self._evaluate_aspect_live(run_id, aspect, pending, record_by_id, len(trace_ids))
            except OtariError as error:
                # On stage, a gateway hiccup must not end the run. Finish the
                # remaining rows locally and say plainly that it happened.
                self.db.update_run(run_id, error=None)
                remaining = [
                    trace_id
                    for trace_id in trace_ids
                    if not self.db.fetch_one(
                        "SELECT 1 AS ok FROM aspect_values WHERE aspect_id=? AND trace_id=?",
                        (aspect["id"], trace_id),
                    )
                ]
                notes = [f"Otari was unavailable partway through ({error}); Raft finished locally."]
                notes += self._evaluate_aspect_locally(run_id, aspect, remaining, trace_ids)
            else:
                if self.db.get_run(run_id)["status"] == "paused_budget":  # type: ignore[index]
                    return
        else:
            notes = self._evaluate_aspect_locally(run_id, aspect, pending, trace_ids)

        values = self.db.fetch_all(
            """
            SELECT av.trace_id, av.value_json, av.confidence, av.evidence_quote
            FROM aspect_values av
            WHERE av.aspect_id=? AND av.status='complete'
            ORDER BY av.trace_id
            """,
            (aspect["id"],),
        )
        eligible = set(trace_ids)
        rows = []
        quotes: dict[str, str] = {}
        confidence_rank: dict[str, float] = {}
        for value in values:
            if value["trace_id"] not in eligible:
                continue
            decoded = json.loads(value["value_json"])
            rows.append(
                {
                    "trace_id": value["trace_id"],
                    "group": "yes" if decoded is True else "no" if decoded is False else str(decoded),
                    "value": 1.0,
                    "eligible": True,
                }
            )
            if value["evidence_quote"]:
                quotes[value["trace_id"]] = value["evidence_quote"]
            confidence_rank[value["trace_id"]] = float(value["confidence"])

        blocked = self.db.fetch_one(
            "SELECT COUNT(*) AS count FROM aspect_values WHERE aspect_id=? AND status='guardrail_blocked'",
            (aspect["id"],),
        )
        if blocked and int(blocked["count"]):
            notes.append(f"{int(blocked['count'])} trace(s) were guardrail-blocked and excluded from the total.")

        await self._finish_answer(
            run,
            spec,
            rows,
            record_by_id,
            method_notes=notes,
            sql=sql,
            sql_params=list(params),
            names={"yes": "Yes", "no": "No"},
            aspect_quotes=quotes,
            rank_hint=confidence_rank,
        )

    def _evaluate_aspect_locally(
        self, run_id: str, aspect: dict[str, Any], pending: list[str], trace_ids: list[str]
    ) -> list[str]:
        if not pending:
            # Everything is already judged; re-scoring would be wasted work and
            # would give an empty array to the threshold search.
            self.db.update_run(run_id, completed=len(trace_ids), message="Aspect already evaluated")
            return [f"Every eligible conversation was already judged by {JUDGE_ID}; nothing was recomputed."]

        judge = SemanticAspectJudge(self.index.index)
        plan, vector, expanded = judge.analyse(aspect["question"])

        spans_by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if pending:
            placeholders = ",".join("?" for _ in pending)
            for span in self.db.fetch_all(
                f"SELECT trace_id,idx,type,content_redacted FROM spans WHERE trace_id IN ({placeholders}) ORDER BY trace_id,idx",
                tuple(pending),
            ):
                spans_by_trace[span["trace_id"]].append(span)

        scoped = [scope_text(spans_by_trace.get(trace_id, []), plan.scope) for trace_id in pending]
        # Embed exactly the text the judge is reading, not the whole trace, so
        # the dense and lexical halves of the score agree on the evidence.
        full = [scope_text(spans_by_trace.get(trace_id, []), "conversation") for trace_id in pending]
        vectors = self.index.index.encode(scoped)
        # Pass one is gated on the question's own rarest words, so the documents
        # that feed relevance feedback are about the question rather than merely
        # sharing its verbs. Pass two widens the gate with what feedback learned,
        # which is how a conversation that says "my parcel" answers a question
        # that said "delivery".
        gate = judge.last_gate
        scores = judge.score_documents(vector, expanded, scoped, vectors, context=full, context_weight=0.6, gate=gate)
        expanded, learned, learned_indices = judge.relevance_feedback(scores, scoped, expanded)
        if learned:
            gate = gate | learned_indices
            scores = judge.score_documents(
                vector, expanded, scoped, vectors, context=full, context_weight=0.6, gate=gate
            )
        threshold, separation, method = judge.threshold(scores)
        spread = float(np.std(scores)) or 1.0
        plan.threshold, plan.separation, plan.method = threshold, separation, method

        written = 0
        for position, trace_id in enumerate(pending):
            verdict = judge.verdict(
                float(scores[position]),
                threshold,
                spread,
                negated=plan.negated,
                scoped_text=scoped[position],
                expanded=expanded,
            )
            quote = verdict.quote if verify_literal_quote(verdict.quote, scoped[position]) else ""
            self.db.execute(
                """
                INSERT OR REPLACE INTO aspect_values(
                  trace_id,aspect_id,value_json,confidence,model_id,prompt_version,status,cost_usd,
                  request_id,score,evidence_quote
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trace_id,
                    aspect["id"],
                    json.dumps(verdict.value),
                    verdict.confidence,
                    JUDGE_ID,
                    "aspect-local-v1",
                    "complete",
                    0.0,
                    None,
                    verdict.score,
                    quote,
                ),
            )
            written += 1
            if written % 120 == 0:
                self.db.update_run(
                    run_id,
                    completed=len(trace_ids) - len(pending) + written,
                    message=f"Evaluated {written:,} of {len(pending):,} conversations",
                )
        self.db.update_run(run_id, completed=len(trace_ids), message="Aspect evaluation complete")
        return [
            f"Aspect evaluated locally by {JUDGE_ID}: no language model was called.",
            f"Evidence scoped to {plan.scope} turns; question terms: {', '.join(plan.terms[:6]) or 'none'}.",
            f"Corpus-expanded terms: {', '.join(plan.expanded_terms[:6]) or 'none'}.",
            (
                "Relevance feedback added the shared vocabulary of the strongest matches: "
                + ", ".join(learned[:8])
                + "."
            )
            if learned
            else "Relevance feedback added nothing; the question's own terms already separated the set.",
            f"Decision boundary {threshold:.4f} chosen by {method} (separation {separation:.3f}) over "
            f"{len(pending):,} scored conversations.",
        ]

    async def _evaluate_aspect_live(
        self,
        run_id: str,
        aspect: dict[str, Any],
        pending: list[str],
        record_by_id: dict[str, Any],
        total: int,
    ) -> list[str]:
        role = self.otari.role("aspect_evaluator")
        spent = sum(
            float(row["cost_usd"])
            for row in self.db.fetch_all(
                "SELECT cost_usd FROM aspect_values WHERE aspect_id=?", (aspect["id"],)
            )
        )
        routing_note = ""
        done = total - len(pending)
        for offset, trace_id in enumerate(pending):
            if spent >= self.settings.raft_local_run_allowance_usd:
                self.db.update_run(
                    run_id,
                    status="paused_budget",
                    completed=done + offset,
                    message="Paused before Raft's local run allowance was exceeded.",
                )
                return []
            record = record_by_id[trace_id]
            try:
                completion, judgment = await self.otari.complete(
                    "aspect_evaluator",
                    [
                        {
                            "role": "system",
                            "content": (
                                "Evaluate exactly one redacted conversation against the aspect question. Return a "
                                "typed value, a confidence, and an evidence quote copied verbatim from the input."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "aspect": aspect["question"],
                                    "user_request": record["user_request"],
                                    "summary": record["summary"],
                                }
                            ),
                        },
                    ],
                    response_schema=AspectJudgment,
                )
                assert judgment
                source = f"{record['user_request']}\n{record['summary']}"
                quote = judgment.evidence_quote if verify_literal_quote(judgment.evidence_quote, source) else ""
                item_cost = self.otari._usage_cost(completion.usage) or 0.00007
                if completion.model and completion.model != role.primary:
                    routing_note = f" · Routing fallback: {role.primary} → {completion.model}"
                spent += item_cost
                self.db.execute(
                    """
                    INSERT OR REPLACE INTO aspect_values(
                      trace_id,aspect_id,value_json,confidence,model_id,prompt_version,status,cost_usd,
                      request_id,score,evidence_quote
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        trace_id,
                        aspect["id"],
                        json.dumps(judgment.value),
                        judgment.confidence,
                        completion.model or role.primary,
                        "aspect-v1",
                        "complete",
                        item_cost,
                        completion.request_id,
                        None,
                        quote,
                    ),
                )
            except OtariError as error:
                if error.status_code == 403 and "guardrail" in str(error).casefold():
                    self.db.execute("UPDATE traces SET guardrail_status='blocked' WHERE id=?", (trace_id,))
                    self.db.execute(
                        """
                        INSERT OR REPLACE INTO aspect_values(
                          trace_id,aspect_id,value_json,confidence,model_id,prompt_version,status,cost_usd,
                          request_id,score,evidence_quote
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (trace_id, aspect["id"], "null", 0, role.primary, "aspect-v1", "guardrail_blocked", 0, error.request_id, None, ""),
                    )
                    continue
                if error.status_code == 403:
                    self.db.record_feature(
                        "Budgets",
                        "live_403_pause_observed_log_pending",
                        error.request_id,
                        f"Aspect run paused on Otari HTTP 403 after {done + offset} completed rows: {error}",
                    )
                    self.db.update_run(
                        run_id,
                        status="paused_budget",
                        completed=done + offset,
                        message="Otari budget headroom was exhausted. Increase the cap, then resume.",
                        error=str(error),
                    )
                    return []
                raise
            if offset % 20 == 0 or offset == len(pending) - 1:
                self.db.update_run(
                    run_id,
                    completed=done + offset + 1,
                    message=f"Evaluated {done + offset + 1:,} of {total:,} conversations{routing_note}",
                )
                await asyncio.sleep(0)
        return [
            f"Aspect evaluated through Otari with {role.primary} (fallback {', '.join(role.fallbacks)}).",
            f"Local run allowance ${self.settings.raft_local_run_allowance_usd:.2f}; spent ${spent:.4f}.",
        ]

    # ------------------------------------------------------------------
    # Shared answer assembly
    # ------------------------------------------------------------------
    async def _finish_answer(
        self,
        run: dict[str, Any],
        spec: QuerySpec,
        rows: list[dict[str, Any]],
        record_by_id: dict[str, Any],
        *,
        method_notes: list[str],
        sql: str,
        sql_params: list[Any],
        names: dict[str, str] | None = None,
        terms: dict[str, list[str]] | None = None,
        exemplars: dict[str, str] | None = None,
        interpretation: str = "",
        aspect_quotes: dict[str, str] | None = None,
        rank_hint: dict[str, float] | None = None,
    ) -> None:
        run_id = run["id"]
        if not rows:
            raise RuntimeError("The question produced no eligible conversations")
        self.db.update_run(run_id, status="aggregating", message="Executing the aggregation code")

        code = aggregation_code(spec)
        role = self.otari.role("aspect_evaluator" if run["path"] == "layer3_aspect" else "trace_labeler")
        api_key = os.getenv(role.api_key_env)
        result = await self.sandbox.aggregate(rows, api_key, code=code)

        grouped_ids: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            if row.get("eligible", True):
                grouped_ids[str(row["group"])].append(row["trace_id"])
        if rank_hint:
            # Evidence should come from the conversations that best represent a
            # group, not from whichever trace id sorted first.
            for key, ids in grouped_ids.items():
                grouped_ids[key] = sorted(ids, key=lambda trace_id: -rank_hint.get(trace_id, 0.0))

        names = names or {}
        unit = str(result.get("unit", "count"))
        raw_groups = list(result["groups"])
        groups = self._build_groups(raw_groups, grouped_ids, names, terms or {}, exemplars or {}, unit, spec)

        denominator = int(result["denominator"])
        headline = self._headline(spec, result, groups)
        if not interpretation:
            interpretation = self._interpretation(spec, result, groups, denominator)

        evidence = self._evidence(groups, record_by_id, aspect_quotes or {})
        excluded = max(0, self.db.trace_count() - denominator)
        work = WorkRecord(
            execution=result["execution"],
            code=code,
            input_row_count=len(rows),
            denominator=denominator,
            excluded_count=excluded,
            stdout_json=result["stdout_json"],
            session_id=result.get("session_id"),
            request_id=result.get("request_id"),
            sql=sql,
            sql_params=[str(item) for item in sql_params],
            method_notes=method_notes,
        )
        answer = Answer(
            id=f"ans_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            question=run["question"],
            path=run["path"],
            denominator=denominator,
            excluded_count=excluded,
            groups=groups,
            interpretation=interpretation,
            evidence=evidence,
            work=work,
            aspect_id=run.get("aspect_id"),
            headline=headline,
            metric=spec.metric,
            unit=unit,
            spec=self.serialize_spec(spec),
            follow_ups=self._follow_ups(spec, groups),
            standouts=self.find_standouts(groups, denominator),
        )
        self.db.update_run(run_id, status="synthesizing", message="Linking literal evidence")
        self.db.execute(
            """
            INSERT INTO answers(
              id,run_id,question,path,denominator,excluded_count,groups_json,interpretation,
              evidence_json,work_json,aspect_id,spec_json,headline,metric,unit,follow_ups_json,standouts_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                answer.id,
                answer.run_id,
                answer.question,
                answer.path,
                answer.denominator,
                answer.excluded_count,
                json.dumps([group.model_dump() for group in answer.groups]),
                answer.interpretation,
                json.dumps([quote.model_dump() for quote in answer.evidence]),
                answer.work.model_dump_json(),
                answer.aspect_id,
                json.dumps(answer.spec),
                answer.headline,
                answer.metric,
                answer.unit,
                json.dumps(answer.follow_ups),
                json.dumps([item.model_dump() for item in answer.standouts]),
                utc_now(),
            ),
        )
        self.db.update_run(
            run_id,
            status="complete",
            completed=denominator,
            total=denominator,
            message="Answer complete",
            answer_id=answer.id,
        )
        if self.settings.raft_otari_mode == "live":
            budget_evidence = self.db.fetch_one("SELECT status FROM feature_evidence WHERE feature='Budgets'")
            if budget_evidence and budget_evidence["status"] == "live_resume_started_log_pending":
                self.db.record_feature(
                    "Budgets",
                    "live_pause_resume_observed_log_pending",
                    work.request_id,
                    f"Run {run_id} completed after resuming only its missing rows.",
                )

    def _build_groups(
        self,
        raw_groups: list[dict[str, Any]],
        grouped_ids: dict[str, list[str]],
        names: dict[str, str],
        terms: dict[str, list[str]],
        exemplars: dict[str, str],
        unit: str,
        spec: QuerySpec,
    ) -> list[AnswerGroup]:
        if {"yes", "no"} <= {str(item["key"]) for item in raw_groups}:
            # A boolean aspect reads as "yes first", whichever side is larger.
            raw_groups = sorted(raw_groups, key=lambda item: 0 if str(item["key"]) == "yes" else 1)
        limit = MAX_TRACE_GROUPS if spec.group_by == "trace" else MAX_GROUPS
        head = raw_groups[:limit]
        tail = raw_groups[limit:]
        reference = max((float(item["value"]) for item in raw_groups), default=0.0)
        groups = [
            AnswerGroup(
                key=str(item["key"]),
                label=names.get(str(item["key"]), _humanise(str(item["key"]), spec.group_by)),
                count=int(item["count"]),
                share=float(item["share"]),
                value=float(item["value"]),
                value_label=format_metric(float(item["value"]), unit, reference),
                trace_ids=grouped_ids[str(item["key"])],
                terms=terms.get(str(item["key"]), []),
                exemplar=exemplars.get(str(item["key"])),
            )
            for item in head
        ]
        for group in groups:
            group.profile = self.profile_group(group.trace_ids)
        if tail:
            # Folding the tail keeps groups summing to the denominator, which the
            # Answer model enforces, without hiding those traces from drill-down.
            ids: list[str] = []
            for item in tail:
                ids.extend(grouped_ids[str(item["key"])])
            total_value = sum(float(item["value"]) for item in tail)
            total_count = sum(int(item["count"]) for item in tail)
            groups.append(
                AnswerGroup(
                    key="__other__",
                    label=(
                        f"{len(tail):,} other conversations"
                        if spec.group_by == "trace"
                        else f"{len(tail)} smaller groups"
                    ),
                    count=total_count,
                    share=sum(float(item["share"]) for item in tail),
                    value=total_value,
                    value_label=format_metric(total_value, unit, reference),
                    trace_ids=ids,
                )
            )
        return groups

    def profile_group(self, trace_ids: list[str]) -> GroupProfile:
        """Read back what happened to these exact conversations.

        A count on its own is not an insight. What makes a group worth reading
        is whether those people got what they came for, whether they gave up,
        and which failure the agent kept hitting - all of which are recorded, so
        none of it has to be guessed.
        """
        if not trace_ids:
            return GroupProfile()
        placeholders = ",".join("?" for _ in trace_ids)
        row = self.db.fetch_one(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN intent_satisfied='yes' THEN 1 ELSE 0 END) AS resolved,
                   SUM(user_gave_up) AS gave_up,
                   SUM(CASE WHEN sentiment_end IN ('frustrated','angry') THEN 1 ELSE 0 END) AS frustrated,
                   SUM(cost_usd) AS cost,
                   AVG(turns) AS turns,
                   AVG(rephrase_count) AS rephrases
            FROM traces WHERE id IN ({placeholders})
            """,
            tuple(trace_ids),
        ) or {}
        total = max(1, int(row.get("total") or 0))
        failure = self.db.fetch_one(
            f"""
            SELECT failure_mode AS key, COUNT(*) AS count FROM traces
            WHERE id IN ({placeholders}) AND failure_mode != 'none'
            GROUP BY failure_mode ORDER BY count DESC LIMIT 1
            """,
            tuple(trace_ids),
        )
        # A group can be entirely "unmet request", which has no mechanical
        # failure at all; the outcome is then the thing worth reporting.
        outcome = self.db.fetch_one(
            f"SELECT outcome AS key, COUNT(*) AS count FROM traces WHERE id IN ({placeholders}) "
            "GROUP BY outcome ORDER BY count DESC LIMIT 1",
            tuple(trace_ids),
        )
        apps = self.db.fetch_all(
            f"SELECT app AS key, COUNT(*) AS count FROM traces WHERE id IN ({placeholders}) "
            "GROUP BY app ORDER BY count DESC",
            tuple(trace_ids),
        )
        tools = self.db.fetch_all(
            f"SELECT tool AS key, COUNT(*) AS count FROM trace_tools WHERE trace_id IN ({placeholders}) "
            "GROUP BY tool ORDER BY count DESC LIMIT 3",
            tuple(trace_ids),
        )
        return GroupProfile(
            resolved=int(row.get("resolved") or 0),
            resolved_share=float(row.get("resolved") or 0) / total,
            gave_up=int(row.get("gave_up") or 0),
            gave_up_share=float(row.get("gave_up") or 0) / total,
            frustrated=int(row.get("frustrated") or 0),
            cost_usd=round(float(row.get("cost") or 0), 6),
            avg_turns=round(float(row.get("turns") or 0), 2),
            avg_rephrases=round(float(row.get("rephrases") or 0), 2),
            top_failure=failure["key"] if failure else None,
            top_failure_count=int(failure["count"]) if failure else 0,
            top_outcome=outcome["key"] if outcome else None,
            top_outcome_count=int(outcome["count"]) if outcome else 0,
            top_app=apps[0]["key"] if apps else None,
            top_app_count=int(apps[0]["count"]) if apps else 0,
            apps=[row["key"] for row in apps[:3]],
            tools=[row["key"] for row in tools],
        )

    def find_standouts(self, groups: list[AnswerGroup], denominator: int) -> list[Standout]:
        """Find the groups that are unlike the rest, not the ones that are big.

        The largest group is usually the least surprising thing in an answer -
        it is large because the app is popular. What a reader has not already
        guessed is disproportion: a small group nobody gets an answer from, one
        that costs several times what the others cost, or one where a single
        failure accounts for nearly every conversation. Each line below is a
        ratio against this answer's own baseline, so it says something the
        counts alone do not.
        """
        real = [group for group in groups if group.key != "__other__" and group.profile]
        if len(real) < 2 or denominator < 20:
            return []

        total = sum(group.count for group in real) or 1
        base_gave_up = sum(group.profile.gave_up for group in real) / total  # type: ignore[union-attr]
        base_resolved = sum(group.profile.resolved for group in real) / total  # type: ignore[union-attr]
        base_cost = sum(group.profile.cost_usd for group in real) / total  # type: ignore[union-attr]
        base_turns = sum(group.profile.avg_turns * group.count for group in real) / total  # type: ignore[union-attr]

        floor = max(5, int(denominator * 0.02))
        found: list[Standout] = []
        for group in real:
            profile = group.profile
            assert profile
            if group.count < floor:
                continue

            gave_up_rate = profile.gave_up / group.count
            if base_gave_up > 0.02 and gave_up_rate >= 1.5 * base_gave_up and profile.gave_up >= 4:
                found.append(
                    Standout(
                        kind="gave_up",
                        group_key=group.key,
                        label=group.label,
                        lift=round(gave_up_rate / base_gave_up, 2),
                        count=profile.gave_up,
                        trace_ids=group.trace_ids,
                        text=(
                            f"{profile.gave_up} of {group.count} walked away — "
                            f"{gave_up_rate / base_gave_up:.1f}× the rate everywhere else in this answer."
                        ),
                    )
                )

            per_conversation = profile.cost_usd / group.count
            if base_cost > 0 and per_conversation >= 1.6 * base_cost:
                found.append(
                    Standout(
                        kind="cost",
                        group_key=group.key,
                        label=group.label,
                        lift=round(per_conversation / base_cost, 2),
                        count=group.count,
                        trace_ids=group.trace_ids,
                        text=(
                            f"Each of these costs ${per_conversation:.4f}, "
                            f"{per_conversation / base_cost:.1f}× the average conversation here."
                        ),
                    )
                )

            if profile.top_failure and profile.top_failure_count / group.count >= 0.7 and group.count >= 8:
                readable = ENUM_LABELS.get(profile.top_failure, profile.top_failure.replace("_", " "))
                found.append(
                    Standout(
                        kind="failure",
                        group_key=group.key,
                        label=group.label,
                        lift=round(profile.top_failure_count / group.count, 2),
                        count=profile.top_failure_count,
                        trace_ids=group.trace_ids,
                        text=(
                            f"{profile.top_failure_count} of {group.count} fail the same way — "
                            f"“{readable.lower()}”. This is one bug, not {group.count} bad conversations."
                        ),
                    )
                )

            if profile.resolved == 0 and base_resolved > 0.1 and group.count >= 8:
                found.append(
                    Standout(
                        kind="never_resolved",
                        group_key=group.key,
                        label=group.label,
                        lift=round(1 / max(base_resolved, 0.01), 2),
                        count=group.count,
                        trace_ids=group.trace_ids,
                        text=(
                            f"Not one of these {group.count} ended resolved, against "
                            f"{base_resolved * 100:.0f}% elsewhere in this answer."
                        ),
                    )
                )

            if base_turns > 0 and profile.avg_turns >= 1.5 * base_turns and group.count >= 8:
                found.append(
                    Standout(
                        kind="turns",
                        group_key=group.key,
                        label=group.label,
                        lift=round(profile.avg_turns / base_turns, 2),
                        count=group.count,
                        trace_ids=group.trace_ids,
                        text=(
                            f"These run {profile.avg_turns:.1f} turns against {base_turns:.1f} elsewhere — "
                            "people keep rephrasing rather than leaving."
                        ),
                    )
                )

        # One line per group, strongest first, so the section reads as findings
        # rather than as every ratio that happened to clear a threshold.
        found.sort(key=lambda item: -item.lift)
        best_per_group: dict[str, Standout] = {}
        for item in found:
            best_per_group.setdefault(item.group_key, item)
        return sorted(best_per_group.values(), key=lambda item: -item.lift)[:3]

    def _headline(self, spec: QuerySpec, result: dict[str, Any], groups: list[AnswerGroup]) -> str:
        unit = str(result.get("unit", "count"))
        denominator = int(result["denominator"])
        if spec.aggregate == "count" and not spec.group_dimension and spec.path == "layer1":
            total = self.db.trace_count()
            if spec.predicates and total:
                return f"{denominator:,} of {total:,} conversations — {denominator / total * 100:.1f}%"
            return f"{denominator:,} conversations match"
        if spec.metric != "traces":
            real = [group for group in groups if group.key != "__other__"]
            metric_name = spec.metric_definition.label.lower()
            if spec.group_dimension and len(real) > 1:
                top = real[0]
                qualifier = "average " if spec.aggregate == "avg" else ""
                # format_metric already spells out tokens and seconds.
                suffix = "" if unit in ("tokens", "ms") else f" {metric_name}"
                measure = f"{top.value_label} {qualifier}{suffix}".replace("  ", " ").strip()
                if spec.group_by == "trace":
                    return f"{measure} — {top.label}"
                return f"{top.label} — {measure} over {top.count:,} conversations"
            if spec.aggregate == "avg":
                mean = format_metric(float(result.get("value_mean", 0)), unit)
                return f"{mean} average {metric_name} across {denominator:,} conversations"
            return f"{format_metric(float(result['value_sum']), unit)} of {metric_name} across {denominator:,} conversations"
        top = groups[0] if groups else None
        if top:
            return f"{top.label} — {top.count:,} of {denominator:,} ({top.share * 100:.1f}%)"
        return f"{denominator:,} conversations"

    def _interpretation(
        self, spec: QuerySpec, result: dict[str, Any], groups: list[AnswerGroup], denominator: int
    ) -> str:
        """Say what happened to these conversations, not just how many there were.

        Every clause below is read off the rows that were just counted, so the
        sentence is as checkable as the number above it.
        """
        unit = str(result.get("unit", "count"))
        real = [group for group in groups if group.key != "__other__"]
        if not real:
            return f"{denominator:,} conversations matched and were counted as one group."
        scope = spec.filter_summary()

        if {group.key for group in real} <= {"yes", "no"} and len(real) == 2:
            # A boolean aspect always shows yes first, so the usual
            # "largest group" phrasing would be wrong whenever yes is smaller.
            yes = next(group for group in real if group.key == "yes")
            no = next(group for group in real if group.key == "no")
            return (
                f"{yes.count:,} of {denominator:,} {scope} — {yes.share * 100:.1f}% — were judged yes; "
                f"the other {no.count:,} were not. Check the sample below before trusting the split."
            )
        if len(real) == 1 and real[0].key == "all":
            total = self.db.trace_count()
            share = f" — {denominator / total * 100:.1f}% of all {total:,}" if total else ""
            return f"{denominator:,} {spec.filter_summary()}{share}."

        top = real[0]
        parts: list[str] = []
        if spec.metric != "traces" and spec.aggregate != "count":
            metric_name = spec.metric_definition.label.lower()
            parts.append(
                f"{top.label} is the largest single draw on {metric_name}: {top.value_label} "
                f"across {top.count:,} of {denominator:,} {scope}."
            )
        else:
            parts.append(
                f"The biggest group is {top.label} — {top.count:,} of {denominator:,} {scope} "
                f"({top.share * 100:.1f}%)."
            )

        top.resolution_is_implied = bool(
            {item.field for item in spec.predicates} & {"intent_satisfied", "outcome"}
        )
        parts.append(self._describe_outcome(top))

        if len(real) > 1:
            second = real[1]
            ratio = top.count / max(second.count, 1)
            if ratio >= 1.6:
                parts.append(f"That is {ratio:.1f}× the next group, {second.label}.")
            elif ratio >= 1.0:
                parts.append(f"{second.label} is close behind at {second.count:,}.")

        # A pattern that holds across the whole answer is worth more than any
        # single group, so say it when it is true.
        profiles = [group.profile for group in real if group.profile]
        if len(profiles) >= 3:
            pinned = {item.field for item in spec.predicates}
            if not pinned & {"intent_satisfied", "outcome"}:
                unresolved = [group for group in real if group.profile and group.profile.resolved_share < 0.05]
                if len(unresolved) >= max(3, len(real) // 2):
                    parts.append(
                        f"{len(unresolved)} of these {len(real)} groups are resolved in under 5% of cases, "
                        "so this is a capability gap rather than a handful of bad conversations."
                    )
        return " ".join(part for part in parts if part)

    @staticmethod
    def _describe_outcome(group: AnswerGroup) -> str:
        """One sentence about how a group actually went, or nothing."""
        profile = group.profile
        if not profile or not group.count:
            return ""
        clauses: list[str] = []
        if profile.resolved == 0 and not group.resolution_is_implied:
            clauses.append("none of them ended with the user getting what they asked for")
        elif profile.resolved_share < 0.25 and not group.resolution_is_implied:
            clauses.append(f"only {profile.resolved:,} ended resolved")
        elif profile.resolved_share > 0.8 and not group.resolution_is_implied:
            clauses.append(f"{profile.resolved:,} ended resolved")
        if profile.top_failure and profile.top_failure_count >= max(2, group.count // 5):
            readable = ENUM_LABELS.get(profile.top_failure, profile.top_failure.replace("_", " "))
            clauses.append(f"{profile.top_failure_count:,} hit “{readable.lower()}”")
        elif profile.top_outcome and profile.top_outcome_count >= max(2, group.count // 2):
            readable = ENUM_LABELS.get(profile.top_outcome, profile.top_outcome)
            clauses.append(f"{profile.top_outcome_count:,} ended as “{readable.lower()}”")
        if profile.gave_up_share >= 0.3:
            clauses.append(f"{profile.gave_up:,} gave up mid-conversation")
        if not clauses:
            return ""
        where = f" Mostly in {profile.top_app}." if profile.top_app and profile.top_app_count > group.count * 0.6 else ""
        return f"Within it, {', and '.join(clauses)}.{where}"

    def _evidence(
        self,
        groups: list[AnswerGroup],
        record_by_id: dict[str, Any],
        aspect_quotes: dict[str, str],
    ) -> list[EvidenceQuote]:
        """Up to three distinct real sentences per group.

        Two traces in a group often share a phrasing, and printing the same
        sentence twice is what makes a real answer look generated. Only quotes
        whose wording has not been shown yet are kept.
        """
        evidence: list[EvidenceQuote] = []
        for position, group in enumerate(groups):
            if group.key == "__other__":
                continue
            seen: set[str] = set()
            for trace_id in group.trace_ids:
                if len(seen) >= 3:
                    break
                record = record_by_id.get(trace_id)
                if not record:
                    continue
                # The opening ask is what makes an answer land; the stored
                # verbatim quote (the last thing they said) is the fallback.
                quote = aspect_quotes.get(trace_id) or record.get("user_request") or record.get("quote") or ""
                source = f"{record.get('user_request', '')}\n{record.get('quote', '')}\n{quote}"
                normalized = " ".join(quote.casefold().split())
                if not quote or normalized in seen or not verify_literal_quote(quote, source):
                    continue
                seen.add(normalized)
                group.quotes.append(quote)
                if position < 6:
                    # Every group carries its own quotes; the flat evidence list
                    # stays short enough to read.
                    evidence.append(
                        EvidenceQuote(
                            trace_id=trace_id,
                            group_key=group.key,
                            label=group.label,
                            quote=quote,
                            speaker="user",
                        )
                    )
        return evidence

    def _follow_ups(self, spec: QuerySpec, groups: list[AnswerGroup]) -> list[str]:
        suggestions: list[str] = []
        real = [group for group in groups if group.key != "__other__"]
        if real and spec.path != "layer1":
            suggestions.append(f"Why do the “{real[0].label}” conversations fail?")
        if spec.metric == "traces":
            suggestions.append("Which of these costs the most?")
        else:
            suggestions.append("Which app do these belong to?")
        if not any(item.field == "user_gave_up" for item in spec.predicates):
            suggestions.append("Now only the ones where the user gave up")
        if not any(item.field == "started_at" for item in spec.predicates):
            suggestions.append("Same question for the last 7 days")
        return suggestions[:4]

    # ------------------------------------------------------------------
    # Verification and span tools
    # ------------------------------------------------------------------
    def verification(self, aspect_id: str) -> dict[str, Any]:
        aspect = self.db.fetch_one("SELECT * FROM aspects WHERE id=?", (aspect_id,))
        rows = self.db.fetch_all(
            """
            SELECT av.trace_id,av.value_json,av.confidence,av.score,av.evidence_quote,av.model_id,
                   t.summary,t.verbatim_quote,t.user_request
            FROM aspect_values av JOIN traces t ON t.id=av.trace_id
            WHERE av.aspect_id=? AND av.status='complete'
            ORDER BY av.confidence DESC, av.trace_id
            """,
            (aspect_id,),
        )
        yes = [row for row in rows if json.loads(row["value_json"]) is True][:5]
        no = [row for row in rows if json.loads(row["value_json"]) is not True][:5]
        return {
            "aspect_id": aspect_id,
            "question": aspect["question"] if aspect else "",
            "judge": rows[0]["model_id"] if rows else None,
            "yes": yes,
            "no": no,
        }

    async def explain_span(self, span_id: str) -> dict[str, Any]:
        span = self.db.fetch_one("SELECT * FROM spans WHERE id=?", (span_id,))
        if not span:
            raise KeyError(span_id)
        neighbours = self.db.fetch_all(
            "SELECT idx,type,name,content_redacted,status,cost_usd FROM spans WHERE trace_id=? ORDER BY idx",
            (span["trace_id"],),
        )
        if self.settings.raft_otari_mode == "live":
            try:
                result, _ = await self.otari.complete(
                    "span_explainer",
                    [
                        {"role": "system", "content": "Explain this redacted trace step and its neighbours in two concise sentences."},
                        {
                            "role": "user",
                            "content": json.dumps({"span": span, "neighbours": neighbours[max(0, span["idx"] - 2) : span["idx"] + 3]}),
                        },
                    ],
                )
                return {"explanation": result.content, "request_id": result.request_id, "mode": "otari"}
            except OtariError as error:
                return {
                    "explanation": self._local_span_explanation(span, neighbours),
                    "request_id": None,
                    "mode": f"computed locally — Otari was unavailable ({error})",
                }
        return {
            "explanation": self._local_span_explanation(span, neighbours),
            "request_id": None,
            "mode": "computed locally from the trace itself — no model was called",
        }

    def _local_span_explanation(self, span: dict[str, Any], neighbours: list[dict[str, Any]]) -> str:
        position = int(span["idx"])
        total_cost = sum(float(row["cost_usd"]) for row in neighbours) or 1e-9
        share = float(span["cost_usd"]) / total_cost * 100
        parts: list[str] = []
        kind = str(span["type"])
        if kind == "user_message":
            parts.append("This is what the person actually typed, after local redaction.")
        elif kind == "assistant_message":
            parts.append("This is the reply the user saw.")
        elif kind == "tool_call":
            identical = [
                row for row in neighbours if row["type"] == "tool_call" and row["content_redacted"] == span["content_redacted"]
            ]
            if len(identical) > 1:
                positions = ", ".join(f"#{int(row['idx']) + 1}" for row in identical)
                parts.append(
                    f"The agent called {span['name']} with these exact arguments {len(identical)} times "
                    f"(steps {positions}) — a loop, not progress."
                )
            else:
                parts.append(f"The agent called {span['name']} once with these arguments.")
        elif kind == "tool_result":
            parts.append(f"What {span['name']} returned. A base-URL proxy never sees the tool's own duration, so it stays unknown.")
        elif kind == "thinking":
            parts.append("Reasoning the model emitted on the wire before acting.")
        elif kind == "error":
            parts.append(f"The step failed with {span['error_code'] or 'an unlabelled error'} and nothing after it ran normally.")

        before = [row for row in neighbours if int(row["idx"]) < position]
        after = [row for row in neighbours if int(row["idx"]) > position]
        if before:
            parts.append(f"It follows a {before[-1]['type'].replace('_', ' ')} step.")
        if after:
            parts.append(f"Next comes a {after[0]['type'].replace('_', ' ')} step.")
        else:
            parts.append("It is the last recorded step in the trace.")
        parts.append(
            f"It carries {int(span['tokens_in']):,} input and {int(span['tokens_out']):,} output tokens, "
            f"{share:.0f}% of this trace's cost, and "
            + (f"{int(span['duration_ms'])}ms of observed latency." if span["duration_ms"] else "unknown duration.")
        )
        return " ".join(parts)

    async def web_search_span(self, span_id: str) -> dict[str, Any]:
        span = self.db.fetch_one("SELECT * FROM spans WHERE id=?", (span_id,))
        if not span:
            raise KeyError(span_id)
        if not span["error_code"]:
            raise ValueError("Look-up is only available for error spans")
        code = str(span["error_code"])
        if self.settings.raft_otari_mode == "live":
            try:
                result, _ = await self.otari.complete(
                    "planner",
                    [
                        {
                            "role": "user",
                            "content": (
                                f"Look up provider error {code}. Explain what it means, whether it is transient or "
                                "configuration-related, and the usual fix."
                            ),
                        }
                    ],
                    tools=[{"type": "otari_web_search"}],
                    guardrail=False,
                )
                self.db.record_feature(
                    "Web Search Enablement",
                    "live_lookup_observed_log_pending",
                    result.request_id,
                    f"otari_web_search completed for provider error {code}.",
                )
                return {"answer": result.content, "request_id": result.request_id, "mode": "otari_web_search", "citations": []}
            except OtariError:
                pass  # fall through to the local reference below

        occurrences = self.db.fetch_one(
            "SELECT COUNT(*) AS count FROM traces WHERE error_code=?", (code,)
        )
        known = ERROR_REFERENCE.get(code)
        detail = known or (
            "Raft has no local reference entry for this code. Enable live mode to look it up with Otari web search."
        )
        return {
            "answer": (
                f"{detail} This code appears on {int(occurrences['count']) if occurrences else 0} captured "
                f"conversations in this dataset."
            ),
            "request_id": None,
            "mode": "local reference table — no web search was performed",
            "citations": [],
        }


ERROR_REFERENCE: dict[str, str] = {
    "provider_model_overloaded_529": (
        "The upstream provider accepted the request but had no capacity for the model. It is transient: retry with "
        "bounded exponential backoff, and let a routing fallback advance to a second candidate."
    ),
    "provider_rate_limit_429": (
        "The provider rate-limited the key or the workspace. Transient, but sustained 429s mean the concurrency or "
        "token budget needs lowering rather than retrying harder."
    ),
    "gateway_upstream_timeout_504": (
        "The gateway gave up waiting for the provider. Usually a symptom of an unbounded request: shrink the input, "
        "stream, or checkpoint the work."
    ),
    "upstream_context_length_exceeded": (
        "The assembled request was longer than the model's context window. Configuration, not capacity: chunk the "
        "input or move to a longer-context model."
    ),
    "provider_content_filter_451": (
        "The provider's own content filter refused the request. Configuration-related; inspect the redacted input "
        "before assuming a false positive."
    ),
    "tool_upstream_error": (
        "The application's own tool returned an error to the agent. This is not a model failure — the fix is in the "
        "tool or its dependency, and the agent needs a fallback path when it happens."
    ),
}


# Enum values whose bare name would mislead in an answer.
ENUM_LABELS = {
    "none": "No mechanical failure",
    "unknown": "Not recorded",
    "yes": "Yes",
    "no": "No",
    "unclear": "Unclear",
    "tool_loop": "Tool loop",
    "hallucinated_tool": "Claimed an action it never performed",
    "context_overflow": "Context window exceeded",
    "provider_error": "Upstream provider error",
    "wrong_answer": "Answered the wrong question",
    "tool_error": "Tool call failed",
    "unmet request": "Outside the agent's capabilities",
}


# Identifiers must be shown exactly as they are; prettifying a model id makes it
# impossible to match against a routing policy or a request-cost lookup.
_VERBATIM_DIMENSIONS = frozenset({"model", "provider", "app", "tool", "error_code", "day", "week", "hour"})


def _humanise(key: str, dimension: str | None = None) -> str:
    if not key:
        return "Not recorded"
    if dimension in _VERBATIM_DIMENSIONS:
        return key
    if key in ENUM_LABELS:
        return ENUM_LABELS[key]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
        return key
    return key.replace("_", " ").replace("-", " ").strip().capitalize()
