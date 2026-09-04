# Raft Otari integration log

This file is competition evidence, not marketing copy. Every entry is written after a live
attempt against hosted Otari, and every number in it was observed on the wire — request IDs,
status codes, latencies and costs are pasted, not projected. Where a feature never produced a
successful response, the entry says so and records what the gateway returned instead; that
absence is part of the evidence, not a gap in it. Where a field could not be observed yet, it
is marked **not observed** with the reason.

## Environment

- Date started: 21 August 2026; live integration 24 August 2026; submission 4 September 2026
- Raft runtime: FastAPI + SQLite in one container, React front end, dark desktop UI
- Otari mode: `RAFT_OTARI_MODE=live`, `RAFT_OTARI_DEPLOYMENT=hosted`
- Generation base URL: `https://api.otari.ai` (chat completions under `/v1`)
- Platform base URL: `https://app.otari.ai` (management routes under `/api/v1`)
- Credentials: one workspace token per model role, six roles, see `model-roles.yaml`
- Public application URL: https://raft-production.up.railway.app (Railway, single container, persistent `/data` volume)
- Public MCP URL: **not registered yet** — the server needs a public origin, which exists as of the Railway deployment; registration with Otari is the next step and is recorded under MCP Servers when it happens
- External tester: **not run yet** — scheduled for 5 September 2026 with `tester-script.md`; the section below stays empty until a real person has done it

## Live-call gate

- Schema-validated request against a live model: **passed 24 August 2026** — see "Live run" below for call counts, latencies and sample request IDs (`chatcmpl-ea5694ca-…` on Hermes-4-70B, `chatcmpl-556ff956-…` on gpt-oss-120b)
- Manual 20-trace labelling review (`manual-label-review.md`): **not run** — the demo corpus was labelled by the local path, so the trace-labeler role has no live evidence to review yet. See "Open-weights model evidence" for exactly which roles have been observed answering.

## Guardrails

> I used feature **Guardrails** to achieve a prompt-injection and privacy checkpoint over
> untrusted trace text before it reaches a model. I found the request shape, the absence of a
> second endpoint, and the absence of a separate SDK easy: guardrails are one
> `guardrails: [{"profile": ..., "mode": "block"}]` array on the chat body I was already
> sending, so turning the feature on cost one `if` in `raft/otari.py`. I struggled with the
> failure mode, the undiagnosable status code, and the missing verdict, because a deployment
> with no guardrails service answers `502 guardrail profile 'prompt-injection' requested but
> no guardrails service is configured` and fails the *entire* completion rather than the
> optional layer; because a 502 cannot be told apart from "temporarily down", so failing
> closed was not safe either; and because I never once saw a successful verdict, so I still
> do not know what a pass or a block actually looks like on the wire. Raft therefore drops
> the guardrail block, retries once, and records the feature as unavailable.

- Status: **integrated and attempted live; hosted Otari has no guardrails service, so no verdict has ever been observed**
- Date/workspace: 24 August 2026 / every role workspace (the block is attached to every request)
- Endpoint/profile: `POST /v1/chat/completions` with `guardrails: [{"profile": "prompt-injection", "mode": "block"}]`
- Exact error, every guarded request: `502 guardrail profile 'prompt-injection' requested but no guardrails service is configured`
- Request ID of a blocked or passed verdict: **not observed** — the gateway fails the whole completion before any verdict exists
- Important distinction: Raft's deterministic redactor performs rewriting. Otari Guardrails judge or block the already-redacted input; they do not sanitize it.
- Safe vs adversarial request evidence: **not observed** — both shapes receive the same 502 on this deployment
- User-visible result: every trace detail page shows `guardrail_status` as its own field, separate from redaction status, so a trace is never shown as screened when it was not
- Workaround and owner: Raft drops the `guardrails` block and retries once, recording the feature as unavailable (`raft/otari.py`). Owner: arguably Otari — a missing optional service should degrade, not 502 the request.
- Reproducible steps: `python scripts/live_smoke.py guardrails` with `OTARI_GUARDRAIL_PROFILE=prompt-injection` set
- Honest verdict: the integration is one `if`; the feature has never once run for us, and I still do not know what a pass or a block looks like on the wire

## Routing

> I used feature **Routing** to achieve six role-specific model tiers behind one base URL and
> one credential. I found role assignment, provider abstraction, and final-model reporting
> easy: swapping a 30B for a 70B for a whole role is a one-line `model-roles.yaml` edit with
> no code change, every provider is reachable through the same `/v1/chat/completions`, and the
> response's own `model` field let me show the user which model really answered. I struggled
> with catalog trust, deployment-dependent paths, unnormalised parameters, and unadvertised
> latency, because a model id from the published catalog is not proof a given key can reach
> it (I wrote `scripts/otari_setup.py` purely to discover per-key reachability before a demo
> instead of during one); because management routes live at `/v1/models` on standalone and
> `/api/v1/models` on hosted, which made every model look missing until I added a
> `raft_otari_deployment` switch; because per-model parameter incompatibility arrives as an
> undifferentiated HTTP 400 (`temperature` "deprecated for this model"), so the gateway
> passes provider quirks straight through and I had to write a retry-minus-one-parameter
> loop; and because same-size models differ ~4x in latency (Hermes-4-70B 2.5 s median versus
> Llama-3.3-70B 12–20 s and one 90 s timeout) with nothing in the API to hint it, so I had to
> measure it myself and give the naming call its own 20 s leash. No provider-level fallback
> ever fired, so Raft's "fallbacks" are its own retries, not Otari routing.

- Status: **used live, 24 August 2026**
- Date/workspace: 24 August 2026 / `planner`, `aspect-evaluator`, `cluster-namer` observed; `trace-labeler`, `autopsy-writer`, `span-explainer` configured, see Open-weights evidence for what has been observed per role
- Endpoint: `POST /v1/chat/completions`, one base URL and one credential per role
- Role policies: `model-roles.yaml`; a role's primary and fallbacks are reported on Settings next to the observed final model
- Observed final model: matched the requested primary on every successful call (Raft asserts this and logs a warning otherwise). No provider-level fallback ever fired — Raft's fallbacks are its own retries, not Otari routing.
- Sample request IDs: `chatcmpl-ea5694ca-61b3-4a76-af77-9a8c04f30ae1` (Hermes-4-70B), `chatcmpl-556ff956-8374-423a-a88b-8d8fa8586a28` (gpt-oss-120b)
- Exact errors and dead ends: bare catalog ids (`openai/gpt-oss-120b`) 404 on hosted — every reachable id carries the `mzai:` prefix; management routes are `/v1/models` on standalone and `/api/v1/models` on hosted; `temperature` returns HTTP 400 "deprecated for this model" on newer Anthropic models; Llama-3.3-70B measured 12–20 s per naming call with one 90 s timeout against Hermes-4-70B at 2.5 s median on the same payload
- Observed latency by role: planner 2.6 s avg / 3.9 s max; aspect evaluator 2.5 s avg / 11.3 s max; cluster namer 9.8 s avg / 90.2 s max (full table under "Live run")
- Raft-side fallback, added 4 September after a model vanished: on a 404 whose body says "does not exist", the client now walks `[primary, *fallbacks]` and records `Routing / raft_side_fallback_log_pending` with the text "{model} does not exist on this deployment; Raft advanced to {next}". Driven end to end in `tests/test_judge.py` against a stubbed transport that 404s the primary and serves the fallback. This is Raft routing around Otari, not Otari routing — the gateway itself never rerouted a request for us.
- A false positive, removed the same day: the gateway echoes `"model": "Qwen/…"` for a request that named `mzai:Qwen/…`, and a literal comparison had been recording that as a reroute (see "Smaller friction").

## MCP Servers

> I used feature **MCP Servers** to achieve read-only planner access to Raft's redacted trace
> table. I found the client-side wiring, the server-side surface, and the tool schemas easy:
> passing `mcp_server_ids` is one more field on the chat body, and exposing five read-only
> tools took a 154-line JSON-RPC endpoint (`raft/mcp.py`) with no MCP library. I struggled
> with reachability, out-of-band registration, and unobservable tool use, because the server
> must be publicly reachable — so a product running on `localhost:8010` cannot use the
> feature at all without standing up a tunnel first, which is the single biggest blocker here;
> because registration happens outside the request that uses it, so the thing I need at
> inference time is state I have to have arranged earlier through a different surface; and
> because the response says nothing about which tools were actually invoked, so I can neither
> verify the call happened nor show the user honestly — Raft's progress panel can only relay
> the planner's own claim about what it inspected. Net result: no recorded MCP tool call, and
> the deterministic local question compiler in `raft/query.py` is what ships.

- Status: **server implemented and tested locally; not yet registered with Otari, so no MCP tool call has been observed**
- Date/workspace: 4 September 2026 (public origin available) / `planner`
- Endpoint: `POST /mcp` (JSON-RPC, Streamable-HTTP compatible), to be passed to Otari as `mcp_server_ids`
- Tools: `describe_dataset`, `list_shape_fields`, `list_aspects`, `get_representative_traces`, `get_trace_rows` — all read-only over redacted rows
- Planner request/tool-call evidence: **not observed** — registration is out-of-band and requires the public URL that only exists as of today's deployment
- Public MCP URL: same origin as the application, path `/mcp`; registration recorded here when it happens
- What the user sees: the Answer progress panel states which MCP tools the planner *reports* inspecting — Raft cannot verify this, because the completion response does not say which tools were invoked
- Workaround and owner: the deterministic local question compiler (`raft/query.py`) plans the query when the planner is unavailable, and every Answer page names which planner ran. Owner: Otari for the unobservable tool use; Raft for the localhost limitation.
- Reproducible steps: `curl -X POST <origin>/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'` lists the five tools; `tests/test_mcp.py` covers the endpoint
- Honest verdict: the wiring is one field and 154 lines; the value — a planner that inspects the real schema before choosing a path — is unproven until a tool call is recorded

## Budgets

> I used feature **Budgets** to achieve a cost ceiling a user can trust before authorising a
> run over 847 conversations. I found having nothing to integrate easy: the ceiling lives on
> the API key, so it is enforced server-side and no client bug can spend past it — that is
> the right place for it. I struggled with granularity, missing cost in `usage`, no
> pre-flight check, and never seeing the rejection, because the only shape available is a
> per-key hard stop while a product needs a per-*run* ceiling, so I built the estimate,
> confirmation, checkpoint, pause and resume myself and Otari is only the outer backstop;
> because the `usage` object on these models carries no cost field, so Raft prices its own
> calls from a local table and cannot show the user Otari's number; because there is no "would
> this run fit in the remaining allowance" call, so my "About \$0.06" confirmation is my
> arithmetic rather than the gateway's; and because I never provoked the 403, so
> `test_budget_pause_preserves_rows_and_resumes` exercises Raft's ceiling, not Otari's.

- Status: **Otari per-key ceiling in place as the backstop; Raft's per-run estimate, confirmation, checkpoint, pause and resume implemented and tested; the Otari 403 has not been provoked**
- Date/workspace: 24 August 2026 / `aspect-evaluator`
- Endpoint: `POST /v1/chat/completions`; the ceiling lives on the role's Otari API key
- Confirmation UX: "About $0.06 and 35–55 seconds. Run it?" — the estimate is Raft's arithmetic from a local price table, because `usage` on these models carries no cost field and there is no pre-flight allowance call
- Observed cost of one full run: $0.0593 for 847 evaluations against a $0.10 local run allowance (24 August, "which language do my clients speak?")
- Budget rejection request ID/error: **not observed** — the run stayed under the key ceiling
- Resume evidence: Raft's own pause/resume is exercised by `test_budget_pause_preserves_rows_and_resumes`; an Otari-triggered pause is **not observed**
- Important distinction: the per-run estimate and allowance are Raft features; Otari provides the aggregate API-key hard stop
- What the user sees: confirmation before execution; a 403 produces `paused_budget`, saves completed rows, and suppresses partial totals
- Reproducible steps: lower the Otari role budget below one run's cost, run an aspect, raise it, resume
- Honest verdict: the right primitive is in the right place, but it is one hard stop per key, and everything a product needs on top of it — estimate, confirm, pause, resume — had to be built client-side

## Code Execution

> I used feature **Code Execution** to achieve deterministic counts, because the product's
> hard rule is that no model may count. I found the session model and keeping ownership of the
> code easy: create / exec / delete is a small honest API, and it let Raft own the Python
> source so the code shown in "Show the work" is literally the string that produced the
> number. I struggled with availability, getting data *in*, and two competing shapes for one
> feature, because hosted Otari answers `503 code execution backend is not configured
> (SANDBOX_BACKEND_URL unset)`; because there is no upload route, so the only way to get an
> 847-row table into a session is to stream it through `exec` calls as string chunks under
> the 2 MiB request cap and `"".join` them back — a data-plane workaround for a missing
> data-plane primitive, and it is the ugliest code I wrote against Otari; and because the
> feature exists both as a declared tool (`tools: [{"type":"otari_code_execution"}]`) and as
> directly-driven sessions with no guidance on which is authoritative, so I implemented the
> one I could reason about. Aggregation therefore runs in-process, and the UI names which
> path executed rather than implying the sandbox.

- Status: **attempted live; hosted Otari has no sandbox backend, so aggregation runs in-process on the same source the UI shows**
- Date/workspace: 24 August 2026 / `aspect-evaluator`
- Endpoint/tool: `POST /api/v1/sandbox/sessions` (session API) and `tools: [{"type":"otari_code_execution"}]` (declared tool) — two shapes for one feature
- Exact error: `503 code execution backend is not configured (SANDBOX_BACKEND_URL unset)` on session creation
- Sandbox session/request ID: **not observed** — no session was ever created
- Input row count/stdout JSON: local execution only; "Show the work" shows the fixed Python, input row count, denominator, exclusions, execution mode and final stdout JSON, and names the execution mode as local
- Local comparison against the sandbox: **not observed** for the same reason
- Workaround and owner: `raft/otari.py` falls back to executing the identical source in-process; the data-plane workaround (streaming rows through `exec` as string chunks under the 2 MiB cap) is implemented for the day the backend exists. Owner: Otari for the missing backend and the missing upload route.
- Reproducible steps: `python scripts/live_smoke.py code-execution`
- Honest verdict: the session API is small and honest, but on hosted Otari the feature does not exist, and the product's central guarantee — no model counts — is delivered by Raft's own execution

## Web Search Enablement

> I used feature **Web Search Enablement** to achieve an on-demand explanation of provider
> error codes a user has never seen before. I found the declaration easy: it is one entry in
> `tools`, identical in shape to code execution, so there was nothing to learn twice. I
> struggled with having no successful response at all, and therefore with citations and with
> honest labelling, because I never observed the tool return — so I cannot say whether
> citations come back, and a citable source is the entire user-facing value of looking
> something up rather than asking a model to recall it; and because an unverifiable feature
> cannot be presented as if it worked, the local fallback ships a six-code reference table
> that states outright "no web search was performed". This is the feature I have the least to
> say about, which is itself the finding.

- Status: **declared and attempted; no successful search response has ever been observed**
- Date/workspace: 24 August 2026 / `planner`
- Endpoint/tool: `POST /v1/chat/completions` with `tools: [{"type":"otari_web_search"}]`
- Provider error used for the lookup: any `error_code` on a span; the local reference (`ERROR_REFERENCE` in `raft/analysis.py`) covers six codes — `provider_model_overloaded_529`, `provider_rate_limit_429`, `gateway_upstream_timeout_504`, `upstream_context_length_exceeded`, `provider_content_filter_451`, `tool_upstream_error` — and says so when a code is outside it
- Search request ID/result shape/citations: **not observed** — the tool has not returned a result on this deployment
- What the user sees: provider-error spans expose "Look this up"; when no search ran, the panel states outright "no web search was performed" and shows the local six-code reference
- Workaround and owner: local reference table (`raft/analysis.py`). Owner: unclear — I cannot tell from the response whether the tool is unavailable or declined to run, and that ambiguity is itself the finding.
- Reproducible steps: `python scripts/live_smoke.py web-search`
- Honest verdict: declaring the tool is one line; without an observable result or citation there is nothing to show a user, and Raft says so rather than implying a lookup happened

## Open-weights model evidence

Observed on hosted Otari. "Live request ID" is a real `chatcmpl-…` id from the named date; a role
with none has never completed a live call, and the table says so rather than implying it.

| Role | Model | Job | Live request ID | Observed | Notes |
|---|---|---|---:|---|---|
| Trace labeler | `mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Layer 1/2 labelling | `chatcmpl-b1c7d83c-c1ae-4d2f-a2f3-befc56d899fa` | 4 Sep 2026, schema-valid, 7 s | Reasoning model: emits `reasoning` before `content`; needs a generous `max_tokens` or it returns `content: null` at `finish_reason: length` |
| Aspect evaluator | `mzai:Qwen/Qwen3-30B-A3B-Instruct-2507` | Layer 3 evaluation | `chatcmpl-839e37d7-b201-4827-afbd-6290e3c87aaf` | 24 Aug (1,683 ok / 11 failed, 2.5 s avg) and 4 Sep | The workhorse; see the concurrency finding below |
| Cluster namer | `mzai:NousResearch/Hermes-4-70B` until 4 Sep; now `mzai:NousResearch/Hermes-4-405B` | Emergent cluster names | `chatcmpl-ea5694ca-61b3-4a76-af77-9a8c04f30ae1` (70B, 24 Aug); `chatcmpl-b756be6b-5f2c-4bf6-a72a-071291472e7f` (405B, 4 Sep) | 24 Aug: 21 ok / 4 failed, 2.5 s median. **4 Sep: `404 The model NousResearch/Hermes-4-70B does not exist`**, then 2.1 s on the 405B | The 70B left the catalog between the two dates with no notice; both roles that used it were dead until repointed, see the 4 September section |
| Autopsy writer | same as cluster namer | Trace autopsy | `chatcmpl-3d445048-45f3-47da-8afc-4b097ab105bc` (405B, 4 Sep) | 4 Sep: 404 on the 70B, then 5.1 s on the 405B | Repointed with the cluster namer |
| Span explainer | `mzai:google/gemma-3-27b-it` | Explain one span | `chatcmpl-24854996-8908-4133-9b39-aa7f21d6515b` | 4 Sep 2026, 3.1 s | |
| Planner | `mzai:openai/gpt-oss-120b` | Path selection, aspect definition, web lookup | `chatcmpl-556ff956-8374-423a-a88b-8d8fa8586a28` (24 Aug), `chatcmpl-3e245cae-d68d-443a-a43b-8db09aab317f` (4 Sep) | 24 Aug: 41 ok / 2 failed, 2.6 s avg; 4 Sep: 1.8 s | Reasoning model, same `content: null` behaviour as Nemotron |

Six roles, six open-weights models configured, and as of the evening of 4 September every one of
the six roles has completed a live call with a request id (Nemotron-3-Nano, Qwen3-30B,
Hermes-4-405B, Gemma-3-27b, gpt-oss-120b; Hermes-4-70B before its removal; Llama-3.3-70B measured
as the naming fallback). `GET /api/settings/preflight` reports `ok: true, "all configured models
are available"` for all six against 376 catalog models.

## External tester

Not run as of 4 September 2026. A colleague is scheduled to walk `tester-script.md` against the
public URL on 5 September; their initials, browser, completion time, confusion points and the
fixes made afterwards will be recorded here. The fields are left out rather than filled with
placeholders, because a tester section with no tester in it is the kind of claim this log exists
to avoid.

## Live run, 24 August 2026

Raft was connected to hosted Otari (`https://api.otari.ai`, management routes
under `/api/v1`, generation under `/v1`) with a workspace token. Everything
below is observed, not projected.

| Role | Model | Calls | Avg latency | Max |
|---|---|---:|---:|---:|
| planner | `mzai:openai/gpt-oss-120b` | 41 ok, 2 failed | 2.6 s | 3.9 s |
| aspect_evaluator | `mzai:Qwen/Qwen3-30B-A3B-Instruct-2507` | 1683 ok, 11 failed | 2.5 s | 11.3 s |
| cluster_namer | `mzai:NousResearch/Hermes-4-70B` | 21 ok, 4 failed | 9.8 s | 90.2 s |

Sample request IDs: `chatcmpl-ea5694ca-61b3-4a76-af77-9a8c04f30ae1`
(Hermes-4-70B), `chatcmpl-556ff956-8374-423a-a88b-8d8fa8586a28` (gpt-oss-120b).

**One full Layer-3 run:** "which language do my clients speak?" became a
`category` aspect with the answer set `[English, Spanish, French, German,
Other]` and was evaluated against all 847 conversations — **English 845
(99.8%), Other 2** — in 188 s at a concurrency of 12, costing $0.0593 of the
$0.10 local run allowance. Asking it again is free: the aspect is cached.

### Friction, exactly as encountered

1. **Guardrails are not configured on this deployment.** Every guarded request
   returned `502 guardrail profile 'prompt-injection' requested but no
   guardrails service is configured`, which failed the *whole* call rather than
   degrading. Raft now drops the guardrail block and retries once, recording the
   feature as unavailable. Owner: arguably Otari — a missing optional service
   should not 502 the request.
2. **Reasoning models return `content: null`.** `gpt-oss-120b` and Nemotron Nano
   emit a `reasoning` field first; with a small `max_tokens` the budget is spent
   thinking and `content` stays null with `finish_reason: length`. Raft treats
   an empty completion at length as an error rather than parsing the literal
   string "None". Owner: Raft.
3. **`temperature` is rejected by newer Anthropic models** ("deprecated for this
   model", HTTP 400). Raft now retries once with the offending optional
   parameter removed.
4. **Cluster-namer latency is wildly variable.** Llama-3.3-70B measured
   12–20 s and once timed out at 90 s; Hermes-4-70B measured 2.5 s median on the
   same payload. Naming is decoration, so it now has its own 20 s budget and
   falls back to Raft's own group names.
5. **No sandbox on hosted Otari:** `POST /api/v1/sandbox/sessions` returns
   `503 code execution backend is not configured (SANDBOX_BACKEND_URL unset)`.
   Aggregation runs locally, executing the same source it displays.
6. **No `/v1/embeddings` on hosted** (404), as the PRD predicted. Standalone
   Otari does mount it. Embeddings remain local.
7. **A mid-run 502 (`Authorization service unavailable`) corrupted an answer
   once.** The fallback finished a `category` run with the boolean local judge,
   producing `English 768, No 76, Yes 1` — two scales in one total. Fixed: a
   typed aspect now excludes unjudged rows and declares the count instead of
   guessing them.

## Live smoke, 4 September 2026

Every role and every feature adapter was exercised again on submission day against
`https://api.otari.ai` with `scripts/live_smoke.py`, and the flagship question was walked in the
browser. Results, exactly as returned:

| Probe | Result |
|---|---|
| `single-label` (trace labeler, Nemotron-3-Nano-30B) | OK, `chatcmpl-b1c7d83c-…`, schema-valid, 7 s |
| `all-models` aspect evaluator (Qwen3-30B) | OK, `chatcmpl-839e37d7-…` |
| `all-models` cluster namer, autopsy writer (Hermes-4-70B) | **FAIL** `HTTP 404 {"detail":"Error code: 404 - {'detail': 'The model `NousResearch/Hermes-4-70B` does not exist.'}"}` in 0.9 s, no request id |
| `all-models` span explainer (Gemma-3-27b) | OK, `chatcmpl-24854996-…`, 3.1 s |
| `all-models` planner (gpt-oss-120b) | OK, `chatcmpl-3e245cae-…`, 1.8 s |
| `code-execution` | `POST /api/v1/sandbox/sessions` → 200 with a session id (an improvement on 24 August's 503); `POST …/exec` with `{"code":"print(1+1)"}` → **`502 {"detail":"code execution backend error"}`**, 3 of 3 attempts. Raft recorded `"execution": "local_reference"`. |
| `guardrails` with a profile deliberately set | **`502 {"detail":"guardrail profile 'prompt-injection' requested but no guardrails service is configured. Set OTARI_GUARDRAILS_URL on the gateway or pass `url` on the guardrail entry."}`**; `/api/v1/guardrails`, `/api/v1/guardrail-profiles`, `/api/v1/guardrails/profiles` all 404. Production therefore sends no guardrail block. |
| `web-search` | **`422 {"detail":"Exceeded max_tool_iterations=10"}`** after 28 s — the first evidence that the tool *runs*: the model looped on it ten times and never produced an answer. Still no citation observed. |
| `otari_setup.py` against hosted | 376 models listed; the probe picked `bedrock:nvidia.nemotron-nano-12b-v2` and got `404 … no pricing is configured for it`; `/v1/embeddings` 404 as documented |

### Replacing a model that disappeared

Two roles named `mzai:NousResearch/Hermes-4-70B`, which had answered 21 naming calls on 24 August
and did not exist on 4 September. The two open-weights candidates left in the catalog were
measured on the real cluster-naming payload — 12 clusters, 3 truncated examples and 4 distinctive
terms each, the production system prompt and the strict `ClusterNamingResult` JSON schema,
3,357 bytes — three calls each:

| Model | Call 1 | Call 2 | Call 3 | Schema valid | Sample names |
|---|---:|---:|---:|---|---|
| `mzai:NousResearch/Hermes-4-405B` | 4.01 s | 3.86 s | 3.43 s | 3/3, identical output each time | "Missing Items", "Dry Cleaning Questions", "Flight Bookings" |
| `mzai:meta-llama/Llama-3.3-70B-Instruct` | 25.62 s | 32.55 s | 16.82 s | 3/3, identical output each time | "Damaged Item", "Dry Cleaning", "Flight Booking" |

Llama-3.3-70B exceeds Raft's 20 s naming leash on two calls out of three, so in production it
would silently produce no names most of the time. Hermes-4-405B is 4–9× faster and inside the
leash on every call. Both roles now run Hermes-4-405B primary with Llama-3.3-70B as fallback.
After the change all six roles completed live: trace labeler Nemotron-3-Nano (see the quote-rate
note below), aspect evaluator `chatcmpl-afd9c932-…` 2.1 s, cluster namer `chatcmpl-b756be6b-…`
2.1 s, autopsy writer `chatcmpl-3d445048-…` 5.1 s, span explainer `chatcmpl-3fba2c5d-…` 1.3 s,
planner `chatcmpl-3b0e5730-…` 2.1 s. `check_models` against the *old* roles file returns
`ok: false, "missing: mzai:NousResearch/Hermes-4-70B"` for both roles, so the preflight gate
would have caught the outage had it been run before the demo. Owner: Otari for removing a
catalog model without notice; Raft for not running its own preflight.

**Nemotron-3-Nano and literal quotes.** Four identical trace-labelling requests on 4 September
returned a genuinely literal quote once. The other three: a paraphrase ("exchanged size 10 for
size 12" for an input that says "exchange"), the whole user and assistant turns concatenated, and
one sentence repeated about six times to 500 characters. Raft verifies every quote as a literal
substring and drops it otherwise, so nothing fabricated reaches the screen — but the smoke script
asserts literality and therefore fails three times in four. The product is right and the script
is stricter than the product; both are left as they are, and the rate is recorded here.

### The finding that changed the product on submission day

The flagship question ("How many users received a product suggestion?") routed correctly to a
new aspect, estimated "About $0.07 and 54–141 seconds", and completed 847/847 in 28 s — with the
wrong answer. The run's own method notes said: *Otari became unavailable partway through (Otari
returned HTTP 502: `{"detail":"Authorization service unavailable"}`); Raft finished locally.* Row
provenance: **24 rows judged by Qwen3-30B, 823 by `local:semantic-judge-v1`.** The headline read
"Yes — 16 of 847 (1.9%)" and quoted *"Query the production database for yesterday's failed
jobs"* as evidence of a product suggestion.

The 502 was reproduced directly against the gateway with identical trivial completions:

| Concurrent requests | Trial 1 | Trial 2 |
|---:|---|---|
| 4 | 4 × 200 | — |
| 12 | 12 × 200 | 12 × 200 (and a third trial, clean) |
| 16 | 8 × 200, 8 × 502 | 16 × 200 |
| 20 | 20 × 502 | 12 × 200, 8 × 502 |
| 24 | 24 × 200 | 17 × 200, 7 × 502 |

It is not a ceiling; the authorization service fails intermittently under burst from 16 up, and
was clean at 12 in every trial. Raft ran at 24. Two hours later the same probe told a different
story — the deployment degraded during the session:

| In flight | 40-call sweep, ~20:45 CEST | Sustained 120 calls |
|---:|---|---|
| 1 | 40 × 200 (100%), 1.3 calls/s | — |
| 2 | 35 × 200, 5 × 502 (87.5%) | — |
| 3 | 33 × 200, 7 × 502 (82.5%) | — |
| 4 | 26 × 200, 14 × 502 (65%) | — |
| 6 | — | 29 × 200, 91 × 502 (24%) |
| 12 | — | 15 × 200, 105 × 502 (12.5%) |

Strictly serial, 15 calls 0.5 s apart: 15/15 succeeded, and `GET /api/v1/models` stayed 200 — the
gateway is up; its authorization service falls over under any concurrency. Any concurrency figure
measured against this deployment has a shelf life of hours. Two changes followed.
`RAFT_ASPECT_CONCURRENCY` dropped from 24 to 12 on the first measurement and then to **3** on the
second: with four attempts per row, 82.5% per-call success leaves an expected 0.1% of rows unjudged
over 847, where 4 in flight would leave ~1.5% and a pause on most runs. And the boolean aspect path stopped substituting the local judge mid-run: a 5xx is
now retried three times (1 s / 3 s / 8 s, inside the concurrency semaphore, so a bad patch costs
up to ~12 s of wall clock as deliberate backpressure), and a row that still fails stays unjudged
while the run pauses as `paused_provider` with every judged row saved and a Resume button —
the same shape as a budget stop. Under the degraded gateway this fired for real on the flagship
question: the run paused at 48 of 847 with the message *"Otari stopped answering after 48 of 847
conversations (Otari returned HTTP 502: {"detail":"Authorization service unavailable"}). Resume
once it recovers."* — all 48 rows judged by Qwen3-30B, zero local rows, $0.0034 spent, no answer
written, and the run resumed from row 49 at lower concurrency. The 24 August fix (friction item 7) had already made *category*
aspects answer over the judged set and declare the remainder excluded, and a test pins that
decision; the boolean path was the one still blending two scales, because the local yes/no judge
was the only second scale that existed. Owner: Otari for the flaky 502; Raft for having papered
over it on one of the two paths. The visible trade: if Otari is down at demo time, the flagship
question now ends on a pause card instead of a wrong number.

### The flagship question, proven end to end

After the concurrency and pause changes, the same chip — "How many people are getting product
suggestions?" — was run again from an empty aspect cache. It paused once at 48 of 847 when the
gateway 502'd, resumed at 2 in flight, and completed: **847 of 847 judged by
`Qwen/Qwen3-30B-A3B-Instruct-2507`, zero rows by the local judge, 847 of 847 evidence quotes
non-empty and verified literal**, 799 conversations in 779 s, $0.0593 of the $0.10 run
allowance. Headline **"Yes — 8 of 847 (0.9%)"**; the Yes group is coherent — all eight in
`shopping-assistant`, all used `search_catalog`, all resolved — with quotes such as *"Which shoes
would you recommend for cycling to work?"*. The morning's poisoned run had answered the same
question with 16 and quoted a database-log request as evidence. The honest answer is also the
thinner one: with 8 of 847 no group behaves unlike the rest, so "What stands out" is empty.

### The clustering question, on a live cluster namer

With the naming roles repointed, "What do my users struggle with most?" (emergent clustering, no
taxonomy shipped) was asked again. Morning, namer dead: headline *"There is a stain on the coat
straight out of the bag. — 23 of 285 (8.1%)"* — the medoid's own sentence standing in for a
name. Evening, Hermes-4-405B naming: **"Damaged or incomplete orders — 25 of 44 (56.8%)"**, groups
Damaged or incomplete orders 25 · Migration runner test failures 7 · Price matching requests 6 ·
Duplicate billing issues 5 · Shoe recommendations for standing 1, and the standout *"25 of 25 fail
the same way — 'tool call failed'. This is one bug, not 25 bad conversations."* The counts did not
change with the namer; only the names did, and the method notes now say exactly that. The planner
narrowed harder on the second run (four recorded filters, then a semantic narrowing to 44 of 208
matched), which is planner nondeterminism, not a code change; the page shows "44 conversations
counted · 803 outside this question" so the narrowing is visible rather than hidden.

### Smaller friction found the same evening

- **The gateway echoes the model without its own prefix.** A request for `mzai:Qwen/Qwen3-30B-A3B-Instruct-2507` comes back with `"model": "Qwen/Qwen3-30B-A3B-Instruct-2507"`. Raft compared the two strings literally, decided a reroute had happened, and printed *"Routing fallback: mzai:Qwen/… → Qwen/…"* in the live progress line for the whole of every aspect run, and recorded it as Routing evidence. No reroute ever occurred. Fixed by normalising the prefix before comparing (`route_key`/`same_model` in `raft/otari.py`); the earlier Routing evidence rows were discarded. Owner: Raft, though a gateway that echoes a different id than it accepted invites exactly this.
- **`live_smoke.py all-models` aborts on the first role that raises**, so one dead role hid the state of the roles after it. Roles were smoked individually; the preflight endpoint is the gate, not the smoke script.
- **A cluster-naming failure printed a timeout it never observed.** The failure note said "did not answer within 20s" for what was a 404 in under a second, and the success note ("Group names written by the cluster-namer model") sat in the failure branch, so it printed on every failure and never on success. Both fixed; the note now carries the exact error text. Owner: Raft.

## What runs without Otari, and how well

Raft is fully functional with no credentials configured. That is deliberate: a
demo nobody can run is not evidence of anything. This section records exactly
what substitutes for each Otari feature when it is absent, so nothing above is
read as a claim about a path that did not execute.

| Otari feature | Local substitute when unconfigured | Honest gap |
|---|---|---|
| Planner + MCP | Deterministic question compiler (`raft/query.py`) | It reads metrics, groupings, filters, time windows and dataset values. It has no world knowledge, so a question relying on a synonym the corpus never uses routes on its residual focus rather than understanding it. |
| Code Execution | The same Python source executed in-process | Identical output; only the execution location differs. The UI names which one ran. |
| Aspect evaluation | `local:semantic-judge-v1` (`raft/judge.py`) | Measured mean F1 0.59 — see below. Every surface labels it and ships a 5-yes/5-no verification sample. |
| Cluster naming | The medoid's own request as the name, class-based TF-IDF for recurring wording | Names are real sentences from the group, so they are checkable but not abstracted. A model writes a better *summary* of a group; it does not change the grouping or the counts. |
| Embeddings | Corpus-fitted TF-IDF + truncated SVD | Hosted Otari has no embeddings route regardless, so this is the v1 path in both modes. |
| Guardrails | Local deterministic redaction only | Otari blocks, it never rewrites; redaction was always Raft's job. No prompt-injection screening happens without Otari. |
| Web Search | Local provider-error reference table | Clearly labelled "no web search was performed". Six known codes; anything else says so. |
| Budgets | Local per-run allowance with pause/resume | Exercised and tested (`test_budget_pause_preserves_rows_and_resumes`), but against Raft's own ceiling, not an Otari 403. |

### Cluster coherence measurement

Grouping is measured the same way, against `intent_key`:

| Method | Cluster purity |
|---|---:|
| Flat k-means over SVD vectors (the first implementation) | 0.35 |
| Mutual-kNN graph over sparse TF-IDF, no merging | 0.92 |
| The same, with average-linkage merging to readable group sizes | 0.88 |

Centroid linkage was tried and rejected: it chains, producing one 81-member
group that absorbed unrelated requests. Average linkage computed exactly from
cluster vector sums does not.

### Aspect judge measurement

`python scripts/eval_judge.py`, against the generated corpus's own intent labels:

| Aspect question | P | R | F1 |
|---|---:|---:|---:|
| Did the user ask about a visa or entry requirement? | 1.00 | 1.00 | 1.00 |
| Did the user ask to cancel an order or subscription? | 0.91 | 0.81 | 0.85 |
| Did the user ask the agent to deploy or open a pull request? | 0.93 | 0.72 | 0.81 |
| Did the user ask how a garment fits? | 0.74 | 0.85 | 0.79 |
| Did the user ask about single sign-on? | 0.70 | 0.47 | 0.56 |
| Did the user report a damaged or missing item? | 1.00 | 0.32 | 0.48 |
| Did the assistant suggest a specific product to the user? | 0.52 | 0.35 | 0.42 |
| Did the user ask about shipping or delivery? | 0.37 | 0.48 | 0.42 |
| Did the user ask about pricing or a discount? | 0.40 | 0.39 | 0.40 |
| Did the user ask for a refund? | 0.24 | 0.10 | 0.14 |

**mean F1 0.587.** The pattern is consistent: it is good where the conversation
states the thing in its own words and poor where the question and the corpus use
different vocabulary for the same idea ("suggest a product" versus "I would take
the Harbor Shell"), or where the distinction is negation — the refund question
scores 0.14 partly because "I did not ask for a refund" contains the word. A language model closes
exactly that gap, which is the honest argument for the Otari path rather than a
claim that the local one is equivalent. Thresholds in `raft/judge.py` were tuned
against this measurement and are guarded by `tests/test_judge.py`.

## OpenAI-compatible fallback provider

`RAFT_LLM_PROVIDER=openai_compatible` points the same client at any Chat
Completions endpoint so live mode is reachable without an Otari workspace. When
it is set, Raft does not send `guardrails`, `mcp_server_ids` or server-side
tool declarations, and does not call the sandbox — those are Otari-specific and
sending them to another provider would fail, while claiming them without sending
them would be false. Settings reports `otari_features_available: false` in that
mode and no feature evidence is recorded.
