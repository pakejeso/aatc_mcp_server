# Copilot Instructions — AACT Schema MCP Server

## Build & Test

```bash
# Install (editable mode, single dependency: mcp>=1.0.0)
pip install -e .

# Run tests (all 10 resource tests)
python test_server.py

# Run the server in stdio mode
aact-mcp-server

# Run the server in HTTP mode (for CT.Sight integration)
# Set env vars first: AACT_MCP_TRANSPORT=streamable-http, AACT_MCP_HOST=0.0.0.0, AACT_MCP_PORT=8001
aact-mcp-server

# Alternative: run as module
python -m src
```

There is no linter or formatter configured. There is no per-test runner — `test_server.py` runs all tests as a single async script.

## Architecture

This is a **read-only MCP server** that serves static AACT clinical trials database schema as MCP Resources. It is part of the **CT.Sight** architecture:

```
CT.Sight frontend → CT.Sight backend (Docker) → THIS MCP SERVER (host machine, HTTP) → returns schema JSON
                         ↓
                    LLM generates SQL using schema context
                         ↓
                    Backend executes SQL against live AACT PostgreSQL DB
```

**This server does NOT**: execute SQL, call any LLM, or connect to any database. It loads bundled JSON files from `data/` at startup and serves them as formatted text over MCP.

### Single-file server

All server logic lives in `src/server.py` (~800 lines). It uses the `FastMCP` class from the `mcp` SDK. There are no routers, no middleware layers, no sub-modules — just one file with:

1. **Module-level loading** — JSON files are loaded into module globals (`_TABLES`, `_FOREIGN_KEYS`, `_GLOSSARY`, etc.) at import time.
2. **Formatting helpers** — `_format_*` functions convert the JSON data into pseudo-DDL text (chosen because LLMs parse DDL reliably).
3. **`@mcp.resource()` handlers** — Async functions decorated as MCP Resources. Each returns a plain text string.
4. **`/health` endpoint** — A Starlette custom route for liveness probes (HTTP mode only).
5. **`main()`** — Selects transport (`stdio` or `streamable-http`) from env vars and starts the server.

### Data files in `data/`

| File | Loaded into | Purpose |
|:---|:---|:---|
| `aact_schema_static.json` | `_TABLES`, `_FOREIGN_KEYS`, `_TABLE_INDEX` | 48-table schema with rich descriptions from the AACT data dictionary |
| `glossary.json` | `_GLOSSARY` | Clinical trial terminology → AACT tables/columns mapping |
| `column_profiles.json` | `_COLUMN_PROFILES`, `_COLUMN_PROFILES_BY_TABLE` | Statistical profiles of key columns (enums, ranges, samples) |
| `query_patterns.json` | `_QUERY_PATTERNS` | Tested SQL templates for common queries |

These JSON files are the source of truth. Regenerating them is a semi-automated LLM-assisted process documented in `UPDATING_SCHEMA.md`.

### Transport modes

Controlled by `AACT_MCP_TRANSPORT` env var:
- `stdio` (default) — subprocess/local use
- `streamable-http` — network use; CT.Sight backend (Docker) connects via `host.docker.internal:8001`

## Key Conventions

- **Resource-only design**: No MCP Tools are implemented. All data is served via `@mcp.resource()` decorators. This eliminates SQL injection risk by design.
- **DDL output format**: Schema is formatted as `CREATE TABLE` pseudo-DDL with SQL comments for descriptions, because LLMs are heavily trained on DDL.
- **Module-level globals with underscore prefix**: All data stores (`_TABLES`, `_SCHEMA`, `_GLOSSARY`, etc.) are loaded once at import time as module globals.
- **Test imports server internals directly**: `test_server.py` imports private globals and resource handler functions from `src.server` and calls them with `await` — it does not spin up a server or use HTTP.
- **Schema counts are hardcoded assertions**: Tests assert exactly 48 tables and 63 foreign keys. If the schema JSON is updated, these assertions in `test_server.py` must be updated too.
