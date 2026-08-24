from __future__ import annotations

import io
import json
import os
import time
from contextlib import redirect_stdout
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from raft.config import ModelRole, Settings
from raft.db import Database, utc_now
from raft.schemas import CompletionResult


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OtariError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class OtariClient:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.roles = settings.load_model_roles()

    def role(self, name: str) -> ModelRole:
        try:
            return self.roles[name]
        except KeyError as error:
            raise OtariError(f"Unknown model role: {name}") from error

    async def complete(
        self,
        role_name: str,
        messages: list[dict[str, str]],
        *,
        response_schema: type[SchemaT] | None = None,
        tools: list[dict[str, Any]] | None = None,
        mcp_server_ids: list[str] | None = None,
        guardrail: bool = True,
        repair_schema_once: bool = True,
    ) -> tuple[CompletionResult, SchemaT | None]:
        role = self.role(role_name)
        if self.settings.raft_otari_mode != "live":
            raise OtariError(
                "Live Otari completion requested while RAFT_OTARI_MODE is not live. "
                "Use the feature-specific mock path or configure credentials."
            )
        generic = self.settings.raft_llm_provider == "openai_compatible"
        api_key = os.getenv(role.api_key_env) or (self.settings.raft_llm_api_key if generic else "")
        if not api_key:
            raise OtariError(
                f"Missing {role.api_key_env}"
                + (" or RAFT_LLM_API_KEY" if generic else "")
                + f" for role {role_name}"
            )
        model = (self.settings.raft_llm_model or role.primary) if generic else role.primary
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
        }
        if response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "strict": True,
                    "schema": response_schema.model_json_schema(),
                },
            }
        # Otari-only request fields. A generic OpenAI-compatible endpoint would
        # reject them, and claiming the feature without sending it would be a lie.
        if tools and not generic:
            payload["tools"] = tools
        if mcp_server_ids and not generic:
            payload["mcp_server_ids"] = mcp_server_ids
        if guardrail and not generic and self.settings.otari_guardrail_profile:
            payload["guardrails"] = [
                {"profile": self.settings.otari_guardrail_profile, "mode": "block"}
            ]

        started = time.perf_counter()
        request_id: str | None = None
        try:
            async with httpx.AsyncClient(timeout=self.settings.otari_request_timeout_seconds) as client:
                base_url = (
                    self.settings.raft_llm_base_url or self.settings.otari_generation_base_url
                    if generic
                    else self.settings.otari_generation_base_url
                )
                response = await client.post(
                    f"{base_url.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
            request_id = response.headers.get("x-request-id")
            if response.status_code >= 400:
                message = response.text[:2000]
                raise OtariError(
                    f"Otari returned HTTP {response.status_code}: {message}",
                    status_code=response.status_code,
                    request_id=request_id,
                )
            raw = response.json()
            raw["_raft_observed_headers"] = {
                name: response.headers[name]
                for name in (
                    "x-request-id",
                    "x-otari-guardrails",
                    "x-otari-model",
                    "x-otari-provider",
                )
                if name in response.headers
            }
            request_id = request_id or raw.get("id") or raw.get("request_id")
            choice = raw.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    item.get("text", "") for item in content if isinstance(item, dict)
                )
            result = CompletionResult(
                content=str(content),
                request_id=request_id,
                model=raw.get("model"),
                provider=raw.get("provider"),
                usage=raw.get("usage") or {},
                raw=raw,
            )
            parsed = None
            if response_schema:
                try:
                    parsed = response_schema.model_validate_json(result.content)
                except ValidationError as validation_error:
                    self._record_observation(
                        role_name,
                        role.primary,
                        status="schema_repair_requested",
                        request_id=request_id,
                        final_model=result.model,
                        provider=result.provider,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        error=str(validation_error),
                    )
                    if repair_schema_once:
                        repair_messages = [
                            *messages,
                            {"role": "assistant", "content": result.content},
                            {
                                "role": "user",
                                "content": (
                                    "The previous response failed the required JSON schema. Return only a corrected "
                                    f"JSON object. Validation error: {validation_error}"
                                ),
                            },
                        ]
                        return await self.complete(
                            role_name,
                            repair_messages,
                            response_schema=response_schema,
                            tools=tools,
                            mcp_server_ids=mcp_server_ids,
                            guardrail=guardrail,
                            repair_schema_once=False,
                        )
                    raise
            self._record_observation(
                role_name,
                model,
                status="complete",
                request_id=request_id,
                final_model=result.model,
                provider=result.provider,
                latency_ms=int((time.perf_counter() - started) * 1000),
                cost_usd=self._usage_cost(result.usage),
            )
            if guardrail and not generic and self.settings.otari_guardrail_profile:
                verdict = result.raw.get("_raft_observed_headers", {}).get("x-otari-guardrails")
                prior = self.db.fetch_one(
                    "SELECT status FROM feature_evidence WHERE feature='Guardrails'"
                )
                self.db.record_feature(
                    "Guardrails",
                    (
                        "pass_and_block_observed_log_pending"
                        if prior and "block" in prior["status"]
                        else "safe_pass_observed_log_pending"
                    ),
                    request_id,
                    f"A guarded request completed; response verdict header={verdict!r}.",
                )
            if result.model and result.model != model:
                self.db.record_feature(
                    "Routing",
                    "fallback_observed_log_pending",
                    request_id,
                    f"Observed final model {result.model} after requesting route primary {model}.",
                )
            return result, parsed
        except (httpx.HTTPError, ValidationError, json.JSONDecodeError, OtariError) as error:
            self._record_observation(
                role_name,
                role.primary,
                status="failed",
                request_id=getattr(error, "request_id", request_id),
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=str(error),
            )
            if (
                isinstance(error, OtariError)
                and error.status_code == 403
                and guardrail
                and "guardrail" in str(error).casefold()
            ):
                prior = self.db.fetch_one(
                    "SELECT status FROM feature_evidence WHERE feature='Guardrails'"
                )
                self.db.record_feature(
                    "Guardrails",
                    (
                        "pass_and_block_observed_log_pending"
                        if prior and "pass" in prior["status"]
                        else "block_observed_log_pending"
                    ),
                    error.request_id,
                    f"Otari blocked a guarded request: {error}",
                )
            if isinstance(error, OtariError):
                raise
            raise OtariError(str(error), request_id=request_id) from error

    @staticmethod
    def _usage_cost(usage: dict[str, Any]) -> float | None:
        for key in ("cost", "cost_usd", "total_cost", "total_cost_usd"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    def _record_observation(
        self,
        role: str,
        requested_model: str,
        *,
        status: str,
        request_id: str | None = None,
        final_model: str | None = None,
        provider: str | None = None,
        latency_ms: int | None = None,
        cost_usd: float | None = None,
        error: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO model_observations(
              role,requested_model,final_model,provider,request_id,latency_ms,cost_usd,status,error,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                role,
                requested_model,
                final_model,
                provider,
                request_id,
                latency_ms,
                cost_usd,
                status,
                error,
                utc_now(),
            ),
        )


class SandboxClient:
    """Deterministic aggregation.

    The PRD is explicit that no model may count. Raft therefore owns the Python
    source and only chooses where it runs: a directly-opened hosted Otari
    sandbox session when credentials exist, and this process otherwise. Both
    paths execute the *same* string that "Show the work" displays, so the code
    on screen is the code that produced the number.
    """

    def __init__(self, settings: Settings, otari: "OtariClient | None" = None):
        self.settings = settings
        self.otari = otari

    async def aggregate(
        self,
        rows: list[dict[str, Any]],
        api_key: str | None,
        *,
        code: str,
    ) -> dict[str, Any]:
        if (
            self.settings.raft_otari_mode != "live"
            or self.settings.raft_llm_provider != "otari"
            or not api_key
            or not self.otari
        ):
            # Only hosted Otari exposes a sandbox; everywhere else the same
            # source runs in this process and the answer says which happened.
            return self.local_reference(rows, code)
        try:
            return await self._hosted_sandbox(rows, api_key, code)
        except OtariError as error:
            if self.otari:
                self.otari.db.record_feature(
                    "Code Execution",
                    "live_sandbox_failed_log_pending",
                    error.request_id,
                    f"Direct sandbox aggregation failed and Raft fell back to local execution: {error}",
                )
            reference = self.local_reference(rows, code)
            reference["execution"] = "local_reference"
            return reference

    async def _hosted_sandbox(self, rows: list[dict[str, Any]], api_key: str, code: str) -> dict[str, Any]:
        base = self.settings.otari_platform_base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = json.dumps(rows, separators=(",", ":"))
        chunks = _chunk(payload, self.settings.otari_sandbox_chunk_bytes)
        session_id: str | None = None
        request_id: str | None = None
        async with httpx.AsyncClient(timeout=self.settings.otari_request_timeout_seconds) as client:
            created = await client.post(f"{base}/api/v1/sandbox/sessions", headers=headers, json={})
            if created.status_code >= 400:
                raise OtariError(
                    f"Sandbox session creation returned HTTP {created.status_code}: {created.text[:500]}",
                    status_code=created.status_code,
                    request_id=created.headers.get("x-request-id"),
                )
            session = created.json()
            session_id = str(session.get("id") or session.get("session_id") or "")
            request_id = created.headers.get("x-request-id")
            if not session_id:
                raise OtariError("Sandbox session response contained no session id")
            try:
                # The hosted proxy has no upload route, so the table is streamed
                # in through exec calls that stay under the request cap.
                await _exec(client, base, headers, session_id, "INPUT_PARTS = []")
                for chunk in chunks:
                    await _exec(client, base, headers, session_id, f"INPUT_PARTS.append({chunk!r})")
                stdout = await _exec(
                    client,
                    base,
                    headers,
                    session_id,
                    'INPUT_JSON = "".join(INPUT_PARTS)\n' + code,
                )
            finally:
                await client.delete(f"{base}/api/v1/sandbox/sessions/{session_id}", headers=headers)

        line = _last_json_line(stdout)
        if line is None:
            raise OtariError(f"Sandbox stdout did not end in JSON: {stdout[:1000]}", request_id=request_id)
        computed = json.loads(line)
        reference = self.local_reference(rows, code)
        expected = json.loads(reference["stdout_json"])
        if computed != expected:
            raise OtariError(
                f"Sandbox result disagreed with the local reference: {computed} != {expected}",
                request_id=request_id,
            )
        if self.otari:
            self.otari.db.record_feature(
                "Code Execution",
                "live_sandbox_matched_reference_log_pending",
                request_id,
                f"Direct sandbox session aggregated {len(rows)} rows in {len(chunks)} chunk(s) and matched the local reference.",
            )
        return {
            **computed,
            "execution": "otari_code_execution",
            "stdout_json": line,
            "session_id": session_id,
            "request_id": request_id,
        }

    @staticmethod
    def local_reference(rows: list[dict[str, Any]], code: str) -> dict[str, Any]:
        """Run Raft's own aggregation source in this process and read its stdout."""
        buffer = io.StringIO()
        namespace: dict[str, Any] = {"INPUT_JSON": json.dumps(rows, separators=(",", ":"))}
        with redirect_stdout(buffer):
            exec(compile(code, "raft_aggregation.py", "exec"), namespace)  # noqa: S102 - Raft-authored source only
        line = _last_json_line(buffer.getvalue())
        if line is None:
            raise OtariError("Local aggregation produced no JSON on stdout")
        computed = json.loads(line)
        return {
            **computed,
            "execution": "local_reference",
            "stdout_json": line,
            "session_id": None,
            "request_id": None,
        }


async def _exec(client: "httpx.AsyncClient", base: str, headers: dict[str, str], session_id: str, source: str) -> str:
    response = await client.post(
        f"{base}/api/v1/sandbox/sessions/{session_id}/exec",
        headers=headers,
        json={"code": source},
    )
    if response.status_code >= 400:
        raise OtariError(
            f"Sandbox exec returned HTTP {response.status_code}: {response.text[:500]}",
            status_code=response.status_code,
            request_id=response.headers.get("x-request-id"),
        )
    body = response.json()
    if body.get("stderr"):
        raise OtariError(f"Sandbox exec wrote to stderr: {str(body['stderr'])[:500]}")
    return str(body.get("stdout") or "")


def _chunk(payload: str, size: int) -> list[str]:
    return [payload[start : start + size] for start in range(0, len(payload), size)] or [""]


def _last_json_line(stdout: str) -> str | None:
    for line in reversed([item.strip().strip("`") for item in stdout.splitlines() if item.strip()]):
        if line.startswith("{") and line.endswith("}"):
            try:
                json.loads(line)
            except json.JSONDecodeError:
                continue
            return line
    return None


async def check_models(settings: Settings, roles: dict[str, ModelRole]) -> dict[str, Any]:
    """Ask the platform which models this workspace can actually call.

    A model in the published catalog is not proof that a given workspace key can
    reach it. Running this before a demo turns a mid-demo 404 into a line in
    Settings, which is the whole point of checking.
    """
    if settings.raft_otari_mode != "live":
        return {
            "checked": False,
            "reason": "RAFT_OTARI_MODE is not live, so no credential exists to check.",
            "roles": [],
        }

    base = (
        settings.raft_llm_base_url
        if settings.raft_llm_provider == "openai_compatible" and settings.raft_llm_base_url
        else settings.otari_platform_base_url
    ).rstrip("/")

    seen: dict[str, tuple[bool, set[str] | None, str | None]] = {}
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for name, role in roles.items():
            api_key = os.getenv(role.api_key_env) or settings.raft_llm_api_key
            if not api_key:
                results.append(
                    {"role": name, "ok": False, "detail": f"{role.api_key_env} is not set", "available": None}
                )
                continue
            if api_key not in seen:
                try:
                    response = await client.get(
                        f"{base}/api/v1/models", headers={"Authorization": f"Bearer {api_key}"}
                    )
                    if response.status_code >= 400:
                        seen[api_key] = (False, None, f"HTTP {response.status_code}: {response.text[:200]}")
                    else:
                        body = response.json()
                        entries = body.get("data") if isinstance(body, dict) else body
                        names = {
                            str(item.get("id") or item.get("name"))
                            for item in (entries or [])
                            if isinstance(item, dict)
                        }
                        seen[api_key] = (True, names, None)
                except (httpx.HTTPError, json.JSONDecodeError, ValueError) as error:
                    seen[api_key] = (False, None, str(error))
            reachable, names, detail = seen[api_key]
            if not reachable:
                results.append({"role": name, "ok": False, "detail": detail, "available": None})
                continue
            wanted = [role.primary, *role.fallbacks]
            missing = [model for model in wanted if names is not None and _model_absent(model, names)]
            results.append(
                {
                    "role": name,
                    "ok": not missing,
                    "detail": ("all configured models are available" if not missing else f"missing: {', '.join(missing)}"),
                    "available": len(names or ()),
                }
            )
    return {"checked": True, "base_url": base, "roles": results}


def _model_absent(model: str, names: set[str]) -> bool:
    """Match a configured id against the catalog, ignoring a provider prefix."""
    bare = model.split(":", 1)[-1]
    return not any(candidate == model or candidate.split(":", 1)[-1] == bare for candidate in names)
