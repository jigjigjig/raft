# Raft Otari integration log

This file is competition evidence, not marketing copy. Entries are written after live attempts. Empty evidence fields are deliberate: the repository currently has no Otari credentials, so no request ID, error, latency, cost, or provider result can be claimed honestly.

## Environment

- Date started: 21 August 2026
- Raft runtime: local Docker/FastAPI/SQLite, dark desktop UI
- Otari mode: `mock` until `RAFT_OTARI_MODE=live` and role API keys are configured
- Generation base URL: configured by `OTARI_GENERATION_BASE_URL`
- Platform base URL: configured by `OTARI_PLATFORM_BASE_URL`
- Public application URL: **pending Cloudflare Tunnel**
- Public MCP URL: **pending Cloudflare Tunnel**
- External tester: **pending**

## Live-call gate

- Schema-validated trace-labelling request: **not attempted — credentials unavailable**
- Request ID: **pending**
- Exact response or error: **pending**
- Manual 20-trace review: see `manual-label-review.md`; **blocked until the live gate succeeds**

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

- Status: **implemented locally; live Otari evidence pending**
- Date/workspace: **pending** / `trace-labeler`
- Endpoint/profile: `POST /v1/chat/completions` / `prompt-injection` in `block` mode
- Request ID and final model/provider: **pending**
- Important distinction: Raft's deterministic redactor performs rewriting. Otari Guardrails judge or block the already-redacted input; they do not sanitize it.
- Safe request evidence: **pending**
- Adversarial request evidence: **pending**
- Exact verdict/error: **pending**
- User-visible result: Settings reports redaction separately from guardrail pass/block, and blocked traces remain quarantined.
- Redacted request/response excerpts: **pending**
- Dead ends and rejected shapes: **pending**
- Workaround and owner (Raft or Otari): **pending**
- Observed cost/latency: **pending**
- Screenshot or reproducible steps: `python scripts/live_smoke.py guardrails`; screenshot **pending**
- Honest verdict: **pending live attempt**

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

- Status: **configuration implemented; live route and fallback evidence pending**
- Date/workspace: **pending** / all six role workspaces
- Endpoint: `POST /v1/chat/completions`
- Role policies: `model-roles.yaml`
- Controlled fallback request ID: **pending**
- Final provider/model: **pending**
- Exact errors or dead ends: **pending**
- What the user sees: Settings shows primary, fallback, observed final model and request ID; progress calls out a final model that differs from the primary.
- Redacted request/response excerpts: **pending**
- Workaround and owner (Raft or Otari): **pending**
- Observed cost/latency: **pending**
- Screenshot or reproducible steps: `python scripts/live_smoke.py all-models`, then controlled provider failure; screenshot **pending**
- Honest verdict: **pending live fallback**

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

- Status: **local Streamable HTTP-compatible JSON-RPC endpoint implemented; hosted registration pending**
- Date/workspace: **pending** / `planner`
- Endpoint: public `POST /mcp`, passed to Otari as `mcp_server_ids`
- Tools: `describe_dataset`, `list_shape_fields`, `list_aspects`, `get_representative_traces`, `get_trace_rows`
- Planner request/tool-call evidence: **pending**
- Public MCP URL: **pending**
- Exact errors or dead ends: **pending**
- Final model/provider and request ID: **pending**
- What the user sees: the Answer progress panel states which MCP tools the planner reports inspecting.
- Redacted request/response excerpts: **pending**
- Workaround and owner (Raft or Otari): **pending**
- Observed cost/latency: **pending**
- Screenshot or reproducible steps: ask the unanticipated question with the registered server; screenshot **pending**
- Honest verdict: **pending recorded MCP tool call**

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

- Status: **local estimate, confirmation, checkpoint, pause, and resume implemented; live Otari 403 pending**
- Date/workspace: **pending** / `aspect-evaluator`
- Endpoint: `POST /v1/chat/completions`; budget managed on the role's Otari API key/user
- Confirmation UX: “About $0.06 and 35–55 seconds. Run it?”
- Budget rejection request ID/error: **pending**
- Resume evidence: **pending**
- Important distinction: the per-run estimate and allowance are Raft features; Otari provides the aggregate API-key hard stop.
- Final model/provider: **pending**
- What the user sees: confirmation before execution; a 403 produces `paused_budget`, saves completed rows, and suppresses partial totals.
- Redacted request/response excerpts: **pending**
- Dead ends and rejected shapes: **pending**
- Workaround and owner (Raft or Otari): **pending**
- Observed cost/latency and headroom: **pending**
- Screenshot or reproducible steps: lower the Otari role budget, run an aspect, raise it, and resume; screenshot **pending**
- Honest verdict: **pending real pause/resume**

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

- Status: **fixed aggregation code and local reference implemented; live sandbox session pending**
- Date/workspace: **pending** / `aspect-evaluator`
- Endpoint/tool: `POST /v1/chat/completions` with `tools: [{"type":"otari_code_execution"}]`
- Sandbox session/request ID: **pending**
- Input row count/stdout JSON: **pending**
- Local comparison: **pending live run**
- Exact errors or dead ends: **pending**
- Final model/provider: **pending**
- What the user sees: “Show the work” exposes fixed Python, input row count, denominator, exclusions, execution mode, and final stdout JSON.
- Redacted request/response excerpts: **pending**
- Workaround and owner (Raft or Otari): **pending**
- Observed cost/latency: **pending**
- Screenshot or reproducible steps: `python scripts/live_smoke.py code-execution`; screenshot **pending**
- Honest verdict: **pending live tool execution**

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

- Status: **user flow and Otari request adapter implemented; live search evidence pending**
- Date/workspace: **pending** / `planner`
- Endpoint/tool: `POST /v1/chat/completions` with `tools: [{"type":"otari_web_search"}]`
- Provider error used: **pending**
- Search request ID/query/result shape: **pending**
- Citation availability: **pending**
- Exact errors or dead ends: **pending**
- Final model/provider: **pending**
- What the user sees: provider-error spans expose “Look this up”; mock output explicitly says no web search occurred.
- Redacted request/response excerpts: **pending**
- Workaround and owner (Raft or Otari): **pending**
- Observed cost/latency: **pending**
- Screenshot or reproducible steps: `python scripts/live_smoke.py web-search`; screenshot **pending**
- Honest verdict: **pending live lookup**

## Open-weights model evidence

| Role | Model | Job | Live request ID | Schema pass rate | Cost / latency | Quality notes |
|---|---|---|---|---:|---|---|
| Trace labeler | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Layer 1/2 labelling | pending | pending | pending | pending |
| Aspect evaluator | `Qwen/Qwen3-30B-A3B-Instruct-2507` | Layer 3 evaluation | pending | pending | pending | pending |
| Cluster namer | `meta-llama/Llama-3.3-70B-Instruct` | Emergent cluster names | pending | pending | pending | pending |
| Autopsy writer | `NousResearch/Hermes-4-70B` | Trace autopsy | pending | pending | pending | pending |
| Span explainer | `google/gemma-3-27b-it` | Explain one span | pending | pending | pending | pending |
| Planner | `openai/gpt-oss-120b` | MCP planning and web lookup | pending | pending | pending | pending |

## External tester

- Name or initials: **pending**
- Date/browser: **pending**
- Reachable application URL: **pending**
- Journey completed: **pending**
- Confusion points and fixes: **pending**

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
