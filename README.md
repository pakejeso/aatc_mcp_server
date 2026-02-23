# AACT Schema MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A read-only, lightweight [Model Context Protocol (MCP)](https://modelcontext.dev/) server that exposes the AACT (Aggregate Analysis of ClinicalTrials.gov) PostgreSQL database schema as a set of structured **Resources**. It is designed to provide an LLM with the necessary structural context to generate accurate SQL queries against the AACT database.

This server is a key component in a hybrid "Human-in-the-loop" architecture for SQL generation. It provides the schema context to the LLM but **does not execute any queries**, ensuring a safe and secure separation of concerns.

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
-   **Dual-Mode Operation**:
    -   **Live DB Mode**: Connects to a PostgreSQL database and dynamically queries `information_schema`.
    -   **Static Fallback Mode**: If DB credentials are not provided or the connection fails, it serves a bundled, comprehensive snapshot of the AACT schema.
-   **Comprehensive Schema Output**: The schema is formatted as a pseudo-DDL (`CREATE TABLE ...`) text block, which is highly effective for LLM comprehension.
-   **Rich Metadata**: Includes table and column descriptions, data types, nullability, primary keys, and a full foreign key relationship summary.
-   **Multiple Resource Granularities**: Provides both the full schema and per-table resources for efficient context loading.

## MCP Resources

The server exposes the following resources:

| URI Template | Name | Description |
| :--- | :--- | :--- |
| `postgres://aact/schema` | AACT Full Schema | The complete schema of all 48 tables, including columns, types, keys, and all 63 foreign key relationships. The primary resource for initial context. |
| `postgres://aact/schema/{table_name}` | AACT Table Schema | The schema for a single table. Use this for surgical, on-demand context loading. |
| `postgres://aact/tables` | AACT Table List | A concise list of all tables with their column counts and domain classification (Protocol, Results, etc.). |
| `postgres://aact/relationships` | AACT Relationships | A summary of all foreign key relationships, separated into `nct_id` joins and hierarchical FKs. |

## Getting Started

### Prerequisites

-   Python 3.10+
-   Access to a PostgreSQL instance with the AACT schema (optional, for Live DB mode)

### Installation

1.  Clone the repository:
    ```bash
    git clone <repo_url>
    cd aact-mcp-server
    ```

2.  Install dependencies:
    ```bash
    pip install -e .
    ```

### Running the Server

The server runs over `stdio` by default, as is common for MCP servers invoked by a parent process.

```bash
# Run using the installed script
aact-mcp-server

# Or run as a module
python -m src
```

### Configuration (Live DB Mode)

To connect to a live PostgreSQL database, create a `.env` file in the project root (or set environment variables) with your connection details. See `.env.example` for the required variables.

```dotenv
# .env
AACT_DB_HOST=your-rds-instance.amazonaws.com
AACT_DB_PORT=5432
AACT_DB_NAME=aact
AACT_DB_USER=your_readonly_user
AACT_DB_PASS=your_password
AACT_DB_SCHEMA=ctgov
```

If these variables are not set, the server will automatically use the bundled static schema.

## Testing

A test script is included to verify the server works correctly in static fallback mode.

```bash
python test_server.py
```

This script exercises all available resources and validates their output without requiring a database connection.
