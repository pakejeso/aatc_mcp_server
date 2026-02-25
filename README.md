# AACT Schema MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A read-only, lightweight [Model Context Protocol (MCP)](https://modelcontext.dev/) server that exposes the AACT (Aggregate Analysis of ClinicalTrials.gov) database schema as a set of structured **Resources**. It is designed to provide an LLM with the necessary structural context to generate accurate SQL queries against the AACT database.

This server is a key component in a hybrid "Human-in-the-loop" architecture for SQL generation. It provides the schema context to the LLM but **does not execute any queries**, ensuring a safe and secure separation of concerns.

The schema is loaded from a bundled JSON snapshot (`data/aact_schema_static.json`) that includes rich, human-readable descriptions sourced from the official AACT data dictionary. See [UPDATING_SCHEMA.md](UPDATING_SCHEMA.md) for instructions on regenerating this file when the AACT schema changes.

The repository also includes an **interactive MCP Flow Visualizer** — a web app that demonstrates how the entire MCP architecture works, step by step, using real LLM calls.

## Architectural Context

This server is designed to fit into the following workflow:

1.  A **main backend** (e.g., the [CT.Sight](https://github.com/pakejeso/clinical-trials-search) FastAPI app) receives a free-text request from a user (e.g., "find phase 3 trials for diabetes").
2.  The backend invokes an **LLM**, which connects to this **AACT Schema MCP Server**.
3.  The LLM reads the database schema from the available MCP Resources to understand table structures, columns, and relationships.
4.  The LLM generates a SQL query and sends it back to the main backend.
5.  The main backend **validates, sanitizes, and executes** the SQL query against the actual database.

This server **only** performs step 3. It has no capabilities to execute arbitrary SQL, ensuring it cannot be exploited to modify or exfiltrate data.

## Features

-   **Read-Only by Design**: Exposes schema information via MCP Resources only. No MCP Tools are implemented.
-   **Static Schema with Rich Descriptions**: The bundled JSON snapshot includes table and column descriptions from the official AACT data dictionary, domain classification, and cardinality metadata — not just raw column names and types.
-   **Comprehensive Schema Output**: The schema is formatted as pseudo-DDL (`CREATE TABLE ...`) text, which is highly effective for LLM comprehension.
-   **Multiple Resource Granularities**: Provides both the full schema and per-table resources for efficient context loading.
-   **Dual Transport Modes**: Supports both `stdio` for local subprocess use and `streamable-http` for network-based deployment.
-   **Zero Configuration**: No database connection required. Install, run, done.

## MCP Resources

The server exposes the following resources:

| URI Template | Name | Description |
| :--- | :--- | :--- |
| `aact://schema` | AACT Full Schema | The complete schema of all 48 tables, including columns, types, keys, and all 63 foreign key relationships. The primary resource for initial context. |
| `aact://schema/{table_name}` | AACT Table Schema | The schema for a single table. Use this for surgical, on-demand context loading. |
| `aact://tables` | AACT Table List | All tables with their descriptions, column counts, and domain classification. Read this first to identify relevant tables. |
| `aact://relationships` | AACT Relationships | A summary of all foreign key relationships, separated into `nct_id` joins and hierarchical FKs. |

## Getting Started

### Prerequisites

-   Python 3.10+

### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/pakejeso/aatc_mcp_server.git
    cd aatc_mcp_server
    ```

2.  Install dependencies:
    ```bash
    pip install -e .
    ```

### Running the MCP Server

The server supports two transport modes, controlled by the `AACT_MCP_TRANSPORT` environment variable.

#### 1. Stdio Mode (default)

This is the classic MCP transport for local use, where the server communicates with a parent process over `stdin` and `stdout`.

```bash
# Run using the installed script (default is stdio)
aact-mcp-server

# Or run as a module
python -m src
```

#### 2. HTTP Mode

This mode exposes the MCP server as a network service, allowing it to be called from other backends (like the CT.Sight web app). It uses the `streamable-http` transport from the `mcp` library.

```bash
# Set environment variables and run
export AACT_MCP_TRANSPORT=streamable-http
export AACT_MCP_HOST=0.0.0.0
export AACT_MCP_PORT=8001

aact-mcp-server
```

The following environment variables are available for HTTP mode:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `AACT_MCP_TRANSPORT` | `stdio` | Set to `streamable-http` to enable HTTP mode. |
| `AACT_MCP_HOST` | `127.0.0.1` | Host to bind the HTTP server to. Use `0.0.0.0` for Docker. |
| `AACT_MCP_PORT` | `8001` | Port to listen on. |

When running in HTTP mode, two endpoints are available:

-   **MCP Endpoint**: `http://<host>:<port>/mcp`
-   **Health Check**: `http://<host>:<port>/health` (returns a JSON object with server status)

## Testing

A test script is included to verify the server works correctly.

```bash
python test_server.py
```

This script exercises all 4 resources and validates their output, including checking that rich descriptions are present.

## Updating the Schema

When the AACT database schema changes (new tables, renamed columns, etc.), the bundled JSON snapshot needs to be regenerated. This is a **semi-automated process that requires an LLM** because the rich descriptions are not stored in the database itself — they must be extracted from the AACT data dictionary and merged with the structural data from `schema.rb`.

See **[UPDATING_SCHEMA.md](UPDATING_SCHEMA.md)** for the full step-by-step guide.

---

## MCP Flow Visualizer (Docker)

The `visualizer/` directory contains an interactive web app that demonstrates the full MCP architecture. It lets you type a clinical trials query in natural language (any language) and watch the entire message flow unfold step by step — from user to backend to LLM to MCP server and back — with real JSON-RPC payloads and actual LLM-generated SQL.

### Quick Start with Docker

```bash
# 1. Clone the repo
git clone https://github.com/pakejeso/aatc_mcp_server.git
cd aatc_mcp_server

# 2. Create your .env file
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY (required for the visualizer)

# 3. Build and run
docker compose up --build

# 4. Open in your browser
#    http://localhost:8090
```

### Environment Variables for the Visualizer

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `OPENAI_API_KEY` | **Yes** | — | Your OpenAI API key (or any OpenAI-compatible key) |
| `OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | Override for compatible endpoints (Azure, local LLMs, etc.) |
| `LLM_MODEL` | No | `gpt-4.1-mini` | Model to use for SQL generation |
| `PORT` | No | `8090` | Port the visualizer listens on |

### What the Visualizer Shows

The app uses the **real MCP server** (spawned as a subprocess) and follows the efficient 3-step strategy. Every JSON-RPC message shown in the UI was actually exchanged. The flow has up to 11 steps:

| Step | Direction | What Happens |
| :---: | :--- | :--- |
| 1 | User → Backend | Natural language query sent as HTTP request |
| 2 | Backend → LLM | LLM identifies which tables are relevant (using `aact://tables` list) |
| 3 | LLM → Backend | LLM returns relevant table names (e.g., 2 of 48) |
| 4 | Backend → MCP Server | JSON-RPC `initialize` handshake |
| 5 | Backend ↔ MCP Server | Discover available Resources via `resources/list` |
| 6 | Backend ← MCP Server | Read table list via `aact://tables` |
| 7–N | Backend ← MCP Server | Read only the relevant table schemas via `aact://schema/{table}` |
| N+1 | Backend ← MCP Server | Read relationships via `aact://relationships` |
| N+2 | Backend → LLM | LLM generates SQL from targeted schema only |
| N+3 | Backend → User | Backend validates and returns the SQL |

The app also shows an **Efficiency Report** comparing tokens used (targeted) vs. tokens that would be used (full schema). Each step has an expandable panel showing the actual JSON-RPC messages exchanged. Example queries are provided in English, Spanish, and French.

### Running the Visualizer Without Docker

```bash
# Install both the MCP server and visualizer dependencies
pip install -e .
pip install -r visualizer/requirements.txt

# Set your API key and run
export OPENAI_API_KEY=sk-your-key
cd visualizer
python app.py
# Open http://localhost:8090
```

Note: The visualizer must be able to find the `src/` and `data/` directories in the parent directory (the repo root). Always run `app.py` from the `visualizer/` directory.

## New MCP Resources (v2)

In addition to the original schema resources, the server now exposes three new knowledge resources:

### `aact://glossary` — Clinical Trial Terminology Glossary
Maps clinical trial vocabulary (endpoints, sites, adverse events, arms, eligibility, etc.) to the correct AACT tables and columns. Includes critical warnings about Protocol vs Results domain tables.

### `aact://column-profiles` — Column Value Profiles
Statistical profiles of key columns showing actual data values, enumerations, ranges, and samples. Essential for generating correct SQL with the right values and case. **Requires running the profiling script once against your database** (see below).

### `aact://query-patterns` — Common SQL Query Patterns
Tested SQL templates for the most common clinical trial questions. Includes SQL conventions and best practices.

### Generating Column Profiles

The `aact://column-profiles` resource requires a `data/column_profiles.json` file generated from your live database:

```bash
export AACT_DATABASE_URL="postgresql://user:pass@host:5432/aact"
cd data/
python generate_column_profiles.py
```

This profiles ~70 key columns using a token-efficient strategy:
- **Enum columns** (≤50 distinct values): full list with counts
- **Text columns** (high cardinality): n_distinct + 5 random samples
- **Numeric columns**: min, max, median, mean
- **Date columns**: min, max range
- **Boolean columns**: true/false/null counts
