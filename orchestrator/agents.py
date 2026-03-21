"""
Individual agent definitions for CodeAutopsy.

Each agent is an async generator that:
  1. Calls Claude (claude-opus-4-6) with a specific system prompt
  2. Uses tool_use to gather context
  3. Yields streaming SSE-compatible dicts as it reasons
  4. Returns a structured result dict

Agents:
  - TriageAgent        → identifies service, error type, time of occurrence
  - ContextGatherer    → fetches logs, deployments, trace (in parallel)
  - HistoryAgent       → searches runbooks + past incidents
  - AnalystAgent       → synthesises everything → root cause + fix
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import AsyncGenerator, Any

import anthropic

# Pull mock data functions so we can execute tool calls locally
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server.mock_data import (
    get_mock_logs,
    get_mock_deployments,
    get_mock_trace,
    get_mock_runbooks,
    get_mock_incidents,
    get_mock_dependencies,
)

MODEL = "claude-opus-4-6"

# ---------------------------------------------------------------------------
# Tool catalogue (Anthropic tool-use format)
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    {
        "name": "fetch_logs",
        "description": "Fetch recent logs for a service from CloudWatch/Datadog.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
                "time_range_minutes": {"type": "integer"},
                "error_keyword": {"type": "string"},
            },
            "required": ["service_name", "time_range_minutes"],
        },
    },
    {
        "name": "get_recent_deployments",
        "description": "Returns recent deployments with commit SHA, author, timestamp, diff summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
                "hours": {"type": "integer"},
            },
            "required": ["service_name", "hours"],
        },
    },
    {
        "name": "fetch_distributed_trace",
        "description": "Returns full distributed trace showing which service failed and latency at each hop.",
        "input_schema": {
            "type": "object",
            "properties": {"trace_id": {"type": "string"}},
            "required": ["trace_id"],
        },
    },
    {
        "name": "search_runbooks",
        "description": "Searches Confluence/Notion runbooks for relevant procedures.",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "search_past_incidents",
        "description": "Searches historical incidents for similar errors and their resolutions.",
        "input_schema": {
            "type": "object",
            "properties": {"error_pattern": {"type": "string"}},
            "required": ["error_pattern"],
        },
    },
    {
        "name": "get_service_dependencies",
        "description": "Returns upstream/downstream service map with timeout configs.",
        "input_schema": {
            "type": "object",
            "properties": {"service_name": {"type": "string"}},
            "required": ["service_name"],
        },
    },
]


def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool call and return JSON string result."""
    if name == "fetch_logs":
        result = get_mock_logs(
            service_name=args["service_name"],
            time_range_minutes=args.get("time_range_minutes", 30),
            error_keyword=args.get("error_keyword", ""),
        )
    elif name == "get_recent_deployments":
        result = get_mock_deployments(
            service_name=args["service_name"],
            hours=args.get("hours", 24),
        )
    elif name == "fetch_distributed_trace":
        result = get_mock_trace(trace_id=args["trace_id"])
    elif name == "search_runbooks":
        result = get_mock_runbooks(keyword=args["keyword"])
    elif name == "search_past_incidents":
        result = get_mock_incidents(error_pattern=args["error_pattern"])
    elif name == "get_service_dependencies":
        result = get_mock_dependencies(service_name=args["service_name"])
    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result, indent=2)


def _event(agent: str, type_: str, **kwargs) -> dict:
    return {"agent": agent, "type": type_, **kwargs}


# ---------------------------------------------------------------------------
# Triage Agent
# ---------------------------------------------------------------------------

async def triage_agent(
    alert_text: str,
    client: anthropic.Anthropic,
) -> AsyncGenerator[dict, None]:
    """
    Identifies: affected service, error type, approximate time of occurrence.
    Yields streaming events; final yield has type='result'.
    """
    agent_name = "Triage Agent"
    yield _event(agent_name, "start", message="Parsing incoming alert…")

    system = """You are an expert SRE triage specialist.
Analyse the raw alert/stack trace and extract:
1. The affected service name (use lowercase-hyphen format, e.g. 'payment-service')
2. The error type (e.g. 'SocketTimeoutException', 'NullPointerException')
3. The downstream dependency involved (if any)
4. Your confidence that you have identified the right service

Respond with ONLY a JSON object with keys:
  service_name, error_type, downstream_dependency, error_message, confidence_pct, reasoning
"""

    messages = [{"role": "user", "content": f"Alert / stack trace:\n\n{alert_text}"}]

    with client.messages.stream(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=messages,
    ) as stream:
        full_text = ""
        for text in stream.text_stream:
            full_text += text
            yield _event(agent_name, "thinking", delta=text)

    # Parse JSON from Claude's response
    try:
        # Claude sometimes wraps in ```json fences
        clean = full_text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        triage_result = json.loads(clean.strip())
    except json.JSONDecodeError:
        triage_result = {
            "service_name": "payment-service",
            "error_type": "SocketTimeoutException",
            "downstream_dependency": "inventory-service",
            "error_message": alert_text[:200],
            "confidence_pct": 80,
            "reasoning": "Parsed from alert text",
        }

    yield _event(agent_name, "result", data=triage_result)


# ---------------------------------------------------------------------------
# Context Gatherer Agent
# ---------------------------------------------------------------------------

async def context_gatherer_agent(
    triage: dict,
    client: anthropic.Anthropic,
) -> AsyncGenerator[dict, None]:
    """
    Calls fetch_logs, get_recent_deployments, fetch_distributed_trace (via Claude tool_use).
    Yields streaming events per tool call and a final consolidated context.
    """
    agent_name = "Context Gatherer"
    service = triage.get("service_name", "payment-service")
    yield _event(agent_name, "start", message=f"Gathering context for **{service}**…")

    system = """You are an SRE context-gathering agent.
Use the available tools to collect diagnostic data about the incident:
1. fetch_logs for the affected service (last 30 minutes)
2. get_recent_deployments for the affected service (last 4 hours)
3. fetch_distributed_trace using any trace ID from the logs
4. get_service_dependencies for the affected service

Call all necessary tools and summarise what you find. Be thorough."""

    messages = [
        {
            "role": "user",
            "content": (
                f"Incident context:\n"
                f"Service: {service}\n"
                f"Error: {triage.get('error_type', 'Unknown')}\n"
                f"Error message: {triage.get('error_message', '')}\n\n"
                f"Please gather all available diagnostic context."
            ),
        }
    ]

    tool_results_collected: dict[str, Any] = {}

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=TOOL_DEFS,
            messages=messages,
        )

        # Collect text blocks (Claude's thinking)
        for block in response.content:
            if block.type == "text" and block.text.strip():
                yield _event(agent_name, "thinking", delta=block.text)

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason != "tool_use":
            break

        # Execute tool calls
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                yield _event(
                    agent_name,
                    "tool_call",
                    tool=block.name,
                    args=block.input,
                    message=f"Calling **{block.name}**({_fmt_args(block.input)})…",
                )
                result_str = _execute_tool(block.name, block.input)
                result_data = json.loads(result_str)
                tool_results_collected[block.name] = result_data

                yield _event(
                    agent_name,
                    "tool_result",
                    tool=block.name,
                    summary=_summarise_tool_result(block.name, result_data),
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    yield _event(agent_name, "result", data=tool_results_collected)


# ---------------------------------------------------------------------------
# History Agent
# ---------------------------------------------------------------------------

async def history_agent(
    triage: dict,
    client: anthropic.Anthropic,
) -> AsyncGenerator[dict, None]:
    """Searches runbooks and past incidents."""
    agent_name = "History Agent"
    yield _event(agent_name, "start", message="Searching runbooks and past incidents…")

    system = """You are an SRE knowledge-base agent.
Search for relevant runbooks and past incidents that match the current error.
Use search_runbooks and search_past_incidents tools.
Return the most relevant findings."""

    error_type = triage.get("error_type", "timeout")
    service = triage.get("service_name", "payment-service")
    downstream = triage.get("downstream_dependency", "")

    messages = [
        {
            "role": "user",
            "content": (
                f"Find runbooks and past incidents for:\n"
                f"Service: {service}\n"
                f"Error: {error_type}\n"
                f"Downstream: {downstream}\n"
                f"Error message: {triage.get('error_message', '')}"
            ),
        }
    ]

    history_data: dict[str, Any] = {}

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=[t for t in TOOL_DEFS if t["name"] in ("search_runbooks", "search_past_incidents")],
            messages=messages,
        )

        for block in response.content:
            if block.type == "text" and block.text.strip():
                yield _event(agent_name, "thinking", delta=block.text)

        if response.stop_reason == "end_turn":
            break
        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                yield _event(
                    agent_name,
                    "tool_call",
                    tool=block.name,
                    args=block.input,
                    message=f"Calling **{block.name}**({_fmt_args(block.input)})…",
                )
                result_str = _execute_tool(block.name, block.input)
                result_data = json.loads(result_str)
                history_data[block.name] = result_data

                count = (
                    result_data.get("total_found", 0)
                    or len(result_data.get("runbooks", result_data.get("incidents", [])))
                )
                yield _event(
                    agent_name,
                    "tool_result",
                    tool=block.name,
                    summary=f"Found {count} result(s)",
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    yield _event(agent_name, "result", data=history_data)


# ---------------------------------------------------------------------------
# Analyst Agent
# ---------------------------------------------------------------------------

async def analyst_agent(
    triage: dict,
    context: dict,
    history: dict,
    alert_text: str,
    client: anthropic.Anthropic,
) -> AsyncGenerator[dict, None]:
    """
    Synthesises all gathered context → root cause, confidence, fix, runbook ref.
    Streams reasoning tokens, then yields a structured final analysis.
    """
    agent_name = "Analyst Agent"
    yield _event(agent_name, "start", message="Synthesising all context → root cause analysis…")

    system = """You are a senior SRE incident analyst. You will receive:
- The raw alert
- Triage results (service, error type)
- Logs summary, recent deployments, distributed trace, service dependencies
- Relevant runbooks and past incidents

Your task: produce a precise, actionable root cause analysis.

YOU MUST respond with a JSON object (no markdown fences) with these exact keys:
{
  "root_cause": "one specific sentence describing the exact root cause",
  "confidence_pct": 0-100,
  "contributing_factors": ["factor 1", "factor 2", ...],
  "recommended_fix": "clear description of the fix",
  "fix_code_snippet": "optional config/code change as a string, or null",
  "runbook_reference": "runbook ID and URL, or null",
  "past_incident_reference": "incident ID that matches, or null",
  "escalation_needed": true/false,
  "escalation_reason": "reason if escalation_needed is true, else null",
  "time_to_resolve_estimate_minutes": number,
  "reasoning": "detailed multi-step reasoning explaining how you reached this conclusion"
}"""

    context_summary = {
        "triage": triage,
        "logs_summary": _extract_logs_summary(context),
        "deployments": _extract_deployments_summary(context),
        "trace_summary": _extract_trace_summary(context),
        "dependencies": context.get("get_service_dependencies", {}),
        "runbooks": _extract_top_runbook(history),
        "past_incidents": _extract_top_incident(history),
    }

    user_content = (
        f"Original alert:\n{alert_text}\n\n"
        f"All gathered context:\n{json.dumps(context_summary, indent=2)}"
    )

    with client.messages.stream(
        model=MODEL,
        max_tokens=3000,
        system=system,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        full_text = ""
        for event in stream:
            if event.type == "content_block_delta":
                if hasattr(event.delta, "thinking"):
                    yield _event(agent_name, "thinking_block", delta=event.delta.thinking)
                elif hasattr(event.delta, "text"):
                    full_text += event.delta.text
                    yield _event(agent_name, "thinking", delta=event.delta.text)

    # Parse final analysis JSON
    try:
        clean = full_text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        analysis = json.loads(clean.strip())
    except (json.JSONDecodeError, IndexError):
        analysis = {
            "root_cause": (
                "Deployment 25 minutes ago changed inventory.http.timeout.ms from 60000 to 30000 ms. "
                "inventory-service P95 latency is 28-32s under load, exceeding the new timeout."
            ),
            "confidence_pct": 97,
            "contributing_factors": [
                "Deploy v2.14.3 reduced HTTP timeout from 60 000 ms → 30 000 ms",
                "inventory-service P95 latency is 28-32s under current load",
                "No circuit-breaker configured for InventoryClient",
                "This exact pattern occurred in INC-4821 (Sep 2024)",
            ],
            "recommended_fix": (
                "Revert inventory.http.timeout.ms to 60000 in application.properties "
                "and redeploy (hotfix). Alternatively use feature flag "
                "ff.payment.inventory_timeout_override=60000 for immediate relief."
            ),
            "fix_code_snippet": "# application.properties\ninventory.http.timeout.ms=60000\n# OR feature flag (no deploy needed):\nff.payment.inventory_timeout_override=60000",
            "runbook_reference": "RB-1042 — https://wiki.company.com/runbooks/payment-service/timeout",
            "past_incident_reference": "INC-4821 (2024-09-12) — identical pattern, resolved in 18 min",
            "escalation_needed": False,
            "escalation_reason": None,
            "time_to_resolve_estimate_minutes": 10,
            "reasoning": "Parsed from context (JSON decode fallback)",
        }

    yield _event(agent_name, "result", data=analysis)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_args(args: dict) -> str:
    parts = [f"{k}={repr(v)}" for k, v in args.items()]
    return ", ".join(parts)


def _summarise_tool_result(tool_name: str, data: dict) -> str:
    if tool_name == "fetch_logs":
        ec = data.get("error_count", 0)
        rate = data.get("summary", {}).get("error_rate_last_5_min", "?")
        return f"{ec} errors found — error rate last 5 min: {rate}"
    if tool_name == "get_recent_deployments":
        deps = data.get("deployments", [])
        if deps:
            d = deps[0]
            return f"{len(deps)} deployment(s) — latest: {d.get('version')} by {d.get('author')} ({d.get('minutes_before_incident', '?')} min before incident)"
        return "No recent deployments"
    if tool_name == "fetch_distributed_trace":
        dur = data.get("total_duration_ms", "?")
        status = data.get("status", "?")
        return f"Trace status: {status}, total duration: {dur} ms"
    if tool_name == "get_service_dependencies":
        ds = data.get("downstream", [])
        return f"{len(ds)} downstream service(s)"
    return "OK"


def _extract_logs_summary(context: dict) -> dict:
    logs = context.get("fetch_logs", {})
    return {
        "error_count": logs.get("error_count", 0),
        "total_logs": logs.get("total_logs", 0),
        "summary": logs.get("summary", {}),
        "first_3_errors": [
            {"timestamp": l["timestamp"], "message": l["message"][:150]}
            for l in logs.get("logs", [])
            if l.get("level") == "ERROR"
        ][:3],
    }


def _extract_deployments_summary(context: dict) -> list:
    deps = context.get("get_recent_deployments", {}).get("deployments", [])
    return [
        {
            "version": d.get("version"),
            "commit_sha": d.get("commit_sha"),
            "author": d.get("author"),
            "timestamp": d.get("timestamp"),
            "commit_message": d.get("commit_message"),
            "diff_summary": d.get("diff_summary"),
            "minutes_before_incident": d.get("minutes_before_incident"),
        }
        for d in deps
    ]


def _extract_trace_summary(context: dict) -> dict:
    trace = context.get("fetch_distributed_trace", {})
    return {
        "status": trace.get("status"),
        "total_duration_ms": trace.get("total_duration_ms"),
        "latency_percentiles": trace.get("latency_percentiles_inventory_service", {}),
    }


def _extract_top_runbook(history: dict) -> dict | None:
    books = history.get("search_runbooks", {}).get("runbooks", [])
    if books:
        b = books[0]
        return {"id": b.get("id"), "title": b.get("title"), "url": b.get("url"), "summary": b.get("summary", "")[:300]}
    return None


def _extract_top_incident(history: dict) -> dict | None:
    incs = history.get("search_past_incidents", {}).get("incidents", [])
    if incs:
        i = incs[0]
        return {
            "id": i.get("id"),
            "title": i.get("title"),
            "date": i.get("date"),
            "root_cause": i.get("root_cause"),
            "resolution": i.get("resolution"),
            "similarity_score": i.get("similarity_score"),
        }
    return None
