# 🔬 CodeAutopsy

> **AI-powered incident root cause analysis in under 60 seconds.**
>
> Claude Code fixes bugs in your editor. CodeAutopsy fixes fires in your production system.

A multi-agent pipeline + MCP server that automatically correlates logs, deployments, distributed traces, runbooks, and past incidents to produce a specific root cause and fix, streamed live to an engineer's browser.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Browser (SSE)                        │
│            frontend/index.html                          │
└───────────────────────┬────────────────────────────────-┘
                        │ POST /analyze (SSE stream)
┌───────────────────────▼─────────────────────────────────┐
│              FastAPI API Server (api_server.py)         │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│           Multi-Agent Orchestrator Pipeline             │
│                                                         │
│  Triage Agent      → service, error type                │
│  (parallel)                                             │
│  Context Gatherer → logs, deployments, traces           │
│  History Agent    → runbooks, past incidents            │
│  Analyst Agent    → root cause + fix (w/ thinking)      │
└───────────────────────┬─────────────────────────────────┘
                        │ tool_use
┌───────────────────────▼─────────────────────────────────┐
│              claude-opus-4-6 (Anthropic API)            │
└─────────────────────────────────────────────────────────┘

Separately:
┌─────────────────────────────────────────────────────────┐
│        MCP Server (mcp_server/server.py)                │
│  Exposes 6 tools via stdio — connects to Claude Desktop │
└─────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Install dependencies

```bash
cd codeautopsy
pip install -r requirements.txt
```

### 2. Set your API key

```bash
# Create a .env file (or export the variable)
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

### 3. Start the API server

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Open the frontend

Open `frontend/index.html` in your browser (double-click or `File → Open`).

Click **Load Demo** then **Analyse Incident** to watch the agents work in real time.

---

## Demo Scenario

The mock data tells a coherent production story:

| Signal | Data |
|--------|------|
| **Service** | `payment-service` |
| **Error** | `SocketTimeoutException: Read timed out after 30000ms calling inventory-service` |
| **Root cause** | Deploy 25 min ago changed `inventory.http.timeout.ms` from 60 000 → 30 000 ms |
| **Why it matters** | `inventory-service` P95 latency is 28-32 s under load — now exceeds the timeout |
| **Fix** | Revert timeout to 60 000 ms (or use feature flag) |
| **Past incident** | `INC-4821` — identical issue, resolved in 18 min by reverting the config |

---

## Connecting the MCP Server to Claude Desktop

Add the following to your Claude Desktop config:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "codeautopsy": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/codeautopsy"
    }
  }
}
```

Restart Claude Desktop. You can then ask Claude:

> "payment-service is throwing SocketTimeoutException. Fetch its logs and recent deployments, then tell me the root cause."

Claude will use the `fetch_logs`, `get_recent_deployments`, and other CodeAutopsy tools automatically.

---

## Connecting to Claude Code (MCP)

Add to your Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "codeautopsy": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/codeautopsy"
    }
  }
}
```

Or use the CLI:

```bash
claude mcp add codeautopsy -- python -m mcp_server.server
```

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `fetch_logs` | Recent logs from CloudWatch/Datadog (mock) |
| `get_recent_deployments` | Deployment history with diffs (mock GitHub Actions) |
| `fetch_distributed_trace` | Span tree from Jaeger/Zipkin (mock) |
| `search_runbooks` | Confluence/Notion runbook search (mock) |
| `search_past_incidents` | Historical incident search (mock) |
| `get_service_dependencies` | Service dependency map |

---

## File Structure

```
codeautopsy/
├── mcp_server/
│   ├── server.py          # MCP server (connects to Claude Desktop/Code)
│   └── mock_data.py       # Realistic mock data — coherent demo story
├── orchestrator/
│   ├── orchestrator.py    # Multi-agent pipeline (async generator + SSE)
│   └── agents.py          # Triage, Context, History, Analyst agents
├── api_server.py          # FastAPI + SSE endpoint
├── frontend/
│   └── index.html         # Live streaming UI
├── requirements.txt
└── README.md
```

---

## CLI Mode

Run the orchestrator directly from the command line:

```bash
cd codeautopsy
python -m orchestrator.orchestrator
```

Or with a custom alert:

```bash
python -m orchestrator.orchestrator "ERROR: auth-service 500 on /login — NullPointerException at UserService.java:88"
```

---

## API Reference

### `POST /analyze`

Start an analysis. Returns a Server-Sent Events stream.

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"alert_text": "payment-service SocketTimeoutException..."}' \
  --no-buffer
```

### `POST /analyze/demo`

Run the pre-built demo scenario (no body required).

### `GET /demo-alert`

Returns the demo alert text for pre-filling UIs.

### `GET /health`

Health check.

---

## Requirements

- Python 3.11+
- `ANTHROPIC_API_KEY` with access to `claude-opus-4-6`
- No real external APIs needed — all data is mocked

---

## Pitch

Production fires are expensive. Engineers spend 30-60 minutes jumping between Datadog, GitHub, Jaeger, Confluence, and PagerDuty to correlate what went wrong.

CodeAutopsy does that in under 60 seconds:

1. **MCP architecture** — plugs into any Claude-powered tool (Claude Desktop, Claude Code, your own app)
2. **Multi-agent reasoning** — each agent is specialised; you see their live thinking
3. **Coherent story** — not just logs, but the *causal chain*: deploy → config change → timeout mismatch → cascade
4. **Actionable output** — specific fix with code snippet, runbook reference, ETA
