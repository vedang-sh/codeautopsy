"""
CodeAutopsy Orchestrator
========================
Multi-agent pipeline for AI-powered incident root cause analysis.

Pipeline:
  1. Triage Agent       → extract service, error type
  2. Context Gatherer   → fetch logs, deployments, trace (tool_use via Claude)
  3. History Agent      → search runbooks + past incidents (in parallel with context)
  4. Analyst Agent      → synthesise → root cause + fix

Usage:
    from orchestrator.orchestrator import run_analysis

    async for event in run_analysis(alert_text):
        print(event)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncGenerator

import anthropic
from dotenv import load_dotenv

from orchestrator.agents import (
    triage_agent,
    context_gatherer_agent,
    history_agent,
    analyst_agent,
)

load_dotenv()


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Create a .env file with: ANTHROPIC_API_KEY=your-key-here"
        )
    return anthropic.Anthropic(api_key=api_key)


async def _drain_agent(gen) -> tuple[list[dict], dict | None]:
    """
    Collect all events from an async generator.
    Returns (all_events, last_result_data).
    """
    events = []
    result_data = None
    async for event in gen:
        events.append(event)
        if event.get("type") == "result":
            result_data = event.get("data")
    return events, result_data


async def run_analysis(
    alert_text: str,
) -> AsyncGenerator[dict, None]:
    """
    Main orchestrator. Yields SSE-compatible dicts as each agent runs.

    Event schema:
      {"phase": str, "agent": str, "type": str, ...extra}

    Types:
      pipeline_start     — analysis begins
      agent_start        — agent is starting
      thinking           — streaming token from Claude
      thinking_block     — extended thinking block token
      tool_call          — agent is calling a tool
      tool_result        — tool returned data
      agent_complete     — agent finished (includes summary)
      pipeline_complete  — full analysis done, includes final_analysis
      error              — something went wrong
    """
    start_time = time.time()
    client = _get_client()

    yield {
        "phase": "pipeline",
        "type": "pipeline_start",
        "message": "CodeAutopsy incident analysis started",
        "alert_preview": alert_text[:200],
    }

    # ------------------------------------------------------------------
    # Phase 1: Triage
    # ------------------------------------------------------------------
    yield {"phase": "triage", "type": "agent_start", "agent": "Triage Agent",
           "message": "Identifying affected service and error type…"}

    triage_result = None
    async for event in triage_agent(alert_text, client):
        event["phase"] = "triage"
        if event.get("type") == "result":
            triage_result = event["data"]
            yield {
                "phase": "triage",
                "type": "agent_complete",
                "agent": "Triage Agent",
                "summary": (
                    f"Service: **{triage_result.get('service_name')}** | "
                    f"Error: **{triage_result.get('error_type')}** | "
                    f"Confidence: {triage_result.get('confidence_pct')}%"
                ),
                "data": triage_result,
            }
        else:
            yield event

    if not triage_result:
        yield {"phase": "triage", "type": "error", "message": "Triage failed to identify service"}
        return

    # ------------------------------------------------------------------
    # Phase 2: Context + History (parallel)
    # ------------------------------------------------------------------
    yield {
        "phase": "gather",
        "type": "agent_start",
        "agent": "Context Gatherer + History Agent",
        "message": "Gathering logs, deployments, traces, runbooks, past incidents in parallel…",
    }

    # Run context gatherer and history agent concurrently
    context_gen = context_gatherer_agent(triage_result, client)
    history_gen = history_agent(triage_result, client)

    context_task = asyncio.create_task(_drain_agent(context_gen))
    history_task = asyncio.create_task(_drain_agent(history_gen))

    # Interleave events from both agents as they complete
    done, pending = await asyncio.wait(
        [context_task, history_task],
        return_when=asyncio.ALL_COMPLETED,
    )

    # Collect results
    context_events, context_data = context_task.result()
    history_events, history_data = history_task.result()

    # Yield context events
    for event in context_events:
        event["phase"] = "context"
        if event.get("type") == "result":
            yield {
                "phase": "context",
                "type": "agent_complete",
                "agent": "Context Gatherer",
                "summary": _summarise_context(context_data or {}),
                "data": context_data,
            }
        else:
            yield event

    # Yield history events
    for event in history_events:
        event["phase"] = "history"
        if event.get("type") == "result":
            rb = (history_data or {}).get("search_runbooks", {})
            inc = (history_data or {}).get("search_past_incidents", {})
            yield {
                "phase": "history",
                "type": "agent_complete",
                "agent": "History Agent",
                "summary": (
                    f"Runbooks: {rb.get('total_found', 0)} | "
                    f"Past incidents: {inc.get('total_found', 0)}"
                ),
                "data": history_data,
            }
        else:
            yield event

    context_data = context_data or {}
    history_data = history_data or {}

    # ------------------------------------------------------------------
    # Phase 3: Analysis
    # ------------------------------------------------------------------
    yield {
        "phase": "analysis",
        "type": "agent_start",
        "agent": "Analyst Agent",
        "message": "Synthesising all context → root cause analysis…",
    }

    analysis_result = None
    async for event in analyst_agent(
        triage=triage_result,
        context=context_data,
        history=history_data,
        alert_text=alert_text,
        client=client,
    ):
        event["phase"] = "analysis"
        if event.get("type") == "result":
            analysis_result = event["data"]
            yield {
                "phase": "analysis",
                "type": "agent_complete",
                "agent": "Analyst Agent",
                "summary": (
                    f"Root cause identified | Confidence: "
                    f"{analysis_result.get('confidence_pct')}%"
                ),
                "data": analysis_result,
            }
        else:
            yield event

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    elapsed = round(time.time() - start_time, 1)
    yield {
        "phase": "pipeline",
        "type": "pipeline_complete",
        "elapsed_seconds": elapsed,
        "final_analysis": analysis_result or {},
        "triage": triage_result,
        "message": f"Analysis complete in {elapsed}s",
    }


def _summarise_context(ctx: dict) -> str:
    parts = []
    if ctx.get("fetch_logs"):
        ec = ctx["fetch_logs"].get("error_count", 0)
        parts.append(f"Logs: {ec} errors")
    if ctx.get("get_recent_deployments"):
        deps = ctx["get_recent_deployments"].get("deployments", [])
        if deps:
            d = deps[0]
            min_ago = d.get("minutes_before_incident")
            s = f"Deployment: {d.get('version')} "
            if min_ago is not None:
                s += f"({min_ago} min before incident)"
            parts.append(s)
    if ctx.get("fetch_distributed_trace"):
        dur = ctx["fetch_distributed_trace"].get("total_duration_ms", "?")
        parts.append(f"Trace: {dur}ms {ctx['fetch_distributed_trace'].get('status', '')}")
    return " | ".join(parts) if parts else "Context gathered"


# ---------------------------------------------------------------------------
# CLI entry point for quick testing
# ---------------------------------------------------------------------------

DEMO_ALERT = """\
ALERT: payment-service — High Error Rate (P1)
Environment: production
Error rate: 71% (up from 0%)

Stack trace:
java.net.SocketTimeoutException: Read timed out after 30000ms calling inventory-service
    at com.example.payment.client.InventoryClient.checkStock(InventoryClient.java:84)
    at com.example.payment.service.PaymentService.processPayment(PaymentService.java:156)
    at com.example.payment.controller.PaymentController.pay(PaymentController.java:72)

Errors began approximately 24 minutes ago.
Affected endpoints: POST /v1/payments (71% failure rate)
"""

DEMO_ALERTS = {
    "payment-service": DEMO_ALERT,
    "auth-service": """\
ALERT: auth-service — Login Failures Spiking (P1)
Environment: production
Error rate: 52% (up from 0%)

Stack trace:
java.lang.NullPointerException: Cannot invoke String.isEmpty() on null token
    at com.example.auth.service.UserService.validateToken(UserService.java:88)
    at com.example.auth.filter.JwtAuthFilter.doFilterInternal(JwtAuthFilter.java:54)
    at org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:117)

Errors began approximately 38 minutes ago.
Affected endpoints: POST /v1/auth/login (52% failure rate)
""",
    "order-service": """\
ALERT: order-service — DB Connection Pool Exhausted (P1)
Environment: production
Error rate: 89% (up from 0%)

Exception:
com.zaxxer.hikari.pool.HikariPool$PoolInitializationException:
  HikariPool-1 - Connection is not available, request timed out after 30000ms
    at com.example.order.repository.OrderRepository.save(OrderRepository.java:43)
    at com.example.order.service.OrderService.createOrder(OrderService.java:112)

Errors began approximately 61 minutes ago.
Affected endpoints: POST /v1/orders (89% failure rate)
""",
    "notification-service": """\
ALERT: notification-service — Kafka Consumer Lag Critical (P2)
Environment: production
Consumer lag on topic order.confirmed: 187,400 messages (was 320 one hour ago)

Error in logs:
Consumer thread blocked for 45000ms in EmailSenderService.sendWithRetry()
  — possible blocking I/O on HTTP call to delivery-tracker-api

Lag spike began approximately 53 minutes ago.
Messages are not being processed. Notification backlog growing at ~3500/min.
""",
}


async def _cli_main():
    import sys
    alert = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEMO_ALERT
    print(f"\n{'='*60}")
    print("CodeAutopsy — Incident Root Cause Analysis")
    print(f"{'='*60}\n")

    async for event in run_analysis(alert):
        t = event.get("type", "")
        agent = event.get("agent", "")
        phase = event.get("phase", "")

        if t == "pipeline_start":
            print(f"\n🚨 {event['message']}\n")
        elif t == "agent_start":
            print(f"\n[{phase.upper()}] ▶ {event['message']}")
        elif t == "tool_call":
            print(f"  🔧 {event.get('message', '')}")
        elif t == "tool_result":
            print(f"  ✅ {event.get('tool')}: {event.get('summary', '')}")
        elif t == "agent_complete":
            print(f"\n  ✓ {agent}: {event.get('summary', '')}")
        elif t == "pipeline_complete":
            fa = event.get("final_analysis", {})
            print(f"\n{'='*60}")
            print("📊 FINAL ANALYSIS")
            print(f"{'='*60}")
            print(f"Root cause:   {fa.get('root_cause', 'N/A')}")
            print(f"Confidence:   {fa.get('confidence_pct', '?')}%")
            print(f"\nContributing factors:")
            for f in fa.get("contributing_factors", []):
                print(f"  • {f}")
            print(f"\nFix:          {fa.get('recommended_fix', 'N/A')}")
            if fa.get("fix_code_snippet"):
                print(f"\nCode:\n{fa['fix_code_snippet']}")
            print(f"\nRunbook:      {fa.get('runbook_reference', 'N/A')}")
            print(f"Past incident:{fa.get('past_incident_reference', 'N/A')}")
            print(f"ETA to fix:   {fa.get('time_to_resolve_estimate_minutes', '?')} minutes")
            print(f"\nCompleted in {event.get('elapsed_seconds')}s")


if __name__ == "__main__":
    asyncio.run(_cli_main())
