# AACT Schema MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A read-only, lightweight [Model Context Protocol (MCP)](https://modelcontext.dev/) server that exposes the AACT (Aggregate Analysis of ClinicalTrials.gov) database schema as a set of structured **Resources**. It is designed to provide an LLM with the necessary structural context to generate accurate SQL queries against the AACT database.

This server is a key component in a hybrid "Human-in-the-loop" architecture for SQL generation. It provides the schema context to the LLM but **does not execute any queries**, ensuring a safe and secure separation of concerns.

The schema is loaded from a bundled JSON snapshot (`data/aact_schema_static.json`) that includes rich, human-readable descriptions sourced from the official AACT data dictionary. See [UPDATING_SCHEMA.md](UPDATING_SCHEMA.md) for instructions on regenerating this file when the AACT schema changes.

The repository also includes an **interactive MCP Flow Visualizer** — a web app that demonstrates how the entire MCP architecture works, step by step, using real LLM calls.

## Architectural Context

This server is designed to fit into the following workflow:

1.  A **main backend** receives a free-text request from a user (e.g., "find phase 3 trials for diabetes").
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

The server runs over `stdio` by default, as is common for MCP servers invoked by a parent process.

```bash
# Run using the installed script
aact-mcp-server

# Or run as a module
python -m src
```

No configuration is needed. The server reads from the bundled `data/aact_schema_static.json` file automatically.

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

The app animates 7 steps that correspond to the real MCP protocol flow:

| Step | Direction | What Happens |
| :---: | :--- | :--- |
| 1 | User → Backend | Natural language query sent as HTTP request |
| 2 | Backend → LLM | Backend wraps the query in a prompt |
| 3 | LLM → MCP Server | JSON-RPC `initialize` handshake |
| 4 | LLM ↔ MCP Server | LLM discovers available Resources via `resources/list` |
| 5 | LLM ← MCP Server | LLM reads the full schema via `resources/read` |
| 6 | LLM → Backend | LLM generates SQL using schema + user query |
| 7 | Backend → User | Backend validates and returns the SQL |

Each step has an expandable panel showing the actual JSON-RPC messages exchanged. Example queries are provided in English, Spanish, and French.

### Running the Visualizer Without Docker

```bash
cd visualizer
pip install -r requirements.txt
export OPENAI_API_KEY=sk-your-key
python app.py
# Open http://localhost:8090
```
