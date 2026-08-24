# Raft: Otari verification and corrected PRD

Verified on 11 August 2026 against:

- `mozilla-ai/otari-ai` at `20081ab75b72f2bb5344b5e42481ed798aae26bc`
- `mozilla-ai/otari` at `1ab8a4bd6d47fd4da964cafe570624715b4bf734`

This is a code and documentation verification, not a live production probe. The current repository catalog is exact for the commits above. The models a particular hosted workspace can call must still be read from authenticated `GET /api/v1/models`; source code cannot prove that a production credential, feature flag, wallet, or workspace allowlist is active.

## 1. Verdict table

| Item | Claim | Verdict | What is actually true | Reference |
| --- | --- | --- | --- | --- |
| 1. Code execution | Raft can put its trace table in Otari's sandbox and run its own pandas or SQL aggregation there. | **Partly right. The PRD guessed the wrong interface.** | A model can call `otari_code_execution` during a completion, but hosted Otari also exposes the sandbox primitive directly at `POST /api/v1/sandbox/sessions`, `POST /api/v1/sandbox/sessions/{id}/exec`, and `DELETE /api/v1/sandbox/sessions/{id}`. Raft can call those endpoints itself with a workspace API key. The hosted proxy has no file-upload route, so load a compact JSON table through one or more `exec` calls. Each HTTP request is capped at 2 MiB by default. State and files persist inside one session, not across sessions, and the session must be released. There is no documented total dataset-size promise. Use Python's standard library, not an assumed pandas install. | [hosted sandbox routes](backend/app/api/routes/sandbox.py#L130), [2 MiB request cap](backend/app/core/config.py#L63), [sandbox protocol](otari/docs/code-execution-protocol.md#L41), [gateway-owned session lifecycle](otari/src/gateway/services/sandbox_backend.py#L161) |
| 2. MCP servers | Otari helps Raft expose its labelled table as an MCP server. | **Wrong direction.** | Otari is the MCP client. Raft must publish a streamable-HTTP MCP server that the gateway can reach. An organization admin registers it once per workspace through the login-session management API at `/api/v1/workspaces/{workspace_id}/mcp-servers`, then Raft includes its ID in `mcp_server_ids` on each planner request. Inline `mcp_servers` is also supported per request. The model lists and calls the server's tools through Otari. Merely publishing a server that Otari never calls is not use of Otari's MCP feature. MCP cannot share a model request with Otari web search or model-driven code execution today. Direct sandbox calls are separate and do not create that conflict. | [MCP request fields and limits](otari/docs/mcp.md#L3), [workspace registration API](backend/app/api/routes/workspace_mcp_servers.py#L25), [streamable HTTP client](otari/src/gateway/services/mcp_client.py#L82) |
| 3. Guardrails | Otari redacts PII before model input and again before quotes reach the UI. | **Wrong. This is not a redaction product.** | Otari guardrails return a verdict. `block` returns 403 and does not call the provider; `monitor` forwards the request and annotates the result. They do not return rewritten content. The gateway enforces input checks only; output is accepted in configuration but not enforced there. The separate non-streaming platform completion service has output checks, which is not the gateway path Raft needs for MCP and web search. Configuration selects guardrail profiles plus block or monitor mode and implementation-specific kwargs, either per workspace or per request. There is no normalized PII detector or entity-type API, and no per-API-key detector policy. Raft must redact PII locally. A real Otari use is input prompt-injection screening of untrusted trace content before batch labelling. Blocked traces remain stored but are quarantined from model-derived labels. | [gateway modes](otari/docs/guardrails.md#L38), [input-only limitation](otari/src/gateway/models/guardrails.py#L48), [request config](backend/app/models/guardrails.py#L18), [workspace config](backend/app/models/workspace_guardrail.py#L55), [current implementation allowlist](backend/app/seed_data/guardrail_implementations.json#L1) |
| 4. Budgets | Raft can create a hard budget for each analysis run and use Otari to project that run's price. | **Wrong at run scope, partly right as a safety ceiling.** | Budgets are daily, weekly, or monthly and scoped to organization, workspace, member, API key, or provider key. Login-session management APIs can create and update them, but a Raft workspace API key cannot administer its own budget and there is no analysis-run budget scope. Updating a cap does not reset accumulated spend. Otari reserves an upper bound before each LLM request and rejects a request that lacks headroom with 403. In a 1,000-call derivation, earlier calls remain complete and the next rejected call leaves a partial, resumable run. `/api/v1/chat/completions/cost` prices known token counts, not an unknown future run. `/api/v1/request-costs/{request_id}` returns actual settled cost after a routed request. Raft must estimate the run locally and enforce its own run allowance while using an admin-configured API-key budget as the aggregate hard stop. | [budget periods](backend/app/models/mixins.py#L10), [workspace and API-key budget APIs](backend/app/api/routes/workspaces.py#L626), [preflight reservation](backend/app/services/reservation_service.py#L46), [403 mapping](backend/app/api/exception_handlers.py#L204), [cost calculator](backend/app/api/routes/completions.py#L334), [settled request cost](backend/app/api/routes/request_costs.py#L41) |
| 5. Routing | Choosing a cheap model for batch calls and a strong model for planning is Otari Routing, with automatic provider fallback. | **Partly right. Direct model choice is not Routing.** | On hosted Otari, a workspace default routing policy, or an internally attached API-key policy, replaces the requested model with an ordered provider/model candidate list. `fallback_enabled` controls whether the gateway advances through that list. The public API exposes policy CRUD, but it does not expose the API-key-to-policy assignment field, so one public workspace effectively has one usable default policy. Raft should use a dedicated batch workspace with a real default failover policy, and a second no-policy workspace for direct planner and autopsy model selection. Passing different `model` strings alone does not earn an honest Routing claim. Hosted responses do not expose an explicit `fallback_fired` flag; compare the final provider/model from request-cost lookup with the configured primary, which proves the final selection but not every failed attempt. Standalone Otari has richer named-policy routing and `/v1/usage` attempt detail, but that is a different deployment. | [hosted policy model](backend/app/models/routing_policy.py#L14), [hosted plan selection](backend/app/services/routing_plan_service.py#L45), [policy CRUD](backend/app/api/routes/routing_policies.py#L16), [API-key field with no public setter](backend/app/models/workspace_api_token.py#L41), [standalone routing](otari/docs/routing.md#L1) |
| 6. Web search | A per-trace “look this up” action can enable Otari web search and show its sources. | **Partly right. The use is real; source visibility depends on the API shape.** | Otari web search is a server-side tool that the model decides to call. Enable it per request with `tools: [{"type":"otari_web_search"}]`; hosted Otari also requires the workspace web-search policy to be enabled. The hosted platform default backend is Tavily, with Brave also implemented, but the active gateway backend is operator-level and the workspace provider field is informational. Chat Completions returns only the final answer, not tool calls or raw results. Responses exposes the search query, not the result list. Messages can expose URL/title citation blocks only when provider-native web-search syntax is intercepted by the gateway. Therefore Raft must not promise structured sources unless that path is verified in the deployed environment. The error-code lookup is the only honest use in these journeys. Adding web results to dataset analysis would contaminate the closed-dataset answer. | [tool declaration and incompatibilities](otari/docs/tools.md#L3), [client-visible shapes](otari/docs/tools.md#L92), [hosted search policy](otari/docs/hybrid-mode-protocol.md#L240), [hosted default provider](backend/app/core/config.py#L337) |
| 7. Open-weights models and embeddings | Otari can supply cheap open-weight JSON models, a mid-tier model, and an embedding endpoint for Layer 2. | **Partly right. Hosted embeddings are the broken part.** | The current managed catalog contains 29 non-proprietary model entries under public provider `mzai`, mostly backed by Nebius, with two Gemma entries backed by Gemini. Actual workspace availability is the authenticated `/api/v1/models` response. The cheapest text candidates in the catalog are `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` and `nvidia/Nemotron-3-Nano-Omni` at $0.06/M input and $0.24/M output. Source code cannot prove reliable JSON conformance, so benchmark the text-only Nano model with `response_format`, schema validation, and repair retries before making it the workhorse. `Qwen/Qwen3-30B-A3B-Instruct-2507` is the sensible mid-tier candidate; `openai/gpt-oss-120b` is the planner candidate. Standalone Otari exposes `/v1/embeddings` and the catalog contains `Qwen/Qwen3-Embedding-8B`, but connected hosted Otari does not mount the embeddings endpoint. Raft must embed locally or use standalone Otari. This PRD chooses a local open-weight embedding model. | [managed catalog](backend/app/seed_data/managed_models_info.json#L1), [authoritative workspace model API](backend/app/api/routes/completions.py#L367), [hosted versus standalone endpoint matrix](otari/docs/api-reference.md#L13), [embeddings route mounted only in standalone](otari/src/gateway/api/main.py#L44) |
| 8. Trace capture | Otari usage logging is metadata-only, so Raft needs a recording proxy for bodies. | **Correct for Otari-generated traces. Keep the proxy.** | Hosted gateway usage reports contain status and token counts, and the platform's synthetic span contains provider/model/cost/IDs with an empty `raw_otel`. The gateway never sends prompt or response bodies. Standalone external-usage and OTLP ingestion is explicitly content-free. Hosted `POST /v1/traces` can preserve arbitrary caller-supplied span attributes, and `GET /api/v1/traces/workspaces/{workspace_id}/span?trace_id=...&span_id=...` returns them under `data.extra_metadata.raw_otel`; both require the app to supply the content first, and the read route uses a logged-in JWT session. That does not capture gateway bodies for Raft and is not a generic replacement for the proxy. The v1 proxy must be described as OpenAI Chat Completions compatible, not universally OpenAI compatible. It must tee SSE streams, merge streamed tool-call deltas, store incomplete captures on disconnect, inject the server-side Otari bearer token without logging it, and treat Anthropic Messages as a separate protocol. | [gateway usage shape](otari/docs/hybrid-mode-protocol.md#L300), [synthetic span](backend/app/services/gateway_usage_service.py#L535), [content-free standalone OTLP](otari/src/gateway/api/routes/otlp.py#L1), [hosted raw OTLP storage](backend/app/services/otel_trace_ingest_service.py#L200), [JWT trace read API](backend/app/api/routes/traces.py#L14), [connected auth](otari/docs/api-reference.md#L36) |

## 2. Updated PRD

# Raft: PRD v0.2

Name: **Raft** (a group of otters floating together, like your traces). Final.

Status: corrected draft for implementation. Owner: you. Target: Otari Games submission, Monday 31 August 2026.

## 1. The bet

Langfuse and its peers show you one trace at a time. They are a viewer. The person looking at them still has to do all the thinking, and they can only think about the trace currently on screen.

Raft assumes the opposite: **the value is in the pile, not the single trace.** An LLM reads every trace, turns each one into a structured row, and then the pile becomes queryable. You stop scrolling through JSON and start asking “what do my users keep struggling with”, “what do they want that my agent cannot do”, and “what breaks most often”.

The second framing matters more than it first looks. A trace is not only a record of what your system did. It is a record of what a real user asked for, in their own words, and whether they got it. That makes a trace store the most honest user research dataset most teams already own and never read. Raft's job is to read it for them.

**The design constraint that follows:** Raft ships with no opinion about what your app is for. Every predefined category is a question someone else decided you were allowed to ask. A refund bot, a shopping assistant, and a coding agent must all be fully served by the same schema, and a question nobody anticipated must be answerable without a code change. Section 8 is where this is enforced, and it is the part of this document to argue with hardest.

The LLM is not a bolt-on. It converts unstructured traces into structured data. Without it this product does not exist.

## 2. Who it is for

- **Primary: the developer who shipped an LLM feature and now owns it.** Checks in a few times a week. Wants to know if it is getting worse and why. Does not want to learn an observability query language.
- **Secondary: the person deciding what to build next.** PM or founder. Wants to know what users are asking for and failing to get, without running interviews. Cares about spend too, but that is not what gets them to open the tool.

## 3. Scope

**In scope for v1**

- Two journeys only: see one trace clearly, ask the whole set questions.
- Read-only. Raft never changes anyone's app.
- A seeded demo workspace so anyone can try it in 10 seconds without wiring anything up.
- OpenAI Chat Completions capture, including non-streaming and SSE streaming.

**Out of scope for v1**

- Evals, scoring, datasets, prompt management, alerting, team accounts, RBAC.
- Live tailing. Batch refresh is enough.
- Anything that requires the user to write SQL.
- Any domain taxonomy, vertical template, or “support bot mode”. Categories come from the user's data, never from Raft.
- Anthropic Messages and OpenAI Responses proxy compatibility. Those are separate wire protocols and need separate capture adapters. Upload remains available for those users.
- Exact application-side tool latency when the app does not emit it. A base-URL proxy sees LLM requests and responses, not arbitrary code running inside the user's app.

## 4. Journey 0: getting traces in

This journey exists to be forgettable. If it takes more than one paste, users will not reach the two journeys that matter.

**Verified constraint D1:** Otari's own gateway traces do not contain prompt or completion bodies. They contain usage and routing metadata. Raft must capture conversation content itself or receive it from the user's own instrumentation.

Three ways in, ranked by how they will actually be used:

1. **Demo workspace.** Pre-seeded with roughly 200 traces from a real agent, including a deliberate mess of failures. Default landing state. No signup.
2. **Upload.** Drag a JSON or JSONL file of traces. Accepts Raft's own schema and OpenAI Chat Completions dumps.
3. **Recording proxy.** For applications that call `/v1/chat/completions`, point `OPENAI_BASE_URL` at Raft. Raft forwards the request to `https://api.otari.ai/v1/chat/completions`, streams the response through unchanged, and records both directions.

The proxy contract is specific:

- The client authenticates to Raft with a Raft ingest key. Raft injects the workspace's Otari API key as `Authorization: Bearer ...` on the upstream request. It never forwards or logs arbitrary incoming authorization headers.
- Non-streaming responses are stored after the upstream response completes.
- Streaming responses are teed without buffering the user-visible stream. Raft merges content deltas, reasoning deltas, and tool-call argument deltas by choice and tool-call index. A disconnect produces an `incomplete` capture rather than a fake completed trace.
- Application-owned tool calls remain in the captured OpenAI transcript. Gateway-owned Otari tools are consumed by Otari and are not returned in Chat Completions. Raft does not claim to see those hidden tool calls.
- The proxy can infer message order and tool-call relationships from the transcript, but it cannot invent tool execution duration or application spans it never saw. Unknown duration is shown as unknown.
- Anthropic clients cannot simply send `x-api-key` to hosted Otari. Connected Otari requires an Authorization bearer token. A future Messages adapter must translate auth and proxy `/v1/messages` separately.

Privacy is Raft's job, not Otari Guardrails' job. Raft stores the original capture encrypted for provenance, creates a deterministic redacted copy at ingest, and uses only that redacted copy for model calls and normal UI. Email addresses, phone numbers, obvious credentials, and configured entity patterns are replaced with stable placeholders. A displayed quote is a literal substring of the redacted trace, never a model paraphrase.

**Acceptance:** a new visitor sees a populated Home page, with real pre-made insights rather than empty states, within 10 seconds of opening the URL and having configured nothing. A streaming capture preserves the exact user-visible response and is marked incomplete if the client disconnects before completion.

## 5. Journey 1: “I want to visualize my traces”

### The story

Maya's support bot gave a bad answer to a customer yesterday. She has the trace ID. She wants to know what happened without reading 4,000 lines of JSON.

### Step by step

1. **Opens the trace list** from Home, or arrives there by drilling down from an insight. One row per trace: time, app, model, outcome badge, cost, duration, capture completeness, and a one-line plain-language summary of what the trace was doing. The summary is the differentiator. Every competitor shows an ID and a token count, which tells you nothing about which row to click.
2. **Filters.** Free text search, plus filter chips for outcome, failure mode, model, and app. Chips are generated from the labels in section 8, so they describe real problems rather than generic status codes.
3. **Opens a trace.** Top of the page is **the autopsy**: three to five sentences, written by an LLM, that say what the user asked for, what the agent tried, where it went wrong, and what it cost. If a guardrail blocked model analysis, Raft says that plainly and shows no fabricated autopsy.
4. **Reads the timeline.** A horizontal track of spans in order: user message, thinking if present on the wire, tool call, tool result if it appears in a later transcript, and assistant message. Two encodings matter:
   - **Width is cost, not time.** The expensive LLM parts are visually large. Zero-cost or unknown-cost application spans get a minimum visible width. Time is available on a toggle only where Raft observed it.
   - **Repeats are collapsed.** Three identical failing tool calls render as one block marked “x3”, expandable. Loops become obvious instead of becoming scroll.
5. **Clicks a span.** Right panel shows redacted content, tokens, cost, observed latency, and status. Under it, **“Explain this step”** sends that redacted span plus its neighbours to a small open-weight model. It is optional and on demand.
6. **Hits a dead end.** If a span is a provider error with an unfamiliar code, Raft offers **“Look this up”**. A separate Otari request enables `otari_web_search` and asks for the meaning, whether the failure is likely transient or configuration-related, and the usual fix. Search is intentionally separate from the closed-dataset analysis. A sources panel appears only if the deployed Messages-native interception path returns URL/title result blocks. Chat Completions does not expose raw search results, so Raft does not pretend otherwise.

### Acceptance criteria

- Trace list renders in under 1 second for 500 traces.
- A cached autopsy appears immediately. An uncached autopsy appears within 3 seconds or shows a clear pending state.
- A trace with 40 spans fits on one screen without horizontal scrolling.
- Unknown tool timing is labelled unknown. It is never inferred from message order.
- A user who has never seen the product can answer “why did this trace fail” in under 60 seconds. Test this with one colleague and record the time.

## 6. Journey 2: “Ask your traces anything”

### The story

Maya is planning the next quarter for the support bot. She wants to know what her users actually struggle with when they talk to it, not what she assumes they struggle with. She has thousands of real conversations sitting in her trace store and no way to read them. Today her options are to skim 30 traces by hand and generalise from those, or to run user interviews for something the traces already answer.

The question she wants to type is: **“what are the things my users struggle with most when speaking with my LLM?”**

### Step by step

1. **One input box** at the top of Home, with example questions as clickable chips. Suggested starters, in this order:
   - What do my users struggle with most?
   - What are people asking for that my agent cannot do?
   - How many people are getting product suggestions?
   - Where do conversations end without the user getting what they wanted?
   - Which failure mode costs me the most?

   User-insight questions lead and cost questions come last. The chips span all three answering paths in section 8. The product-suggestion question is deliberately not a shipped schema field.
2. **Asks a question in plain English.** No syntax, no field names.
3. **Raft inspects the dataset through MCP, picks a path, and says which one.** Raft publishes a read-only streamable-HTTP MCP server with tools such as `describe_dataset`, `list_aspects`, and `get_representative_traces`. It is registered in the planner workspace and its ID is passed in `mcp_server_ids` on each planner request. Otari connects to it and the planner model calls it. The server returns redacted summaries and schema metadata, never raw secrets. The planner chooses: query the shape table, cluster the content vectors, or define a new aspect and evaluate it. The UI states whether Raft used existing rows, clustered all eligible traces, or read each trace for a new aspect.
4. **If a new aspect is needed, Raft asks first.** The confirmation is honest: *“I need to evaluate 847 conversations. Estimated 35 to 55 seconds and up to $0.08. Your Raft API-key budget currently has sufficient daily headroom. Run it?”* Raft computes the estimate from measured latency, local token estimates, catalog prices, and recent actual request costs. Otari does not provide a per-run projected-spend API or a per-run budget. The aspect question is editable before the run.
5. **Numbers always come from code.** The model judges one conversation at a time and writes typed aspect values. It never counts. Raft opens a hosted Otari sandbox session directly, loads a compact table containing trace IDs and the relevant shape, cluster, or aspect columns in chunks, runs fixed Python aggregation code, parses the JSON result from stdout, and deletes the session. The aggregation code and its input row count are available under **“Show the work”**. No model writes or edits this code.
6. **Gets an answer with four parts,** in this order:
   - The headline: a ranked list or number, with counts and shares of eligible conversations.
   - One sentence interpreting it.
   - **Two or three real redacted user quotes per group**, selected from traces in that group. Each quote is a literal substring of the deterministic redacted trace, not a paraphrase.
   - **“Show me the traces behind this”**, which opens Journey 1 filtered to the exact trace IDs used in the computation.

   The quotes plus drill-down are the product. The wording states the denominator and any excluded or quarantined traces.
7. **Verifies a derived aspect.** For any answer that used a new aspect, Raft offers five traces marked yes and five marked no. If the aspect is wrong, Maya edits the question and reruns it. A changed question creates a new aspect version rather than silently mutating the old evidence.
8. **Follows up.** “Now just for the checkout app.” Or “show me only the ones where the user gave up.” Context carries over, and so do aspects. Once `got_product_suggestion` exists, it is a normal cached column and new traces are evaluated at ingest.
9. **Sees the pre-made insights.** Directly below the input box: the top three things users struggled with this week, the top unmet request, and spend split by outcome. All three come from layers 1 and 2, so Home never waits on a new derivation. Each is clickable and drills down to the exact evidence set.

### Budget-stop behaviour

Otari reserves budget before each model request. If the dedicated API-key budget has insufficient headroom, the next request returns 403. Raft stops scheduling new work, lets already accepted calls settle, stores completed aspect rows, and marks the run `paused_budget` with `completed / eligible` progress. It does not present partial counts as a full-dataset answer. After an admin increases the daily or monthly budget, the user resumes from the missing rows. Raft's own scheduler also stops before its local per-run allowance is exceeded, based on settled costs plus conservative reservations for in-flight work.

### Acceptance criteria

- Every numeric claim in an answer traces back to deterministic executed code. The code, row count, denominator, and excluded count are viewable.
- Every quote is a literal substring of a redacted real trace and links to that trace. No paraphrasing and no invented quotes.
- All five example questions return a correct answer against the demo dataset, using all three paths between them.
- **The domain test:** point Raft at a trace set from a different kind of app and ask the same five questions. If any requires a code or schema change, layer 3 has failed.
- Layer 1 and layer 2 answers arrive in under 15 seconds. Aspect derivations show a cost range and time range up front, then a live completed count.
- A budget rejection produces a paused, resumable run. It never produces a silently partial final answer.
- Drill-down from any answer lands on a trace list filtered to exactly the traces used.
- Hand-check the top result of the struggle question against 20 traces. If the clusters do not match the reading, the labels are wrong and nothing else in this journey matters.

## 7. Screens

Three pages.

1. **Home.** The landing page and the centre of the product. Ask box at the top, pre-made insights below it: top failure modes, spend by outcome, week-over-week delta. A user who never leaves this page should still get value on the first visit.
2. **Trace list.** Journey 1 entry and the target of every drill-down. Opening a row expands into the trace detail view: autopsy, timeline, span panel. Detail is a view inside this page, so drilling in and back out never loses the filter.
3. **Settings.** Ingest configuration, proxy URL and key, model availability check, local redaction patterns, Otari workspace configuration status, API-key budget headroom, and demo data reset. It does not claim to create a per-run Otari budget.

## 8. Data model

Normalized trace, written at ingest:

```json
{
  "trace_id": "t_01J8...",
  "app": "support-bot",
  "started_at": "2026-08-11T09:14:22Z",
  "duration_ms": 8420,
  "model": "mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
  "provider": "mzai",
  "status": "ok",
  "capture_completeness": "complete",
  "otari_request_id": "6a76...",
  "cost_usd": 0.0043,
  "tokens": { "input": 3200, "output": 410, "cached": 0 },
  "spans": [
    {
      "index": 0,
      "type": "user_message",
      "name": null,
      "duration_ms": null,
      "tokens_in": 24,
      "tokens_out": 0,
      "cost_usd": 0,
      "status": "ok",
      "content_redacted": "..."
    }
  ]
}
```

Span types: `system`, `user_message`, `thinking`, `tool_call`, `tool_result`, `assistant_message`, `error`.

Two label layers plus a derivation engine. The split is the most important decision in this document.

**The rule:** precompute facts about the shape of a conversation, because those are true of every LLM app. Never precompute facts about its content, because those are only true of one app.

### Layer 1: shape

Domain-free. True of a refund bot, a coding agent, and a recipe app alike. Most of it is computed from the trace with no model.

```json
{
  "trace_id": "t_01J8...",
  "turns": 6,
  "tool_calls": 4,
  "distinct_tools": ["lookup_order", "escalate"],
  "repeated_identical_calls": 3,
  "duration_ms": 8420,
  "cost_usd": 0.0043,
  "error_spans": [7],
  "ended_by": "user",
  "last_span_type": "assistant_message",
  "intent_satisfied": "no",
  "user_gave_up": true,
  "rephrase_count": 3,
  "sentiment_end": "frustrated",
  "failure_mode": "tool_loop",
  "confidence": 0.82,
  "analysis_status": "complete"
}
```

The first block is arithmetic. The second block is the only part a model judges, and every field is a question that applies to any conversation. `failure_mode` is a closed mechanical enum: `none`, `tool_error`, `tool_loop`, `hallucinated_tool`, `context_overflow`, `refusal`, `wrong_answer`, `timeout`, `guardrail_block`, `provider_error`.

`analysis_status` is `complete`, `guardrail_blocked`, `invalid_model_output`, or `pending`. Guardrail-blocked traces remain available in Journey 1 and are included in exclusion counts, but Raft does not invent labels for them.

### Layer 2: content, as text and vectors, with no categories

```json
{
  "trace_id": "t_01J8...",
  "user_request": "wanted to cancel a refund and reorder the item in a different size",
  "what_happened": "agent only reported refund status and never offered to cancel",
  "verbatim_quote": { "span": 4, "text": "no I don't want the status, I want to cancel it" },
  "embedding_model": "BAAI/bge-small-en-v1.5",
  "embedding": [0.013, -0.221, "..."]
}
```

Free text and a local embedding. No enum, no taxonomy, no opinion about what this app is for. Categories are discovered by clustering these vectors at question time.

`verbatim_quote.text` must be a literal substring of the redacted span it points at. Verify this in code and drop it if it is not. The quote is verbatim relative to the redacted evidence view. Raft never calls redacted text identical to the original private text.

Hosted Otari does not expose `/v1/embeddings`. Raft therefore runs a pinned local open-weight embedding model in v1. `BAAI/bge-small-en-v1.5` is the default because it is small enough to ship. This model does not count as an Otari-routed model unless the competition rules explicitly say local integration counts. If standalone Otari is selected instead, replace this local call with `/v1/embeddings` and a tested configured embedding model.

### Layer 3: aspects, the blank canvas

When a question cannot be answered from layers 1 and 2, Raft creates an **aspect**: a per-trace question with a typed answer, evaluated once per trace and cached.

```json
{
  "aspect_id": "a_product_suggestion_v1",
  "question": "Did the assistant suggest a specific product to the user?",
  "type": "boolean",
  "created_from": "how many people are getting product suggestions from my LLM?"
}
```

Store aspects as `(trace_id, aspect_id, value, confidence, model_id, prompt_version, status)`. Types: `boolean`, `number`, `category`, and `text`.

Aspects are cached and incremental. Ask once, pay once. New traces are evaluated against every saved aspect at ingest. Editing an aspect creates a new version so old answers remain reproducible.

### How each question type gets answered

The planner picks one of three paths and tells the user which one it took.

| Question | Path | Final numeric work |
| --- | --- | --- |
| “Where am I paying for retries?” | Layer 1 filter | Fixed Python aggregation in a direct Otari sandbox session |
| “What do my users struggle with most?” | Cluster local Layer 2 vectors where `intent_satisfied = no`, then name clusters from representative redacted contents | Cluster membership and counts computed in code; model only names clusters |
| “How many people are getting product suggestions?” | New aspect evaluated over eligible traces | Typed model judgments stored row by row, then counted in code |

The struggle question is an emergent clustering result rather than a category guessed in advance.

## 9. Model and Otari call plan

The old heading “LLM calls, all via Otari” was false because hosted Otari has no embeddings endpoint. Every generative call goes through Otari. Embeddings run locally in v1. Aggregation uses the direct hosted Otari sandbox API without a model in the loop.

| # | Where | Purpose | Model or execution | Otari mechanics |
| --- | --- | --- | --- | --- |
| 1 | Ingest, batch | Layer 1 judgments and Layer 2 text, one trace per call | `mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, pending benchmark | Gateway Chat Completions with `response_format`; top-level input guardrail in block mode; strict local schema validation and bounded repair retry |
| 2 | Ingest, local batch | Embed `user_request` for clustering | `BAAI/bge-small-en-v1.5` | Local process, because hosted Otari has no `/v1/embeddings` |
| 3 | Insights | Inspect dataset and choose path; write aspect question | `mzai:openai/gpt-oss-120b` | Planner request includes registered Raft `mcp_server_ids`; the model calls Raft's read-only MCP tools through Otari |
| 4 | Insights, batch | Evaluate one aspect against one trace | Same tested small model as call 1 | Dedicated batch workspace whose default routing policy has a real primary and fallback chain |
| 5 | Insights | Name clusters and write one interpretation sentence | `mzai:Qwen/Qwen3-30B-A3B-Instruct-2507` | Direct model selection in a no-policy reasoning workspace |
| 6 | Trace detail | Write the autopsy | Same Qwen model as call 5 | Direct model selection, cached by trace content hash and prompt version |
| 7 | Span panel | Explain one step | Tested small model | On demand, no MCP or web search |
| 8 | Span panel | Look up unfamiliar provider error | A model verified to call tools reliably | Separate request with `tools: [{"type":"otari_web_search"}]`; no MCP or code-execution tool in that request |
| 9 | Insights, deterministic | Count, group, rank, and calculate shares | Fixed Python code | Raft directly calls `/api/v1/sandbox/sessions` and `/exec`; no model writes or counts |

### Model availability gate

At startup and before the demo, Raft calls authenticated `GET /api/v1/models` on the hosted platform and verifies every configured model ID. A model appearing in the source catalog is not enough. If a configured model is absent, Settings shows the missing ID and the demo fails before ingest rather than silently switching models.

The workhorse is not accepted based on vibes. Run at least 200 representative traces through each cheap candidate. Require valid JSON after at most one repair retry, stable enum values, and at least 99% parse success. Compare 40 hand-labelled traces for field accuracy. The current catalog makes the text-only Nemotron Nano the cheapest sensible candidate, not a proven winner.

### Current managed open-weight catalog snapshot

All entries below use public provider `mzai`. Prices are repository catalog dollars per million input/output tokens, not a live quote. Actual workspace availability comes from `/api/v1/models`.

| Model | Upstream | Modality | Input / output |
| --- | --- | --- | --- |
| `openai/gpt-oss-120b` | Nebius | Text | $0.15 / $0.60 |
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Nebius | Text | $0.06 / $0.24 |
| `Qwen/Qwen3-235B-A22B-Instruct-2507` | Nebius | Text | $0.20 / $0.60 |
| `Qwen/Qwen3-30B-A3B-Instruct-2507` | Nebius | Text | $0.10 / $0.30 |
| `Qwen/Qwen3-32B` | Nebius | Text | $0.10 / $0.30 |
| `nvidia/Llama-3_1-Nemotron-Ultra-253B-v1` | Nebius | Text | $0.60 / $1.80 |
| `meta-llama/Llama-3.3-70B-Instruct` | Nebius | Text | $0.13 / $0.40 |
| `moonshotai/Kimi-K2.6` | Nebius | Text | $0.95 / $4.00 |
| `NousResearch/Hermes-4-405B` | Nebius | Text | $1.00 / $3.00 |
| `NousResearch/Hermes-4-70B` | Nebius | Text | $0.13 / $0.40 |
| `Qwen/Qwen3-Next-80B-A3B-Thinking` | Nebius | Text | $0.15 / $1.20 |
| `MiniMaxAI/MiniMax-M2.5` | Nebius | Text | $0.30 / $1.20 |
| `MiniMaxAI/MiniMax-M3` | Nebius | Text | $0.30 / $1.20 |
| `zai-org/GLM-5.1` | Nebius | Text | $1.40 / $4.40 |
| `Qwen/Qwen3.5-397B-A17B` | Nebius | Text | $0.60 / $3.60 |
| `nvidia/nemotron-3-super-120b-a12b` | Nebius | Text | $0.30 / $0.90 |
| `Qwen/Qwen3-Embedding-8B` | Nebius | Embedding | $0.01 / $0.00 |
| `Qwen/Qwen2.5-VL-72B-Instruct` | Nebius | Vision | $0.25 / $0.75 |
| `google/gemma-3-27b-it` | Nebius | Text | $0.10 / $0.30 |
| `deepseek-ai/DeepSeek-V4-Pro` | Nebius | Text | $1.75 / $3.50 |
| `nvidia/Nemotron-3-Nano-Omni` | Nebius | Text | $0.06 / $0.24 |
| `nvidia/Cosmos3-Super-Reasoner` | Nebius | Vision | $0.10 / $0.30 |
| `openbmb/MiniCPM-V-4_5` | Nebius | Vision | $0.658 / $1.11 |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | Nebius | Text | $1.00 / $3.00 |
| `zai-org/GLM-5.2` | Nebius | Text | $1.40 / $4.40 |
| `moonshotai/Kimi-K2.7-Code` | Nebius | Text | $0.95 / $4.00 |
| `moonshotai/Kimi-K3` | Nebius | Vision | $3.00 / $15.00 |
| `gemma-4-31b-it` | Gemini | Text | $0.00 / $0.00 in the current catalog |
| `gemma-4-26b-a4b-it` | Gemini | Text | $0.00 / $0.00 in the current catalog |

The three intended Otari-routed open-weight integrations are Nemotron Nano for volume, Qwen3 30B for autopsies and cluster names, and gpt-oss 120B for planning. They count only after Raft actually calls all three in the working demo and records evidence. A configured but unused model is not an integration.

## 10. Otari feature map

Each surviving feature has a product job. None exists only to inflate the score.

| Feature | Judgment | Honest use in Raft | Correct mechanics and limits |
| --- | --- | --- | --- |
| **Routing** | Correct idea, wrong mechanics | Protect the high-volume ingest and aspect pipeline from one model/provider failure. | Put batch traffic in a dedicated hosted workspace with a default ordered policy, for example Nemotron Nano primary and Qwen3 30B fallback. Keep planner and autopsy traffic in a second no-policy workspace so per-call model choice still works. Different `model` strings alone are not Routing. Compare request-cost final model with the primary to infer that fallback selected a different candidate. |
| **Code Execution** | Load-bearing, mechanics corrected | Execute every final count, share, group, and ranking from a real table. | Raft calls the hosted sandbox endpoints directly. It loads only compact derived rows, chunks below the 2 MiB request cap, runs fixed standard-library Python, reads JSON from stdout, and deletes the session. State lasts only for that session. No model counts and no model writes aggregation code. |
| **MCP Servers** | Correct idea, direction reversed | Let the Journey 2 planner inspect the Raft dataset and retrieve representative redacted traces through tools. | Raft publishes the server; Otari consumes it. Register once per planner workspace and pass `mcp_server_ids` on planner calls. Restrict `allowed_tools` to read-only tools. Merely exposing the server does not count. |
| **Budgets** | Available for a real use the PRD misstated | Bound aggregate Raft batch and reasoning spend, and exercise a clean pause/resume path. | Apply daily and monthly limits to dedicated Raft API keys. Raft estimates and enforces a local run allowance. If Otari rejects the next request with 403, preserve completed rows and pause. There is no native per-run budget or run projection. |
| **Guardrails** | Correct idea, wrong job | Detect prompt injection or unsafe untrusted trace content before the high-volume labeler sees it. | Use an available profile in input block mode on each ingest labelling request. Keep blocked traces, mark them `guardrail_blocked`, and exclude them from model-derived aggregates with visible counts. Perform PII redaction locally. Do not claim Otari redacts or gateway-checks output. |
| **Web Search** | Load-bearing but deliberately narrow | Explain an unfamiliar provider error in Journey 1 using current external documentation. | Enable `otari_web_search` only on that request. Hosted workspace search policy must be enabled. Do not add web search to Journey 2. Show structured sources only when the deployed API returns native citation blocks. |

### Configuration topology

- **Batch workspace and API key:** default Routing policy, input guardrail, daily and monthly API-key budget, code execution enabled.
- **Reasoning workspace and API key:** no default Routing policy, Raft MCP server registered, web search enabled, daily and monthly API-key budget.
- **Raft backend:** local PII redactor, local embedding model, aspect queue, deterministic sandbox client, request-cost polling, and the streamable-HTTP MCP server.

Keep two Otari base URLs in configuration. The generation gateway serves `/v1/chat/completions`, `/v1/messages`, and `/v1/responses`. The platform API serves `/api/v1/models`, `/api/v1/sandbox`, `/api/v1/request-costs`, and the login-session management routes. Do not construct platform URLs by appending `/api/v1` to the generation gateway host unless the deployed environment explicitly documents that host mapping.

Routing policies, budgets, MCP registration, workspace code-execution enablement, and workspace web-search enablement are provisioned by an organization admin before the demo. Raft's runtime workspace API key does not have authority to mutate those settings. This two-workspace split is deployment plumbing, not a user-facing product feature. It exists because the hosted public API does not expose API-key-specific routing-policy assignment and a workspace default policy overrides the requested model.

## 11. Risks

1. **Native Otari traces do not contain bodies.** The proxy or upload is mandatory. Without it, Raft has no product dataset.
2. **The proxy sees only the LLM wire.** It cannot recover arbitrary application spans or exact tool duration. Do not present reconstructed chat messages as complete distributed traces.
3. **Label quality decides the whole product.** Mitigate with schema validation, the verification sample, model benchmarks, and a hand-check of 20 traces.
4. **Hosted embeddings do not exist.** Pin and ship a local embedding model. If local model packaging misses the deadline, replace vector clustering with TF-IDF as a degraded mode and state the loss in semantic recall.
5. **Otari guardrails do not redact.** Local deterministic PII redaction is a security dependency, not polish.
6. **Aspect derivation is slow and linear.** Fine at 1,000 traces, painful at 100,000. v1 caps the derivation set and says so. Sampling with a stated confidence interval is a later path.
7. **Hosted routing observability is weak.** Raft can prove the final model, but not show the full failed-attempt chain through a public endpoint. Do not manufacture a richer fallback story.
8. **Web-search citations are API-shape dependent.** The explanation works through Chat Completions, but a reliable citations panel requires verified Messages-native interception.
9. **Scope creep toward a Langfuse clone.** Any feature outside the two journeys goes on a later list.
10. **Time budget.** Roughly 3 working days total is tight. The proxy's streaming and tool-call capture deserves real tests; treating it as a trivial reverse proxy will create corrupt traces.

## 12. Build order

1. Schema, local redaction, upload normalizer, seeded demo dataset. Half a day.
2. Trace list and detail with explicit unknown or incomplete fields. Half a day.
3. OpenAI Chat Completions recording proxy, including SSE and tool-call delta tests. Three quarters of a day.
4. Model availability gate and 200-trace workhorse benchmark. Half a day.
5. Layer 1 and Layer 2 ingest pass, including local embeddings. Half a day.
6. Autopsy and explain-this-step. Half a day.
7. Read-only Raft MCP server, hosted registration, and planner path. Half a day.
8. Direct sandbox aggregation and “Show the work”. Half a day.
9. Aspect engine: define, evaluate, cache, verify, pause, and resume. Three quarters of a day.
10. Pre-made insights, error lookup, and evidence-backed feature log. Half a day.

The estimate is now longer than the original. The original treated streaming capture, model qualification, and Otari integration as free. They are not. If time runs out, keep upload plus the demo and cut live proxy ingestion before cutting the aspect engine or deterministic aggregation.

## 13. Open questions for implementation review

1. Which of the three target managed models does the competition workspace actually return from `/api/v1/models`? This needs a workspace API key and a live call.
2. Which guardrail profile is provisioned and demonstrably detects prompt injection in the competition workspace? The repository allowlist is not proof that its external credential is configured.
3. Is web-search interception enabled on the hosted gateway? If not, keep the lookup but omit the structured sources panel.
4. Where does the demo dataset come from? Real traces need local PII redaction before they enter the demo artifact.
5. Is a chat-level trace sufficient for the demo, or does the intended wow factor require application spans and accurate tool timing? If it requires the latter, upload or a tiny Raft instrumentation SDK is mandatory.
6. Is `intent_satisfied`, `user_gave_up`, `rephrase_count`, or `sentiment_end` secretly domain-specific? Any field that fails the refund-bot, coding-agent, recipe-app test becomes an aspect.
7. Do Otari Games rules count a local open-weight embedding model, or only an open-weight model actually called through Otari? Do not claim the point until the rule is explicit.

## 3. Build blockers, ranked

1. **No prompt or completion bodies in Otari-generated traces. Product impact: fatal without another ingest path.** Raft cannot label, quote, autopsy, or cluster metadata-only usage rows. Keep the recording proxy and upload. Reading the normal Otari trace API does not solve this.
2. **The recording proxy is not a full application tracer. Product impact: Journey 1 fidelity is limited.** It can capture chat messages and tool-call transcripts but cannot recover arbitrary app spans or exact tool duration. If the demo depends on those, require uploaded traces or explicit app instrumentation.
3. **No hosted embeddings endpoint. Product impact: Layer 2 cannot remain “all via hosted Otari”.** Run a pinned local embedding model, or choose standalone Otari with a configured embedding provider. The three-layer model itself remains viable.
4. **No Otari PII redaction. Product impact: the original privacy promise is impossible as written.** Build deterministic local redaction before any LLM call or quote display. Otari Guardrails can block, not sanitize.
5. **No native per-run budget or projected run cost. Product impact: the original confirm dialog was false.** Show a locally computed estimate range, enforce a local run allowance, and treat Otari's API-key budget as an aggregate hard stop with partial, resumable work.
6. **Hosted public API does not expose API-key routing-policy assignment. Product impact: multiple model tiers and one workspace default policy conflict.** Use a dedicated batch workspace for the default failover policy and a no-policy reasoning workspace for direct model selection, or use standalone named routing policies.
7. **No general structured web-search source list on Chat Completions. Product impact: the lookup works, but a reliable sources panel may not.** Only show source blocks when the deployed Messages-native interception path returns them.
8. **Actual production model and guardrail availability cannot be proven from source. Product impact: demo configuration may fail at runtime.** Run `/api/v1/models` and one real guardrail request with the competition workspace before building prompts around specific IDs.

## 4. Feature log skeleton

- **Routing:** Used a dedicated batch workspace whose default policy routes high-volume labelling to `[primary]` and falls back to `[fallback]`. Easy: `[fill in]`. Friction and why: `[fill in]`. Evidence: policy export, request ID, configured primary, and final model from request-cost lookup.
- **Code Execution:** Loaded `[row count]` compact derived rows into a direct hosted sandbox session and ran fixed Python for `[question]`. Easy: `[fill in]`. Friction and why: `[fill in]`. Evidence: session request, code shown in UI, stdout JSON, and matching local test result.
- **MCP Servers:** Registered Raft's read-only streamable-HTTP server in the planner workspace; Otari called `[tool names]` during `[question]`. Easy: `[fill in]`. Friction and why: `[fill in]`. Evidence: registration ID, server log, and planner request containing `mcp_server_ids`.
- **Budgets:** Applied daily/monthly limits to the dedicated Raft API keys and handled a preflight 403 by pausing and resuming `[run]`. Easy: `[fill in]`. Friction and why: no run-scoped budget or projection API, `[add observed detail]`. Evidence: budget config, completed count at pause, and resumed result.
- **Guardrails:** Ran `[profile]` in input block mode before untrusted redacted trace content reached the labelling model; quarantined `[count]` blocked traces. Easy: `[fill in]`. Friction and why: Otari blocks but does not redact, `[add observed detail]`. Evidence: blocked request, stored trace status, and visible exclusion count.
- **Web Search Enablement:** Used `otari_web_search` only for the Journey 1 provider-error lookup. Easy: `[fill in]`. Friction and why: source visibility differs by API shape, `[add observed detail]`. Evidence: request tool declaration, search query block if exposed, and resulting explanation.
- **Open-weight model, Nemotron Nano:** Used for `[ingest/aspect count]` high-volume calls. Structured-output pass rate: `[fill in]`. Accuracy sample: `[fill in]`. Cost: `[fill in]`. Friction: `[fill in]`.
- **Open-weight model, Qwen3 30B:** Used for `[autopsy/cluster naming count]` calls. Quality result: `[fill in]`. Cost: `[fill in]`. Friction: `[fill in]`.
- **Open-weight model, gpt-oss 120B:** Used for `[planner count]` calls with Raft MCP tools. Planning accuracy: `[fill in]`. Cost: `[fill in]`. Friction: `[fill in]`.
- **Local embedding model, BGE small:** Used for Layer 2 because hosted Otari has no embeddings route. Count this only if the Games rules confirm local open-weight integrations qualify. Clustering check: `[fill in]`. Packaging friction: `[fill in]`.
