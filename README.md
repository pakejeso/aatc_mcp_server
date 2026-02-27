# AACT Schema MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A read-only, lightweight [Model Context Protocol (MCP)](https://modelcontext.dev/) server that exposes the AACT (Aggregate Analysis of ClinicalTrials.gov) database schema and reference data as a set of structured **Resources**. It provides the structural context a consuming application needs to generate accurate SQL queries against the AACT database.

This server is a key component in the **CT.Sight** architecture. It serves schema definitions, a clinical-trial glossary, column-value profiles, and tested SQL query patterns — but **does not execute any queries and does not call any LLM**. It is a pure data-serving process with zero external dependencies beyond the `mcp` Python package.

The schema and reference data are loaded from bundled JSON files in the `data/` directory. See [UPDATING_SCHEMA.md](UPDATING_SCHEMA.md) for instructions on regenerating these files when the AACT schema changes.

## Architectural Context

This server fits into the following workflow:

1. The **CT.Sight backend** (FastAPI app, running in Docker) receives a free-text query from a user.
2. The backend calls this **AACT Schema MCP Server** (running locally on the host, outside Docker) over HTTP to read schema resources.
3. The backend passes the schema context to an **LLM**, which generates a SQL query.
4. The backend **validates, sanitizes, and executes** the SQL query against the actual AACT database.

This server **only** performs step 2. It has no database connection, no LLM integration, and no ability to execute SQL.

> **See [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for step-by-step instructions on running this server locally on Windows alongside the CT.Sight Docker stack.**

## Features

- **Read-Only by Design**: Exposes schema information via MCP Resources only. No MCP Tools are implemented.
- **Static Schema with Rich Descriptions**: The bundled JSON snapshot includes table and column descriptions from the official AACT data dictionary, domain classification, and cardinality metadata.
- **Reference Data Resources**: Glossary, column-value profiles, and tested SQL query patterns complement the raw schema.
- **Multiple Resource Granularities**: Provides both the full schema and per-table resources for efficient context loading.
- **Dual Transport Modes**: Supports both `stdio` (for subprocess use) and `streamable-http` (for network-based use by CT.Sight).
- **Zero Configuration**: No database connection, no API keys, no `.env` file. Install, run, done.

## MCP Resources

The server exposes the following resources:

| URI Template | Name | Description |
|:---|:---|:---|
| `aact://schema` | Full Schema | Complete DDL-style schema of all 48 tables, including columns, types, keys, and all 63 foreign key relationships. |
| `aact://schema/{table_name}` | Table Schema | DDL for a single table. Use for surgical, on-demand context loading. |
| `aact://tables` | Table List | All tables with descriptions, column counts, and domain classification. Read this first to identify relevant tables. |
| `aact://relationships` | Relationships | All foreign key relationships, separated into `nct_id` joins and hierarchical FKs. |
| `aact://glossary` | Glossary | Maps clinical trial terminology to the correct AACT tables and columns. Includes warnings about Protocol vs Results domain tables. |
| `aact://column-profiles` | Column Profiles Summary | Lightweight summary listing which tables have column profiles available. |
| `aact://column-profiles/{table_name}` | Column Profiles (per table) | Statistical profiles of key columns: enumerations, ranges, samples. Essential for generating correct SQL. |
| `aact://query-patterns` | Query Patterns | Tested SQL templates for common clinical trial questions. |

## Getting Started

### Prerequisites

- Python 3.10+

### Installation

```bash
git clone https://github.com/pakejeso/aatc_mcp_server.git
cd aatc_mcp_server
pip install -e .
```

### Running the Server

The server supports two transport modes, controlled by the `AACT_MCP_TRANSPORT` environment variable.

#### Stdio Mode (default)

Classic MCP transport for local/subprocess use, communicating over `stdin`/`stdout`.

```bash
aact-mcp-server
```

#### HTTP Mode (for CT.Sight integration)

This is the mode used when the CT.Sight backend (running in Docker) needs to reach the MCP server over the network.

**Linux / macOS:**

```bash
export AACT_MCP_TRANSPORT=streamable-http
export AACT_MCP_HOST=0.0.0.0
export AACT_MCP_PORT=8001

aact-mcp-server
```

**Windows PowerShell:**

```powershell
$env:AACT_MCP_TRANSPORT = "streamable-http"
$env:AACT_MCP_HOST = "0.0.0.0"
$env:AACT_MCP_PORT = "8001"

aact-mcp-server
```

The following environment variables are available for HTTP mode:

| Variable | Default | Description |
|:---|:---|:---|
| `AACT_MCP_TRANSPORT` | `stdio` | Set to `streamable-http` to enable HTTP mode. |
| `AACT_MCP_HOST` | `127.0.0.1` | Host to bind to. Use `0.0.0.0` to accept connections from Docker containers. |
| `AACT_MCP_PORT` | `8001` | Port to listen on. |

When running in HTTP mode, two endpoints are available:

- **MCP Endpoint**: `http://<host>:<port>/mcp`
- **Health Check**: `http://<host>:<port>/health`

## Testing

```bash
python test_server.py
```

This script exercises all resources and validates their output, including checking that rich descriptions are present.

## Bundled Data Files

| File | Description |
|:---|:---|
| `data/aact_schema_static.json` | Full schema snapshot: 48 tables, 63 FKs, rich descriptions from the AACT data dictionary. |
| `data/glossary.json` | Clinical trial terminology mapped to AACT tables and columns. |
| `data/column_profiles.json` | Statistical profiles of key columns (enums with counts, numeric ranges, date ranges, samples). |
| `data/query_patterns.json` | Tested SQL templates for common clinical trial questions. |

### Regenerating Column Profiles

The column profiles file can be regenerated from a live AACT database:

```bash
export AACT_DATABASE_URL="postgresql://user:pass@host:5432/aact"
cd data/
python generate_column_profiles.py
```

## Updating the Schema

When the AACT database schema changes, the bundled JSON snapshot needs to be regenerated. See **[UPDATING_SCHEMA.md](UPDATING_SCHEMA.md)** for the full step-by-step guide.

## License

[MIT](https://opensource.org/licenses/MIT)
