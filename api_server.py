"""
CodeAutopsy API Server
======================
FastAPI server that exposes the multi-agent orchestrator via HTTP + SSE.

Endpoints:
  POST /analyze        → runs analysis, returns Server-Sent Events stream
  POST /analyze/demo   → runs the pre-built payment-service demo scenario
  GET  /health         → health check

Start:
    uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import json
import traceback
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orchestrator.orchestrator import run_analysis, DEMO_ALERT, DEMO_ALERTS

app = FastAPI(
    title="CodeAutopsy",
    description="AI-powered incident root cause analysis",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class IncidentRequest(BaseModel):
    alert_text: str


async def _sse_stream(alert_text: str) -> AsyncGenerator[str, None]:
    """Wrap the orchestrator generator as Server-Sent Events."""
    try:
        async for event in run_analysis(alert_text):
            # Filter out raw streaming tokens for the SSE feed to keep it clean
            # (they're captured in agent_complete summaries)
            if event.get("type") in ("thinking", "thinking_block"):
                # Still send thinking but abbreviated
                delta = event.get("delta", "")
                if delta and len(delta) > 1:
                    yield f"data: {json.dumps({'type': 'thinking', 'agent': event.get('agent', ''), 'delta': delta})}\n\n"
            else:
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0)  # yield control to event loop

    except Exception as e:
        tb = traceback.format_exc()
        yield f"data: {json.dumps({'type': 'error', 'message': str(e), 'traceback': tb})}\n\n"

    yield "data: [DONE]\n\n"


@app.post("/analyze")
async def analyze(request: IncidentRequest):
    """
    Start an incident analysis. Returns a Server-Sent Events stream.

    Each event is a JSON object. Listen for type='pipeline_complete' for the final result.
    """
    if not request.alert_text.strip():
        raise HTTPException(status_code=400, detail="alert_text cannot be empty")

    return StreamingResponse(
        _sse_stream(request.alert_text),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/analyze/demo")
async def analyze_demo():
    """
    Run the pre-built payment-service demo scenario.
    Perfect for hackathon demos — always tells the same coherent story.
    """
    return StreamingResponse(
        _sse_stream(DEMO_ALERT),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "codeautopsy"}


@app.get("/demo-alert")
async def get_demo_alert():
    return {"alert_text": DEMO_ALERT}


@app.get("/demo-scenarios")
async def get_demo_scenarios():
    return {
        "scenarios": [
            {"id": "payment-service",      "label": "💳 payment-service — SocketTimeoutException"},
            {"id": "auth-service",         "label": "🔐 auth-service — NullPointerException"},
            {"id": "order-service",        "label": "📦 order-service — DB Pool Exhaustion"},
            {"id": "notification-service", "label": "🔔 notification-service — Kafka Consumer Lag"},
        ]
    }


@app.get("/demo-alert/{scenario}")
async def get_scenario_alert(scenario: str):
    alert = DEMO_ALERTS.get(scenario)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    return {"alert_text": alert}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
