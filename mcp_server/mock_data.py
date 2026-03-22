"""
Mock data for CodeAutopsy — 4 demo scenarios:
  1. payment-service     — SocketTimeoutException (timeout config reduced in deploy)
  2. auth-service        — NullPointerException   (null check removed in deploy)
  3. order-service       — DB connection pool exhaustion (pool size not increased with traffic)
  4. notification-service — Kafka consumer lag    (blocking call introduced in consumer)
"""

import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

_NOW = datetime.now(timezone.utc)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Per-service GitHub repos
_GITHUB_REPOS = {
    "payment-service":      os.environ.get("GITHUB_REPO", ""),
    "auth-service":         os.environ.get("GITHUB_REPO_AUTH", ""),
    "order-service":        os.environ.get("GITHUB_REPO_ORDER", ""),
    "notification-service": os.environ.get("GITHUB_REPO_NOTIF", ""),
}


def _ts(minutes_ago: float, seconds_ago: float = 0) -> str:
    dt = _NOW - timedelta(minutes=minutes_ago, seconds=seconds_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ─────────────────────────────────────────────
# fetch_logs
# ─────────────────────────────────────────────

def _normalise(name: str) -> str:
    """Normalise service name: lowercase, spaces/underscores → hyphens."""
    return name.lower().replace("_", "-").replace(" ", "-").strip()


def get_mock_logs(service_name: str, time_range_minutes: int, error_keyword: str = "") -> dict:

    scenarios = {
        "payment-service": _logs_payment,
        "auth-service": _logs_auth,
        "order-service": _logs_order,
        "notification-service": _logs_notification,
    }

    builder = scenarios.get(_normalise(service_name))
    if not builder:
        return {"service": service_name, "time_range_minutes": time_range_minutes,
                "total_logs": 0, "error_count": 0, "logs": [], "summary": {}}

    all_logs = builder()
    if error_keyword:
        all_logs = [l for l in all_logs if error_keyword.lower() in l["message"].lower()]
    all_logs.sort(key=lambda x: x["timestamp"], reverse=True)

    return {
        "service": service_name,
        "time_range_minutes": time_range_minutes,
        "total_logs": len(all_logs),
        "error_count": sum(1 for l in all_logs if l["level"] == "ERROR"),
        "logs": all_logs[:50],
        "summary": _log_summary(service_name, all_logs),
    }


def _logs_payment():
    normal = [{"timestamp": _ts(m), "level": "INFO", "service": "payment-service",
               "message": f"Payment processed orderId=TXN-{8000+m} duration=412ms",
               "traceId": f"trace-pre-{m:04d}"} for m in range(25, 36)]
    errors = []
    for i in range(1, 25):
        level = "ERROR" if i % 3 != 0 else "WARN"
        e = {"timestamp": _ts(i, i*7%60), "level": level, "service": "payment-service",
             "traceId": f"trace-err-{9000+i:04d}",
             "message": "java.net.SocketTimeoutException: Read timed out after 30000ms calling inventory-service [GET /api/v1/stock/check]" if level == "ERROR"
                        else f"Slow response from inventory-service: {27000+i*200}ms"}
        if level == "ERROR":
            e["stackTrace"] = ("java.net.SocketTimeoutException: Read timed out after 30000ms\n"
                               "\tat com.example.payment.client.InventoryClient.checkStock(InventoryClient.java:84)\n"
                               "\tat com.example.payment.service.PaymentService.processPayment(PaymentService.java:156)")
        errors.append(e)
    return errors + normal


def _logs_auth():
    normal = [{"timestamp": _ts(m), "level": "INFO", "service": "auth-service",
               "message": f"Login successful userId=USR-{1000+m} duration=38ms",
               "traceId": f"trace-pre-{m:04d}"} for m in range(40, 50)]
    errors = []
    for i in range(1, 35):
        level = "ERROR" if i % 2 == 0 else "WARN"
        e = {"timestamp": _ts(i, i*11%60), "level": level, "service": "auth-service",
             "traceId": f"trace-auth-{5000+i:04d}",
             "message": "java.lang.NullPointerException: Cannot invoke String.isEmpty() on null token at UserService.java:88" if level == "ERROR"
                        else "JWT validation skipped — token field is null, returning 401"}
        if level == "ERROR":
            e["stackTrace"] = ("java.lang.NullPointerException\n"
                               "\tat com.example.auth.service.UserService.validateToken(UserService.java:88)\n"
                               "\tat com.example.auth.filter.JwtAuthFilter.doFilterInternal(JwtAuthFilter.java:54)\n"
                               "\tat org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:117)")
        errors.append(e)
    return errors + normal


def _logs_order():
    normal = [{"timestamp": _ts(m), "level": "INFO", "service": "order-service",
               "message": f"Order created orderId=ORD-{3000+m} duration=95ms",
               "traceId": f"trace-pre-{m:04d}"} for m in range(65, 75)]
    errors = []
    for i in range(1, 60):
        level = "ERROR" if i % 3 != 0 else "WARN"
        e = {"timestamp": _ts(i * 0.5, i*5%60), "level": level, "service": "order-service",
             "traceId": f"trace-ord-{7000+i:04d}",
             "message": "com.zaxxer.hikari.pool.HikariPool$PoolInitializationException: HikariPool-1 - Connection is not available, request timed out after 30000ms" if level == "ERROR"
                        else f"DB connection pool utilization: {85+i%10}% — approaching limit"}
        if level == "ERROR":
            e["stackTrace"] = ("com.zaxxer.hikari.pool.HikariPool: HikariPool-1 - Connection is not available\n"
                               "\tat com.example.order.repository.OrderRepository.save(OrderRepository.java:43)\n"
                               "\tat com.example.order.service.OrderService.createOrder(OrderService.java:112)")
        errors.append(e)
    return errors + normal


def _logs_notification():
    normal = [{"timestamp": _ts(m), "level": "INFO", "service": "notification-service",
               "message": f"Email sent to userId=USR-{2000+m} topic=order.confirmed lag=120ms",
               "traceId": f"trace-pre-{m:04d}"} for m in range(55, 65)]
    errors = []
    for i in range(1, 50):
        level = "WARN" if i % 3 == 0 else "ERROR"
        e = {"timestamp": _ts(i * 0.6, i*9%60), "level": level, "service": "notification-service",
             "traceId": f"trace-notif-{6000+i:04d}",
             "message": f"Kafka consumer lag on topic order.confirmed partition=0: {50000+i*1200} messages behind" if level == "WARN"
                        else "Consumer thread blocked for 45000ms in EmailSenderService.sendWithRetry() — possible blocking I/O on HTTP call"}
        errors.append(e)
    return errors + normal


def _log_summary(service: str, logs: list) -> dict:
    summaries = {
        "payment-service":      {"error_rate_last_5_min": "71%", "error_rate_prev_30_min": "0%",
                                 "most_common_error": "SocketTimeoutException", "first_error_timestamp": _ts(24, 37)},
        "auth-service":         {"error_rate_last_5_min": "52%", "error_rate_prev_30_min": "0%",
                                 "most_common_error": "NullPointerException", "first_error_timestamp": _ts(38, 12)},
        "order-service":        {"error_rate_last_5_min": "89%", "error_rate_prev_30_min": "0%",
                                 "most_common_error": "HikariPool connection timeout", "first_error_timestamp": _ts(61, 5)},
        "notification-service": {"consumer_lag_current": 187400, "consumer_lag_1h_ago": 320,
                                 "most_common_error": "Kafka consumer thread blocked", "first_lag_spike_timestamp": _ts(53, 44)},
    }
    return summaries.get(service, {})


# ─────────────────────────────────────────────
# get_recent_deployments
# ─────────────────────────────────────────────

def _fetch_github_commits(repo: str, token: str, hours: int = 0) -> list | None:
    """Fetch the latest commits from GitHub API (no time filter). Returns None on failure."""
    try:
        print(f"[GitHub] Fetching latest commits from {repo}", flush=True)
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://api.github.com/repos/{repo}/commits?per_page=10"
        r = httpx.get(url, headers=headers, timeout=5)
        print(f"[GitHub] Response: {r.status_code}", flush=True)
        if r.status_code != 200:
            return None
        commits = r.json()
        result = []
        for c in commits:
            sha = c["sha"][:9]
            # Fetch diff for the first commit only (to avoid rate limits)
            diff_summary = ""
            if not result:
                detail = httpx.get(
                    f"https://api.github.com/repos/{repo}/commits/{c['sha']}",
                    headers=headers, timeout=5
                ).json()
                files = detail.get("files", [])
                diff_summary = "\n".join(
                    f"{f['filename']}: +{f['additions']} -{f['deletions']}\n{f.get('patch','')[:300]}"
                    for f in files[:3]
                )
            commit_time = c["commit"]["author"]["date"]
            result.append({
                "id": f"deploy-{sha}",
                "version": f"commit-{sha}",
                "commit_sha": sha,
                "author": c["commit"]["author"]["email"],
                "timestamp": commit_time,
                "status": "success",
                "environment": "production",
                "changed_files": [f["filename"] for f in
                                  httpx.get(f"https://api.github.com/repos/{repo}/commits/{c['sha']}",
                                            headers=headers, timeout=5).json().get("files", [])[:5]]
                                 if not result else [],
                "commit_message": c["commit"]["message"],
                "diff_summary": diff_summary,
                "github_url": c["html_url"],
                "minutes_before_incident": round(
                    (_NOW - datetime.fromisoformat(commit_time.replace("Z", "+00:00"))).total_seconds() / 60
                ),
            })
        return result
    except Exception:
        return None


def get_mock_deployments(service_name: str, hours: int) -> dict:
    service_name = _normalise(service_name)
    # Try real GitHub API first for any service that has a configured repo
    repo = _GITHUB_REPOS.get(service_name, "")
    if repo:
        commits = _fetch_github_commits(repo, GITHUB_TOKEN, hours)
        if commits:
            return {"service": service_name, "hours_searched": hours,
                    "source": "github", "repo": repo, "deployments": commits}

    deploys = {
        "payment-service": [
            {"id": "deploy-7f3a9c", "version": "v2.14.3", "commit_sha": "a3f9c217b",
             "author": "sarah.chen@company.com", "timestamp": _ts(25), "status": "success",
             "changed_files": ["src/main/resources/application.properties",
                               "src/main/java/com/example/payment/config/HttpClientConfig.java"],
             "commit_message": "perf: reduce HTTP client timeouts to improve p99 latency",
             "diff_summary": "application.properties: inventory.http.timeout.ms 60000 → 30000\nHttpClientConfig.java: connect timeout 10000 → 5000",
             "minutes_before_incident": 25},
            {"id": "deploy-3b12ef", "version": "v2.14.2", "commit_sha": "e8b214cc1",
             "author": "james.park@company.com", "timestamp": _ts(72*60), "status": "success",
             "changed_files": ["src/main/java/com/example/payment/service/PaymentService.java"],
             "commit_message": "feat: add idempotency key support",
             "diff_summary": "PaymentService.java: added idempotency key dedup logic",
             "minutes_before_incident": None},
        ],
        "auth-service": [
            {"id": "deploy-c8a211", "version": "v1.9.1", "commit_sha": "f4c8a211d",
             "author": "alex.morgan@company.com", "timestamp": _ts(40), "status": "success",
             "changed_files": ["src/main/java/com/example/auth/service/UserService.java",
                               "src/main/java/com/example/auth/filter/JwtAuthFilter.java"],
             "commit_message": "refactor: simplify JWT token validation logic",
             "diff_summary": ("UserService.java line 85-90: removed null-check guard on token field\n"
                              "Before: if (token == null || token.isEmpty()) return false;\n"
                              "After:  if (token.isEmpty()) return false;  // NPE if token is null"),
             "minutes_before_incident": 40},
            {"id": "deploy-9e3c44", "version": "v1.9.0", "commit_sha": "b3e9c441a",
             "author": "priya.sharma@company.com", "timestamp": _ts(96*60), "status": "success",
             "changed_files": ["src/main/java/com/example/auth/service/TokenRefreshService.java"],
             "commit_message": "feat: add refresh token rotation",
             "diff_summary": "TokenRefreshService.java: added refresh token rotation on every login",
             "minutes_before_incident": None},
        ],
        "order-service": [
            {"id": "deploy-4d9f1b", "version": "v3.2.0", "commit_sha": "7d4f1b88c",
             "author": "tim.xu@company.com", "timestamp": _ts(62), "status": "success",
             "changed_files": ["src/main/resources/application.properties",
                               "src/main/java/com/example/order/service/OrderService.java"],
             "commit_message": "feat: increase max concurrent order processing threads",
             "diff_summary": ("application.properties: server.tomcat.threads.max 50 → 200\n"
                              "application.properties: spring.datasource.hikari.maximum-pool-size NOT changed (still 10)\n"
                              "OrderService.java: removed request queuing, now processes all requests concurrently"),
             "minutes_before_incident": 62},
        ],
        "notification-service": [
            {"id": "deploy-2b7e9a", "version": "v2.5.3", "commit_sha": "9b2e7a33f",
             "author": "dana.lee@company.com", "timestamp": _ts(55), "status": "success",
             "changed_files": ["src/main/java/com/example/notification/service/EmailSenderService.java",
                               "src/main/java/com/example/notification/consumer/OrderEventConsumer.java"],
             "commit_message": "feat: add email delivery confirmation via HTTP callback",
             "diff_summary": ("EmailSenderService.java: added synchronous HTTP call to delivery-tracker-api\n"
                              "  sendWithRetry() now blocks on HTTP response (up to 45s) before acking Kafka message\n"
                              "OrderEventConsumer.java: consumer thread now waits for sendWithRetry() to complete"),
             "minutes_before_incident": 55},
        ],
    }

    return {
        "service": service_name,
        "hours_searched": hours,
        "deployments": deploys.get(service_name, []),
    }


# ─────────────────────────────────────────────
# fetch_distributed_trace
# ─────────────────────────────────────────────

def get_mock_trace(trace_id: str) -> dict:
    # Infer scenario from trace_id prefix
    if "auth" in trace_id:
        return {
            "trace_id": trace_id, "total_duration_ms": 12, "status": "ERROR",
            "root_span": {
                "span_id": "span-001", "service": "api-gateway",
                "operation": "POST /v1/auth/login", "duration_ms": 12, "status": "ERROR",
                "children": [{
                    "span_id": "span-002", "service": "auth-service",
                    "operation": "JwtAuthFilter.doFilterInternal", "duration_ms": 8, "status": "ERROR",
                    "error": "NullPointerException at UserService.java:88",
                    "children": [{
                        "span_id": "span-003", "service": "auth-service",
                        "operation": "UserService.validateToken", "duration_ms": 1,
                        "status": "ERROR", "error": "token field is null — null check removed in v1.9.1",
                        "children": []
                    }]
                }]
            }
        }
    if "ord" in trace_id:
        return {
            "trace_id": trace_id, "total_duration_ms": 30215, "status": "ERROR",
            "root_span": {
                "span_id": "span-001", "service": "api-gateway",
                "operation": "POST /v1/orders", "duration_ms": 30215, "status": "ERROR",
                "children": [{
                    "span_id": "span-002", "service": "order-service",
                    "operation": "OrderService.createOrder", "duration_ms": 30190, "status": "ERROR",
                    "error": "HikariPool connection timeout",
                    "children": [{
                        "span_id": "span-003", "service": "order-service",
                        "operation": "OrderRepository.save [JDBC]", "duration_ms": 30001,
                        "status": "TIMEOUT", "error": "HikariPool-1 - Connection is not available, timed out after 30000ms",
                        "db": {"pool_size": 10, "active_connections": 10, "pending_threads": 142},
                        "children": []
                    }]
                }]
            },
            "hikari_pool_stats": {"maximum_pool_size": 10, "active": 10, "idle": 0,
                                   "pending": 142, "note": "Pool fully saturated — 200 threads competing for 10 connections"}
        }
    if "notif" in trace_id:
        return {
            "trace_id": trace_id, "total_duration_ms": 45200, "status": "SLOW",
            "root_span": {
                "span_id": "span-001", "service": "kafka-broker",
                "operation": "order.confirmed partition=0", "duration_ms": 45200, "status": "SLOW",
                "consumer_lag": 187400,
                "children": [{
                    "span_id": "span-002", "service": "notification-service",
                    "operation": "OrderEventConsumer.onMessage", "duration_ms": 45180, "status": "SLOW",
                    "children": [{
                        "span_id": "span-003", "service": "notification-service",
                        "operation": "EmailSenderService.sendWithRetry", "duration_ms": 44900,
                        "status": "SLOW", "note": "Blocking HTTP call to delivery-tracker-api (synchronous, added in v2.5.3)",
                        "children": [{
                            "span_id": "span-004", "service": "delivery-tracker-api",
                            "operation": "POST /v1/track", "duration_ms": 44870,
                            "status": "SLOW", "note": "delivery-tracker-api P95 is 40-50s under load",
                            "children": []
                        }]
                    }]
                }]
            }
        }
    # default: payment-service
    return {
        "trace_id": trace_id, "total_duration_ms": 30482, "status": "ERROR",
        "root_span": {
            "span_id": "span-001", "service": "api-gateway",
            "operation": "POST /v1/payments", "duration_ms": 30482, "status": "ERROR",
            "children": [{
                "span_id": "span-002", "service": "payment-service",
                "operation": "PaymentController.pay", "duration_ms": 30418, "status": "ERROR",
                "error": "SocketTimeoutException",
                "children": [{
                    "span_id": "span-003", "service": "payment-service",
                    "operation": "InventoryClient.checkStock [HTTP GET]", "duration_ms": 30001,
                    "status": "TIMEOUT", "error": "Read timed out after 30000ms",
                    "http": {"url": "http://inventory-service:8080/api/v1/stock/check",
                             "timeout_config_ms": 30000, "actual_wait_ms": 30001},
                    "children": []
                }]
            }]
        },
        "latency_percentiles_inventory_service": {
            "p50_ms": 420, "p90_ms": 18200, "p95_ms": 28900, "p99_ms": 34500,
            "note": "inventory-service P95 exceeds the 30 000 ms timeout"
        }
    }


# ─────────────────────────────────────────────
# search_runbooks
# ─────────────────────────────────────────────

def get_mock_runbooks(keyword: str) -> dict:
    all_runbooks = [
        # payment-service
        {"id": "RB-1042", "title": "PaymentService HTTP Timeout Troubleshooting",
         "url": "https://wiki.company.com/runbooks/payment-service/timeout",
         "tags": ["payment-service", "timeout", "sockettimeoutexception", "inventory"],
         "summary": ("Check recent deploys for timeout config changes. "
                     "inventory.http.timeout.ms must be ≥60 000 ms. "
                     "Fix: revert config or use feature flag ff.payment.inventory_timeout_override=60000."),
         "steps": ["1. Check Datadog error rate", "2. Review Spinnaker deploys",
                   "3. Inspect application.properties diff", "4. Revert timeout if reduced",
                   "5. Monitor — error rate drops within 2 min"]},
        # auth-service
        {"id": "RB-0743", "title": "AuthService NullPointerException — JWT Validation",
         "url": "https://wiki.company.com/runbooks/auth-service/npe-jwt",
         "tags": ["auth-service", "nullpointerexception", "jwt", "token", "userservice"],
         "summary": ("NullPointerException in UserService.validateToken() usually means a null-check "
                     "guard was removed in a recent refactor. Check the diff for UserService.java lines 80-95. "
                     "Fix: restore null check — if (token == null || token.isEmpty()) return false;"),
         "steps": ["1. Check git diff for UserService.java",
                   "2. Restore null guard on token field",
                   "3. Deploy hotfix — NPE will stop immediately"]},
        # order-service
        {"id": "RB-0915", "title": "OrderService DB Connection Pool Exhaustion",
         "url": "https://wiki.company.com/runbooks/order-service/db-pool",
         "tags": ["order-service", "hikari", "connection pool", "database", "jdbc"],
         "summary": ("HikariPool exhaustion happens when thread count is increased without increasing "
                     "spring.datasource.hikari.maximum-pool-size. Rule: pool-size ≥ max-threads / 4. "
                     "Fix: set maximum-pool-size=50 in application.properties and redeploy."),
         "steps": ["1. Check HikariPool metrics in Grafana",
                   "2. Compare server.tomcat.threads.max vs hikari.maximum-pool-size",
                   "3. Set hikari.maximum-pool-size ≥ threads/4",
                   "4. Redeploy — pool saturation clears within 30s"]},
        # notification-service
        {"id": "RB-1187", "title": "Notification Service Kafka Consumer Lag",
         "url": "https://wiki.company.com/runbooks/notification-service/kafka-lag",
         "tags": ["notification-service", "kafka", "consumer lag", "blocking", "email"],
         "summary": ("Kafka consumer lag spikes when consumer threads block on synchronous I/O. "
                     "Kafka consumers must never block — all downstream calls must be async. "
                     "Fix: make EmailSenderService calls non-blocking (CompletableFuture / async HTTP), "
                     "ack the Kafka message immediately and process delivery confirmation separately."),
         "steps": ["1. Check consumer lag in Kafka UI / Confluent Control Center",
                   "2. Find blocking calls in consumer thread stack traces",
                   "3. Move blocking I/O to async thread pool",
                   "4. Lag will drain once consumer is unblocked"]},
    ]

    kw = keyword.lower()
    matched = [r for r in all_runbooks
               if kw in r["title"].lower() or kw in r["summary"].lower()
               or any(kw in t for t in r["tags"])]
    if not matched:
        matched = all_runbooks[:2]

    return {"keyword": keyword, "total_found": len(matched), "runbooks": matched}


# ─────────────────────────────────────────────
# search_past_incidents
# ─────────────────────────────────────────────

def get_mock_incidents(error_pattern: str) -> dict:
    # Pull real resolved incidents from Supabase first
    db_incidents = []
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from orchestrator.db import query_incidents
        db_incidents = query_incidents(limit=20)
    except Exception:
        pass

    all_incidents = db_incidents + [
        {"id": "INC-4821", "title": "PaymentService SocketTimeoutException — inventory timeout config",
         "date": "2024-09-12", "duration_minutes": 18, "severity": "P1",
         "affected_services": ["payment-service"],
         "error_pattern": "sockettimeoutexception inventory timeout 30000",
         "root_cause": "Deploy v2.11.4 reduced inventory.http.timeout.ms 60000→30000. inventory-service P95 is 28-32s.",
         "resolution": "Hotfix reverted timeout to 60000 ms. Error rate 0% within 90s.",
         "similarity_score": 0.99},
        {"id": "INC-3841", "title": "AuthService NPE — null token after JWT refactor",
         "date": "2024-07-08", "duration_minutes": 12, "severity": "P1",
         "affected_services": ["auth-service"],
         "error_pattern": "nullpointerexception jwt token userservice",
         "root_cause": "Refactor removed null check on token in UserService.validateToken(). Tokens set to null by some mobile clients.",
         "resolution": "Hotfix restored: if (token == null || token.isEmpty()) return false;",
         "similarity_score": 0.97},
        {"id": "INC-5102", "title": "OrderService DB pool exhaustion after thread count increase",
         "date": "2025-01-15", "duration_minutes": 35, "severity": "P1",
         "affected_services": ["order-service"],
         "error_pattern": "hikaripool connection not available timed out",
         "root_cause": "Tomcat threads increased 50→200 but hikari pool size left at 10. 142 threads blocked on DB connections.",
         "resolution": "Set hikari.maximum-pool-size=50 and redeployed. Connections cleared in 30s.",
         "similarity_score": 0.98},
        {"id": "INC-4430", "title": "Notification service Kafka lag — blocking HTTP in consumer",
         "date": "2024-11-22", "duration_minutes": 68, "severity": "P2",
         "affected_services": ["notification-service"],
         "error_pattern": "kafka consumer lag blocking notification",
         "root_cause": "Synchronous HTTP call added to consumer thread. delivery-tracker-api P95 is 40-50s, blocking Kafka ack.",
         "resolution": "Moved HTTP call to async CompletableFuture. Lag drained within 10 minutes.",
         "similarity_score": 0.96},
    ]  # end mock incidents

    pat = error_pattern.lower()
    results = sorted(
        all_incidents,
        key=lambda i: (0.6 * i["similarity_score"] +
                       0.4 * sum(1 for w in pat.split() if w in i["error_pattern"])),
        reverse=True,
    )

    return {"pattern_searched": error_pattern, "total_found": len(results), "incidents": results}


# ─────────────────────────────────────────────
# get_service_dependencies
# ─────────────────────────────────────────────

def get_mock_dependencies(service_name: str) -> dict:
    service_name = _normalise(service_name)
    graph = {
        "payment-service": {
            "upstream": ["api-gateway", "checkout-service", "mobile-api"],
            "downstream": [
                {"service": "inventory-service", "protocol": "HTTP/REST", "criticality": "blocking",
                 "timeout_config_ms": 30000, "circuit_breaker": False},
                {"service": "fraud-detection-service", "protocol": "HTTP/REST", "criticality": "non-blocking",
                 "timeout_config_ms": 5000, "circuit_breaker": True},
                {"service": "payment-db", "protocol": "JDBC", "criticality": "blocking",
                 "timeout_config_ms": 3000, "circuit_breaker": False},
            ],
        },
        "auth-service": {
            "upstream": ["api-gateway", "mobile-api", "web-app"],
            "downstream": [
                {"service": "user-db", "protocol": "JDBC", "criticality": "blocking",
                 "timeout_config_ms": 3000, "circuit_breaker": False},
                {"service": "token-cache", "protocol": "Redis", "criticality": "blocking",
                 "timeout_config_ms": 500, "circuit_breaker": False},
            ],
        },
        "order-service": {
            "upstream": ["api-gateway", "checkout-service"],
            "downstream": [
                {"service": "order-db", "protocol": "JDBC", "criticality": "blocking",
                 "pool_size": 10, "max_threads": 200, "circuit_breaker": False},
                {"service": "payment-service", "protocol": "HTTP/REST", "criticality": "blocking",
                 "timeout_config_ms": 10000, "circuit_breaker": True},
                {"service": "notification-service", "protocol": "Kafka", "criticality": "async",
                 "timeout_config_ms": None, "circuit_breaker": False},
            ],
        },
        "notification-service": {
            "upstream": ["order-service", "payment-service", "auth-service"],
            "downstream": [
                {"service": "delivery-tracker-api", "protocol": "HTTP/REST", "criticality": "blocking-in-consumer",
                 "timeout_config_ms": 45000, "circuit_breaker": False,
                 "note": "Called synchronously inside Kafka consumer thread — dangerous"},
                {"service": "email-provider", "protocol": "SMTP", "criticality": "blocking",
                 "timeout_config_ms": 10000, "circuit_breaker": False},
            ],
        },
    }

    if service_name not in graph:
        return {"service": service_name, "upstream": [], "downstream": [], "note": "Unknown service"}

    return {"service": service_name, **graph[service_name]}
