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

> I used feature Guardrails to achieve a privacy and prompt-injection checkpoint. I found M, N, O easy and struggled with A, B, C because D, E, F.

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

> I used feature Routing to achieve model tiering and provider fallback. I found M, N, O easy and struggled with A, B, C because D, E, F.

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

> I used feature MCP Servers to achieve read-only planner access to Raft's redacted trace table. I found M, N, O easy and struggled with A, B, C because D, E, F.

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

> I used feature Budgets to achieve a user-visible cost confirmation and resumable budget stop. I found M, N, O easy and struggled with A, B, C because D, E, F.

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

> I used feature Code Execution to achieve deterministic counts for every answer. I found M, N, O easy and struggled with A, B, C because D, E, F.

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

> I used feature Web Search Enablement to achieve an on-demand explanation of unfamiliar provider error codes. I found M, N, O easy and struggled with A, B, C because D, E, F.

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
