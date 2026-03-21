"""
CodeAutopsy MCP Server
======================
Exposes 6 tools for AI-powered incident root cause analysis.

Run standalone:
    python -m mcp_server.server

Connect to Claude Desktop by adding to ~/Library/Application Support/Claude/claude_desktop_config.json:
    {
      "mcpServers": {
        "codeautopsy": {
          "command": "python",
          "args": ["-m", "mcp_server.server"],
          "cwd": "/absolute/path/to/codeautopsy"
        }
      }
    }
"""

import asyncio
import json
import sys
import os

# Add parent directory to path so we can import mock_data
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
    ListToolsResult,
    ServerCapabilities,
    ToolsCapability,
)

from mcp_server.mock_data import (
    get_mock_logs,
    get_mock_deployments,
    get_mock_trace,
    get_mock_runbooks,
    get_mock_incidents,
    get_mock_dependencies,
)

server = Server("codeautopsy")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="fetch_logs",
            description=(
                "Fetches recent logs for a service from CloudWatch/Datadog. "
                "Returns structured log entries with timestamps, levels, and stack traces."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the microservice (e.g. 'payment-service')",
                    },
                    "time_range_minutes": {
                        "type": "integer",
                        "description": "How many minutes back to fetch logs",
                        "default": 30,
                    },
                    "error_keyword": {
                        "type": "string",
                        "description": "Optional keyword to filter logs (e.g. 'SocketTimeoutException')",
                    },
                },
                "required": ["service_name", "time_range_minutes"],
            },
        ),
        Tool(
            name="get_recent_deployments",
            description=(
                "Returns recent deployments for a service from GitHub Actions / Spinnaker. "
                "Includes commit SHA, author, timestamp, changed files, and diff summary."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the microservice",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "How many hours back to search for deployments",
                        "default": 24,
                    },
                },
                "required": ["service_name", "hours"],
            },
        ),
        Tool(
            name="fetch_distributed_trace",
            description=(
                "Returns a full distributed trace (span tree) from Jaeger/Zipkin. "
                "Shows which microservice failed, latency at each hop, and error details."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "trace_id": {
                        "type": "string",
                        "description": "The trace ID from the error log entry",
                    },
                },
                "required": ["trace_id"],
            },
        ),
        Tool(
            name="search_runbooks",
            description=(
                "Searches internal Confluence/Notion runbooks for relevant procedures. "
                "Returns matching runbooks with step-by-step resolution guides."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search keyword (e.g. 'timeout', 'payment-service', 'SocketTimeoutException')",
                    },
                },
                "required": ["keyword"],
            },
        ),
        Tool(
            name="search_past_incidents",
            description=(
                "Searches historical incidents (PagerDuty / incident.io) for similar errors. "
                "Returns past incidents with root causes and resolutions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "error_pattern": {
                        "type": "string",
                        "description": "Error pattern to search for (e.g. 'SocketTimeoutException inventory-service')",
                    },
                },
                "required": ["error_pattern"],
            },
        ),
        Tool(
            name="get_service_dependencies",
            description=(
                "Returns the upstream and downstream service dependency map for a given service. "
                "Includes protocol, criticality, timeout configs, and circuit-breaker status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the microservice",
                    },
                },
                "required": ["service_name"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "fetch_logs":
            result = get_mock_logs(
                service_name=arguments["service_name"],
                time_range_minutes=arguments.get("time_range_minutes", 30),
                error_keyword=arguments.get("error_keyword", ""),
            )
        elif name == "get_recent_deployments":
            result = get_mock_deployments(
                service_name=arguments["service_name"],
                hours=arguments.get("hours", 24),
            )
        elif name == "fetch_distributed_trace":
            result = get_mock_trace(trace_id=arguments["trace_id"])
        elif name == "search_runbooks":
            result = get_mock_runbooks(keyword=arguments["keyword"])
        elif name == "search_past_incidents":
            result = get_mock_incidents(error_pattern=arguments["error_pattern"])
        elif name == "get_service_dependencies":
            result = get_mock_dependencies(service_name=arguments["service_name"])
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def main():
    init_options = InitializationOptions(
        server_name="codeautopsy",
        server_version="1.0.0",
        capabilities=ServerCapabilities(tools=ToolsCapability(listChanged=False)),
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
