# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test

```bash
# Install (editable mode, single dependency: mcp>=1.0.0)
pip install -e .

# Run all tests (single async script, no per-test runner)
python test_server.py

# Run server — stdio mode (default)
aact-mcp-server

# Run server — HTTP mode (for CT.Sight/Docker integration)
AACT_MCP_TRANSPORT=streamable-http AACT_MCP_HOST=0.0.0.0 AACT_MCP_PORT=8001 aact-mcp-server

# Alternative: run as module
python -m src
```

No linter or formatter is configured.

## Architecture

Read-only MCP server that serves static AACT (ClinicalTrials.gov) database schema as MCP Resources. Part of the **CT.Sight** pipeline:

```
CT.Sight frontend → CT.Sight backend (Docker) → THIS MCP SERVER (host, HTTP) → schema context
                         ↓
                    LLM generates SQL using schema
                         ↓
                    Backend executes SQL against live AACT PostgreSQL DB
```

**This server does NOT** execute SQL, call any LLM, or connect to any database.

### Single-file server

All logic is in `src/server.py` (~800 lines) using `FastMCP` from the `mcp` SDK. Structure:

1. **Module-level loading** — JSON files from `data/` loaded into globals (`_TABLES`, `_FOREIGN_KEYS`, `_GLOSSARY`, `_COLUMN_PROFILES`, `_QUERY_PATTERNS`) at import time.
2. **`_format_*` helpers** — Convert JSON data to pseudo-DDL text (DDL chosen because LLMs parse it reliably).
3. **`@mcp.resource()` handlers** — Async functions returning plain text. Resources use `aact://` URI scheme.
4. **`/health` custom route** — Starlette endpoint for liveness probes (HTTP mode only).
5. **`main()`** — Selects transport from `AACT_MCP_TRANSPORT` env var and starts server.

### Data files (`data/`)

| File | Module globals | Purpose |
|:---|:---|:---|
| `aact_schema_static.json` | `_TABLES`, `_FOREIGN_KEYS`, `_TABLE_INDEX` | 48-table schema with rich descriptions |
| `glossary.json` | `_GLOSSARY` | Clinical trial terminology → tables/columns |
| `column_profiles.json` | `_COLUMN_PROFILES`, `_COLUMN_PROFILES_BY_TABLE` | Column value enums, ranges, samples |
| `query_patterns.json` | `_QUERY_PATTERNS` | Tested SQL templates |

`column_profiles_big.json` is an alternative profiles file (not loaded by default). `generate_column_profiles.py` regenerates profiles from a live AACT database (requires `AACT_DATABASE_URL` env var).

## Key Conventions

- **Resource-only design** — No MCP Tools are implemented. All data served via `@mcp.resource()`. This eliminates SQL injection risk by design.
- **DDL output format** — Schema formatted as `CREATE TABLE` pseudo-DDL with SQL comments for descriptions.
- **Underscore-prefixed module globals** — All data stores (`_TABLES`, `_SCHEMA`, etc.) loaded once at import time.
- **Tests import server internals directly** — `test_server.py` imports private globals and async resource handlers from `src.server`, calls them with `await`. No server spin-up or HTTP involved.
- **Hardcoded schema counts in tests** — Tests assert exactly 48 tables and 63 foreign keys. If `aact_schema_static.json` changes, update these assertions in `test_server.py`.
- **Schema regeneration is LLM-assisted** — See `UPDATING_SCHEMA.md`. Requires merging `schema.rb` from the AACT repo with the HTML data dictionary.

## Transport Modes

Controlled by `AACT_MCP_TRANSPORT` env var:
- `stdio` (default) — subprocess/local use, Claude Desktop
- `streamable-http` — network use; CT.Sight backend (Docker) connects via `host.docker.internal:8001`

HTTP mode also reads `AACT_MCP_HOST` (default `127.0.0.1`) and `AACT_MCP_PORT` (default `8001`). Endpoints: `/mcp` (MCP), `/health` (liveness probe).
