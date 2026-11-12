# ADR 0009 — The MCP server speaks the protocol directly

Status: Accepted (2026-11-12). Supersedes ADR 0006.

## Context
ADR 0006 adopted the official `mcp` SDK for the agent-facing edge. The SDK brings 28 transitive
packages (pydantic, httpx, starlette, uvicorn, cryptography, ...) to serve four read-only tools
over stdio. Each client session spawns the server, and importing the SDK costs 0.64 s and 53 MB
on the serving host, against 0.14 s and 30 MB for numpy alone. The code touched the SDK in three
imports and about fifteen lines.

## Decision
`pipeline/alexandria_mcp.py` implements the stdio transport itself: newline-delimited JSON-RPC
with `initialize` (echoing a supported protocol version), `ping`, `tools/list` and `tools/call`.
Notifications get no reply. Tool results carry both `structuredContent` and the same JSON as text.
Tool failures are `isError` results; protocol failures are JSON-RPC errors (-32601 unknown method,
-32602 bad params, -32700 parse error). Arguments are checked against each tool's `inputSchema`
(required, type, enum, minimum, additionalProperties) with the SDK's message wording.
Stdout carries protocol only; anything else printed goes to stderr.

## Consequences
- Runtime dependency is numpy only.
- Measured on the serving host: start-up 0.5 s faster, 30 MB less memory, and identical
  top-8 results and scores for 60 searches against the SDK server.
- Resources, prompts, sampling and other optional MCP features are not offered. Adding one means
  extending `handle_request`.
- The protocol surface must be kept in step with the MCP specification by hand. The test suite
  covers handshake, version echo, ping, argument errors, unknown methods and notification silence.
