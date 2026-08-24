# Raft deployment handoff

## Stable Cloudflare Tunnel

Raft is packaged as one application container plus an optional `cloudflared` sidecar. Create a named tunnel in the Cloudflare dashboard, add the token to `.env`, and map two public hostnames:

| Public hostname | Tunnel service | Purpose |
|---|---|---|
| `raft.example.com` | `http://raft:8000` | External tester web application |
| `raft-mcp.example.com` | `http://raft:8000` | Read-only planner MCP endpoint |

Set:

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=<named-tunnel-token>
RAFT_PUBLIC_APP_URL=https://raft.example.com
RAFT_PUBLIC_MCP_URL=https://raft-mcp.example.com/mcp
```

Then start both services:

```sh
docker compose --profile tunnel up -d --build
```

When `RAFT_PUBLIC_MCP_URL` is configured, the FastAPI middleware rejects every path except `/mcp` and `/api/health` on that hostname. The MCP tools themselves expose only redacted, read-only SQL projections.

Verify from a device outside the author's network:

```sh
curl -fsS https://raft.example.com/api/health
curl -fsS -X POST https://raft-mcp.example.com/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Record the final URLs and the external session in `otari-log.md`. A quick tunnel is not accepted as the competition deliverable because its URL and lifetime are unstable.

## Live Otari checklist

1. Copy `.env.example` to `.env` and set `RAFT_OTARI_MODE=live`.
2. Add the six role API keys and the registered planner MCP server ID.
3. Configure the `prompt-injection` guardrail, Code Execution, Web Search, model pricing, and a deliberately low test budget in Otari.
4. Run `python scripts/live_smoke.py single-label` first.
5. Run `python scripts/build_demo_live.py label-gate`, then manually review 20 rows.
6. Only after that gate passes, run `python scripts/build_demo_live.py label-rest --gate-approved`.
7. Install the embedding extra, run `python scripts/embed_demo.py`, then `python scripts/build_clusters_live.py`. The pinned BGE revision and real cluster-namer request are stored with the dataset.
8. Run every remaining live smoke command and immediately fill the corresponding section of `otari-log.md`.
