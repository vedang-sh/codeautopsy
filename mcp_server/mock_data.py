"""
Realistic mock data for CodeAutopsy demo.

Demo scenario:
  - Service:  payment-service
  - Error:    java.net.SocketTimeoutException: Read timed out after 30000ms calling inventory-service
  - Root cause: Deployment 25 minutes ago changed HTTP timeout from 60 000 ms → 30 000 ms.
                inventory-service P95 latency is ~28-32 s under load, which now exceeds the timeout.
  - Resolution: revert timeout config (past incident confirms this).
"""

from datetime import datetime, timedelta, timezone
import json

_NOW = datetime.now(timezone.utc)


def _ts(minutes_ago: float, seconds_ago: float = 0) -> str:
    dt = _NOW - timedelta(minutes=minutes_ago, seconds=seconds_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# fetch_logs
# ---------------------------------------------------------------------------

def get_mock_logs(service_name: str, time_range_minutes: int, error_keyword: str = "") -> dict:
    if service_name != "payment-service":
        return {
            "service": service_name,
            "time_range_minutes": time_range_minutes,
            "total_logs": 0,
            "error_count": 0,
            "logs": [],
            "summary": {"note": "No log data available for this service"},
        }

    # Normal traffic before the deployment (25-35 min ago)
    normal_logs = [
        {
            "timestamp": _ts(m),
            "level": "INFO",
            "service": "payment-service",
            "message": f"Payment processed successfully orderId=TXN-{8000 + m} duration=412ms",
            "traceId": f"trace-pre-{m:04d}",
            "thread": f"http-nio-8080-exec-{m % 10}",
        }
        for m in range(25, 36)
    ]

    # Errors starting right after deployment (~24 min ago)
    error_logs = []
    for i in range(1, 25):
        level = "ERROR" if i % 3 != 0 else "WARN"
        if level == "ERROR":
            msg = (
                "java.net.SocketTimeoutException: Read timed out after 30000ms "
                "calling inventory-service [GET /api/v1/stock/check]"
            )
            stack = (
                "java.net.SocketTimeoutException: Read timed out after 30000ms\n"
                "\tat sun.nio.ch.SocketChannelImpl.read(SocketChannelImpl.java:385)\n"
                "\tat com.example.payment.client.InventoryClient.checkStock"
                "(InventoryClient.java:84)\n"
                "\tat com.example.payment.service.PaymentService.processPayment"
                "(PaymentService.java:156)\n"
                "\tat com.example.payment.controller.PaymentController.pay"
                "(PaymentController.java:72)"
            )
        else:
            msg = f"Slow response from inventory-service: {27000 + i * 200}ms (threshold: 5000ms)"
            stack = None

        entry = {
            "timestamp": _ts(i, seconds_ago=i * 7 % 60),
            "level": level,
            "service": "payment-service",
            "message": msg,
            "traceId": f"trace-err-{9000 + i:04d}",
            "thread": f"http-nio-8080-exec-{i % 10}",
        }
        if stack:
            entry["stackTrace"] = stack
        error_logs.append(entry)

    all_logs = error_logs + normal_logs
    all_logs.sort(key=lambda x: x["timestamp"], reverse=True)

    if error_keyword:
        all_logs = [l for l in all_logs if error_keyword.lower() in l["message"].lower()]

    return {
        "service": service_name,
        "time_range_minutes": time_range_minutes,
        "total_logs": len(all_logs),
        "error_count": sum(1 for l in all_logs if l["level"] == "ERROR"),
        "logs": all_logs[:50],
        "summary": {
            "error_rate_last_5_min": "71%",
            "error_rate_prev_30_min": "0%",
            "most_common_error": "SocketTimeoutException",
            "affected_downstream": "inventory-service",
            "first_error_timestamp": _ts(24, seconds_ago=37),
            "errors_per_minute_last_5": 14,
        },
    }


# ---------------------------------------------------------------------------
# get_recent_deployments
# ---------------------------------------------------------------------------

def get_mock_deployments(service_name: str, hours: int) -> dict:
    if service_name == "payment-service":
        return {
            "service": service_name,
            "hours_searched": hours,
            "deployments": [
                {
                    "id": "deploy-7f3a9c",
                    "version": "v2.14.3",
                    "commit_sha": "a3f9c217b",
                    "author": "sarah.chen@company.com",
                    "timestamp": _ts(25),
                    "status": "success",
                    "environment": "production",
                    "changed_files": [
                        "src/main/resources/application.properties",
                        "src/main/java/com/example/payment/client/InventoryClient.java",
                        "src/main/java/com/example/payment/config/HttpClientConfig.java",
                    ],
                    "commit_message": "perf: reduce HTTP client timeouts to improve overall p99 latency",
                    "diff_summary": (
                        "application.properties: inventory.http.timeout.ms changed from 60000 → 30000\n"
                        "HttpClientConfig.java: default connect timeout 10000 → 5000\n"
                        "InventoryClient.java: added retry logic (max 1 retry, no backoff)"
                    ),
                    "minutes_before_incident": 25,
                },
                {
                    "id": "deploy-3b12ef",
                    "version": "v2.14.2",
                    "commit_sha": "e8b214cc1",
                    "author": "james.park@company.com",
                    "timestamp": _ts(hours=72),
                    "status": "success",
                    "environment": "production",
                    "changed_files": [
                        "src/main/java/com/example/payment/service/PaymentService.java",
                        "src/test/java/com/example/payment/PaymentServiceTest.java",
                    ],
                    "commit_message": "feat: add idempotency key support for payment requests",
                    "diff_summary": "PaymentService.java: added idempotency key validation and dedup logic",
                    "minutes_before_incident": None,
                },
            ],
        }

    if service_name == "inventory-service":
        return {
            "service": service_name,
            "hours_searched": hours,
            "deployments": [
                {
                    "id": "deploy-9d4c11",
                    "version": "v3.8.1",
                    "commit_sha": "c9d4118ab",
                    "author": "mike.torres@company.com",
                    "timestamp": _ts(hours=96),
                    "status": "success",
                    "environment": "production",
                    "changed_files": ["src/main/java/com/example/inventory/StockService.java"],
                    "commit_message": "fix: handle concurrent stock check requests under high load",
                    "diff_summary": "StockService.java: added mutex for DB reads under concurrent access",
                    "minutes_before_incident": None,
                }
            ],
        }

    return {"service": service_name, "hours_searched": hours, "deployments": []}


# ---------------------------------------------------------------------------
# fetch_distributed_trace
# ---------------------------------------------------------------------------

def get_mock_trace(trace_id: str) -> dict:
    return {
        "trace_id": trace_id,
        "total_duration_ms": 30482,
        "status": "ERROR",
        "root_span": {
            "span_id": "span-001",
            "service": "api-gateway",
            "operation": "POST /v1/payments",
            "duration_ms": 30482,
            "status": "ERROR",
            "start_time": _ts(3),
            "children": [
                {
                    "span_id": "span-002",
                    "service": "payment-service",
                    "operation": "PaymentController.pay",
                    "duration_ms": 30418,
                    "status": "ERROR",
                    "error": "SocketTimeoutException",
                    "children": [
                        {
                            "span_id": "span-003",
                            "service": "payment-service",
                            "operation": "PaymentService.processPayment",
                            "duration_ms": 30395,
                            "status": "ERROR",
                            "children": [
                                {
                                    "span_id": "span-004",
                                    "service": "payment-service",
                                    "operation": "InventoryClient.checkStock [HTTP GET]",
                                    "duration_ms": 30001,
                                    "status": "TIMEOUT",
                                    "error": "Read timed out after 30000ms",
                                    "http": {
                                        "method": "GET",
                                        "url": "http://inventory-service:8080/api/v1/stock/check",
                                        "timeout_config_ms": 30000,
                                        "actual_wait_ms": 30001,
                                    },
                                    "children": [],
                                },
                                {
                                    "span_id": "span-005",
                                    "service": "inventory-service",
                                    "operation": "StockService.getStockLevels",
                                    "duration_ms": 29870,
                                    "status": "SLOW",
                                    "note": "P95 latency is 28-32s under current load (DB contention)",
                                    "db_query_ms": 28940,
                                    "children": [],
                                },
                            ],
                        }
                    ],
                }
            ],
        },
        "latency_percentiles_inventory_service": {
            "p50_ms": 420,
            "p90_ms": 18200,
            "p95_ms": 28900,
            "p99_ms": 34500,
            "note": "inventory-service is under elevated load; P95 exceeds 30 000 ms timeout",
        },
    }


# ---------------------------------------------------------------------------
# search_runbooks
# ---------------------------------------------------------------------------

def get_mock_runbooks(keyword: str) -> dict:
    runbooks = [
        {
            "id": "RB-1042",
            "title": "PaymentService HTTP Timeout Troubleshooting",
            "url": "https://wiki.company.com/runbooks/payment-service/timeout",
            "last_updated": "2024-09-14",
            "relevance_score": 0.97,
            "summary": (
                "When PaymentService reports SocketTimeoutException against downstream services, "
                "first check recent deployments for timeout config changes. "
                "Timeout is configured in application.properties under `inventory.http.timeout.ms`. "
                "Recommended production value: ≥60 000 ms. "
                "To roll back: update config and deploy hotfix, or use feature flag "
                "`ff.payment.inventory_timeout_override=60000`."
            ),
            "steps": [
                "1. Check error rate in Datadog: payment-service/SocketTimeoutException",
                "2. Review last 2 hours of deployments in Spinnaker",
                "3. Inspect application.properties diff for timeout settings",
                "4. If timeout was reduced, revert to previous value (min 60 000 ms)",
                "5. Re-deploy or use feature flag to apply immediately",
                "6. Monitor error rate — should drop to 0 within 2 minutes of rollout",
            ],
        },
        {
            "id": "RB-0887",
            "title": "Inventory Service High Latency Response Plan",
            "url": "https://wiki.company.com/runbooks/inventory-service/high-latency",
            "last_updated": "2024-11-01",
            "relevance_score": 0.82,
            "summary": (
                "inventory-service P95 latency can spike to 25-35 s under high DB load. "
                "Upstream callers MUST set timeouts ≥60 s or implement circuit-breaker patterns. "
                "For immediate relief, scale inventory-service pods (min 6 replicas under load)."
            ),
            "steps": [
                "1. Check inventory-service pod count: kubectl get pods -n inventory",
                "2. Scale up if <6 replicas: kubectl scale deployment inventory-service --replicas=8",
                "3. Check DB connection pool utilization in Grafana",
                "4. If DB is bottleneck, enable read replica routing via config flag",
            ],
        },
        {
            "id": "RB-1198",
            "title": "General Microservice Circuit Breaker Configuration",
            "url": "https://wiki.company.com/runbooks/general/circuit-breakers",
            "last_updated": "2025-01-20",
            "relevance_score": 0.61,
            "summary": (
                "Circuit breaker best practices for inter-service calls. "
                "Use Resilience4j with: failureRateThreshold=50, slowCallDurationThreshold=10000ms, "
                "waitDurationInOpenState=30s."
            ),
            "steps": [],
        },
    ]

    kw = keyword.lower()
    scored = [
        r for r in runbooks
        if kw in r["title"].lower() or kw in r["summary"].lower()
    ]
    if not scored:
        scored = runbooks  # return all if no match

    return {
        "keyword": keyword,
        "total_found": len(scored),
        "runbooks": scored,
    }


# ---------------------------------------------------------------------------
# search_past_incidents
# ---------------------------------------------------------------------------

def get_mock_incidents(error_pattern: str) -> dict:
    incidents = [
        {
            "id": "INC-4821",
            "title": "PaymentService SocketTimeoutException cascade — inventory-service timeout config",
            "date": "2024-09-12",
            "duration_minutes": 18,
            "severity": "P1",
            "affected_services": ["payment-service", "inventory-service"],
            "error_pattern": "SocketTimeoutException: Read timed out after 30000ms",
            "root_cause": (
                "Deploy v2.11.4 reduced inventory.http.timeout.ms from 60000 to 30000 ms. "
                "inventory-service P95 latency is 28-32 s under peak load, so the new timeout "
                "caused widespread failures."
            ),
            "resolution": (
                "Reverted timeout to 60000 ms via hotfix deploy v2.11.4-hotfix. "
                "Error rate returned to 0% within 90 seconds of hotfix deployment."
            ),
            "commit_reverted": "d1f3a9c",
            "follow_up_actions": [
                "Add lint rule to flag timeout values <60 000 ms for inventory-service client",
                "Implement circuit-breaker for InventoryClient (Resilience4j)",
                "Add canary analysis step to deployment pipeline for timeout config changes",
            ],
            "similarity_score": 0.99,
        },
        {
            "id": "INC-3307",
            "title": "Payment processing degraded — database connection pool exhaustion",
            "date": "2024-06-03",
            "duration_minutes": 42,
            "severity": "P2",
            "affected_services": ["payment-service"],
            "error_pattern": "Connection pool timeout",
            "root_cause": "DB connection pool size was reduced from 50 to 20 in a config change.",
            "resolution": "Reverted pool size to 50 in application.properties.",
            "similarity_score": 0.41,
        },
        {
            "id": "INC-2198",
            "title": "Inventory service latency spike under Black Friday load",
            "date": "2023-11-24",
            "duration_minutes": 95,
            "severity": "P1",
            "affected_services": ["inventory-service", "order-service", "payment-service"],
            "error_pattern": "slow response from inventory-service",
            "root_cause": "DB read replicas not scaled for load; all queries hitting primary.",
            "resolution": "Scaled read replicas to 4 and enabled read replica routing.",
            "similarity_score": 0.68,
        },
    ]

    pat = error_pattern.lower()
    results = sorted(
        incidents,
        key=lambda i: (
            0.6 * i["similarity_score"]
            + 0.4 * (1 if pat in i["error_pattern"].lower() else 0)
        ),
        reverse=True,
    )

    return {
        "pattern_searched": error_pattern,
        "total_found": len(results),
        "incidents": results,
    }


# ---------------------------------------------------------------------------
# get_service_dependencies
# ---------------------------------------------------------------------------

def get_mock_dependencies(service_name: str) -> dict:
    graph = {
        "payment-service": {
            "upstream": ["api-gateway", "checkout-service", "mobile-api"],
            "downstream": [
                {
                    "service": "inventory-service",
                    "protocol": "HTTP/REST",
                    "criticality": "blocking",
                    "timeout_config_ms": 30000,
                    "circuit_breaker": False,
                },
                {
                    "service": "fraud-detection-service",
                    "protocol": "HTTP/REST",
                    "criticality": "non-blocking",
                    "timeout_config_ms": 5000,
                    "circuit_breaker": True,
                },
                {
                    "service": "payment-db",
                    "protocol": "JDBC",
                    "criticality": "blocking",
                    "timeout_config_ms": 3000,
                    "circuit_breaker": False,
                },
                {
                    "service": "notification-service",
                    "protocol": "Kafka",
                    "criticality": "async-non-blocking",
                    "timeout_config_ms": None,
                    "circuit_breaker": False,
                },
            ],
        },
        "inventory-service": {
            "upstream": ["payment-service", "order-service", "warehouse-service"],
            "downstream": [
                {
                    "service": "inventory-db",
                    "protocol": "JDBC",
                    "criticality": "blocking",
                    "timeout_config_ms": 5000,
                    "circuit_breaker": False,
                },
                {
                    "service": "warehouse-api",
                    "protocol": "HTTP/REST",
                    "criticality": "non-blocking",
                    "timeout_config_ms": 10000,
                    "circuit_breaker": True,
                },
            ],
        },
    }

    if service_name not in graph:
        return {"service": service_name, "upstream": [], "downstream": [], "note": "Unknown service"}

    return {"service": service_name, **graph[service_name]}
