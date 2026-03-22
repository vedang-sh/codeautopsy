"""
Supabase persistence layer for CodeAutopsy.

Table schema (run once in Supabase SQL editor):
    CREATE TABLE incidents (
        id         BIGSERIAL PRIMARY KEY,
        service    TEXT,
        error_type TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        data       JSONB
    );
    CREATE INDEX idx_incidents_service    ON incidents(service);
    CREATE INDEX idx_incidents_error_type ON incidents(error_type);
    CREATE INDEX idx_incidents_data       ON incidents USING GIN (data);
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if url and key:
            from supabase import create_client
            _client = create_client(url, key)
    return _client


def save_incident(triage: dict, analysis: dict) -> bool:
    """Save a completed incident analysis to Supabase. Returns True on success."""
    try:
        client = _get_client()
        if not client:
            return False

        service = triage.get("service_name", "unknown")
        error_type = triage.get("error_type", "unknown")

        data = {
            "title": f"{service} — {error_type}",
            "root_cause": analysis.get("root_cause", ""),
            "resolution": analysis.get("recommended_fix", ""),
            "confidence_pct": analysis.get("confidence_pct", 0),
            "contributing_factors": analysis.get("contributing_factors", []),
            "fix_code_snippet": analysis.get("fix_code_snippet"),
            "runbook_reference": analysis.get("runbook_reference"),
            "time_to_resolve_minutes": analysis.get("time_to_resolve_estimate_minutes"),
            "escalation_needed": analysis.get("escalation_needed", False),
            "triage": triage,
            "analysis": analysis,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }

        client.table("incidents").insert({
            "service": service,
            "error_type": error_type,
            "data": data,
        }).execute()
        return True
    except Exception as e:
        print(f"[Supabase] save_incident failed: {e}", flush=True)
        return False


def find_cached_incident(service: str, error_type: str, min_confidence: int = 85) -> dict | None:
    """
    Look for a recent high-confidence resolved incident for the same service + error type.
    Returns the full analysis dict if found, else None.
    """
    try:
        client = _get_client()
        if not client:
            return None

        result = (
            client.table("incidents")
            .select("id, service, error_type, created_at, data")
            .eq("service", service)
            .eq("error_type", error_type)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        row = result.data[0]
        d = row.get("data", {})
        confidence = d.get("analysis", {}).get("confidence_pct", d.get("confidence_pct", 0))
        if confidence < min_confidence:
            return None

        print(f"[Supabase] Cache hit for {service}/{error_type} "
              f"(confidence={confidence}%, recorded={row['created_at'][:10]})", flush=True)
        return d.get("analysis", {})
    except Exception as e:
        print(f"[Supabase] find_cached_incident failed: {e}", flush=True)
        return None


def query_incidents(service: str = "", error_type: str = "", limit: int = 10) -> list[dict]:
    """
    Fetch past incidents from Supabase.
    Returns a list of incident dicts (the JSONB data column + metadata).
    """
    try:
        client = _get_client()
        if not client:
            return []

        query = client.table("incidents").select("id, service, error_type, created_at, data")

        if service:
            query = query.eq("service", service)

        result = query.order("created_at", desc=True).limit(limit).execute()

        incidents = []
        for row in result.data:
            d = row.get("data", {})
            incidents.append({
                "id": f"INC-DB-{row['id']}",
                "title": d.get("title", f"{row['service']} — {row['error_type']}"),
                "date": row["created_at"][:10],
                "duration_minutes": d.get("time_to_resolve_minutes"),
                "severity": "P1",
                "affected_services": [row["service"]],
                "error_pattern": f"{row['error_type']} {row['service']}",
                "root_cause": d.get("root_cause", ""),
                "resolution": d.get("resolution", ""),
                "similarity_score": 0.95,
                "source": "database",
            })
        return incidents
    except Exception as e:
        print(f"[Supabase] query_incidents failed: {e}", flush=True)
        return []
