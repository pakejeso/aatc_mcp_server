"""
AACT Schema MCP Server
======================
A read-only MCP server that exposes the AACT (Aggregate Analysis of
ClinicalTrials.gov) database schema as MCP Resources.

This server provides structural context (tables, columns, types, keys,
relationships, and rich descriptions) so that an LLM can generate accurate
SQL queries. It does NOT execute any SQL against the database.

The schema is loaded from a bundled JSON snapshot
(data/aact_schema_static.json) which includes rich descriptions sourced
from the official AACT data dictionary and documentation. See
UPDATING_SCHEMA.md for instructions on how to regenerate this file when
the AACT schema changes.

Transport Modes
---------------
The server supports two transport modes, controlled by the AACT_MCP_TRANSPORT
environment variable (default: "stdio"):

  AACT_MCP_TRANSPORT=stdio          — Classic stdio mode for local/subprocess use.
  AACT_MCP_TRANSPORT=streamable-http — HTTP service mode for remote/web use.

When running in HTTP mode, host and port are also configurable:
  AACT_MCP_HOST=0.0.0.0   (default: 127.0.0.1)
  AACT_MCP_PORT=8001       (default: 8001)

The HTTP endpoint is served at: http://<host>:<port>/mcp
A health-check endpoint is available at: http://<host>:<port>/health
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("aact-mcp-server")

# ---------------------------------------------------------------------------
# Transport configuration (from environment variables)
# ---------------------------------------------------------------------------

_TRANSPORT = os.environ.get("AACT_MCP_TRANSPORT", "stdio").lower()
_HOST = os.environ.get("AACT_MCP_HOST", "127.0.0.1")
_PORT = int(os.environ.get("AACT_MCP_PORT", "8001"))

# ---------------------------------------------------------------------------
# MCP Server instance
# ---------------------------------------------------------------------------

# When running in HTTP mode we need to allow cross-origin requests from the
# CT.Sight backend.  The FastMCP transport-security defaults block unknown
# hosts, so we relax them when the server is deployed as a network service.
if _TRANSPORT == "streamable-http":
    from mcp.server.fastmcp.server import TransportSecuritySettings

    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
else:
    _transport_security = None  # type: ignore[assignment]

mcp = FastMCP(
    "aact-schema",
    host=_HOST,
    port=_PORT,
    transport_security=_transport_security,
    instructions=(
        "This server provides read-only access to the AACT clinical trials "
        "database **schema** (PostgreSQL, schema name: ctgov). "
        "All tables live under the ctgov schema (e.g. ctgov.studies).\n"
        "\n"
        "IMPORTANT — Follow this resource reading strategy for efficiency:\n"
        "\n"
        "Step 1: Read aact://tables FIRST. It lists all 48 tables with their "
        "descriptions, column counts, and domain classification. Use the "
        "descriptions to identify which tables are relevant to the user's query.\n"
        "\n"
        "Step 2: Read aact://schema/{table_name} for ONLY the tables you "
        "identified as relevant. This gives you the full column definitions, "
        "data types, and foreign key constraints for that specific table.\n"
        "\n"
        "Step 3: If you need to understand how tables connect, read "
        "aact://relationships for the complete foreign key map.\n"
        "\n"
        "AVOID reading aact://schema (the full schema) unless the query is "
        "complex enough to require most or all tables. The full schema is "
        "~10K tokens — prefer targeted per-table reads when possible.\n"
        "\n"
        "Key facts: Most tables join to 'studies' via nct_id (VARCHAR). "
        "Results tables have hierarchical FK chains (e.g. outcomes -> "
        "outcome_analyses -> outcome_analysis_groups)."
    ),
)

# ---------------------------------------------------------------------------
# Load static schema
# ---------------------------------------------------------------------------

_DATA_PATH = Path(__file__).parent.parent / "data" / "aact_schema_static.json"

with open(_DATA_PATH) as _f:
    _SCHEMA = json.load(_f)

_TABLES: list[dict[str, Any]] = _SCHEMA["tables"]
_FOREIGN_KEYS: list[dict[str, Any]] = _SCHEMA["foreign_keys"]
_TABLE_INDEX: dict[str, dict[str, Any]] = {t["table_name"]: t for t in _TABLES}

logger.info(
    "Schema loaded: %d tables, %d foreign keys",
    len(_TABLES),
    len(_FOREIGN_KEYS),
)


# ---------------------------------------------------------------------------
# Schema formatting helpers
# ---------------------------------------------------------------------------

def _format_column_ddl(col: dict[str, Any]) -> str:
    """Format a single column as a DDL line."""
    name = col["column_name"]
    dtype = col["data_type"]
    nullable = "" if col.get("is_nullable", "YES") == "YES" else " NOT NULL"
    pk = " PRIMARY KEY" if col.get("is_primary_key") else ""
    comment = ""
    desc = col.get("description", "")
    if desc:
        short = desc[:120].replace("--", "-").replace("\n", " ")
        if len(desc) > 120:
            short += "..."
        comment = f"  -- {short}"
    return f"    {name} {dtype}{nullable}{pk},{comment}"


def _format_table_ddl(table: dict[str, Any], fks: list[dict[str, Any]]) -> str:
    """Format a full table as a CREATE TABLE DDL block."""
    tname = table["table_name"]
    schema = table.get("table_schema", "ctgov")
    desc = table.get("description", "")
    domain = table.get("domain", "")
    rows_per = table.get("rows_per_study", "")

    lines = []

    # Table-level comment
    meta_parts = []
    if domain:
        meta_parts.append(f"Domain: {domain}")
    if rows_per:
        meta_parts.append(f"Rows per study: {rows_per}")
    if meta_parts:
        lines.append(f"-- {' | '.join(meta_parts)}")
    if desc:
        short_desc = desc[:200].replace("\n", " ")
        if len(desc) > 200:
            short_desc += "..."
        lines.append(f"-- {short_desc}")

    lines.append(f"CREATE TABLE {schema}.{tname} (")

    for col in table["columns"]:
        lines.append(_format_column_ddl(col))

    # Foreign key constraints for this table
    table_fks = [fk for fk in fks if fk["child_table"] == tname]
    for fk in table_fks:
        lines.append(
            f"    FOREIGN KEY ({fk['child_column']}) "
            f"REFERENCES {schema}.{fk['parent_table']}({fk['parent_column']}),"
        )

    # Remove trailing comma from last line
    if lines and lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]

    lines.append(");")
    return "\n".join(lines)


def _format_full_schema() -> str:
    """Format the entire schema as DDL text."""
    header = [
        "-- =============================================================",
        "-- AACT Database Schema (ctgov)",
        "-- Aggregate Analysis of ClinicalTrials.gov",
        "-- =============================================================",
        f"-- {len(_TABLES)} tables | {len(_FOREIGN_KEYS)} foreign key relationships",
        "--",
        "-- Key relationships:",
        "--   Most tables join to 'studies' via nct_id (VARCHAR).",
        "--   Results tables have hierarchical FK chains, e.g.:",
        "--     outcomes.id -> outcome_analyses.outcome_id",
        "--     outcome_analyses.id -> outcome_analysis_groups.outcome_analysis_id",
        "--   The 'result_groups' table is referenced by baseline and outcome tables.",
        "-- =============================================================",
        "",
    ]

    blocks = [_format_table_ddl(t, _FOREIGN_KEYS) for t in _TABLES]

    summary = [
        "",
        "-- =============================================================",
        "-- FOREIGN KEY RELATIONSHIP SUMMARY",
        "-- =============================================================",
    ]
    for fk in _FOREIGN_KEYS:
        summary.append(
            f"-- {fk['child_table']}.{fk['child_column']} "
            f"-> {fk['parent_table']}.{fk['parent_column']}"
        )

    return "\n".join(header) + "\n\n".join(blocks) + "\n" + "\n".join(summary) + "\n"


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------

@mcp.resource(
    "aact://schema",
    name="AACT Full Schema",
    title="Complete AACT Database Schema",
    description=(
        "Complete DDL-style schema of ALL 48 AACT tables (~10K tokens). "
        "Includes columns, PostgreSQL types, primary keys, foreign key "
        "constraints, and relationship summary. "
        "WARNING: This is a large resource. Prefer reading aact://tables first "
        "to identify relevant tables, then use aact://schema/{table_name} for "
        "only the tables you need. Use this full schema only when the query "
        "requires many tables or you cannot determine which tables are needed."
    ),
    mime_type="text/plain",
)
async def full_schema() -> str:
    """Return the complete AACT schema as DDL text."""
    return _format_full_schema()


@mcp.resource(
    "aact://schema/{table_name}",
    name="AACT Table Schema",
    title="Single Table Schema",
    description=(
        "DDL-style schema for a single AACT table. "
        "Returns columns, types, keys, and foreign key constraints "
        "for the specified table. PREFERRED approach: read aact://tables "
        "first to identify relevant tables by description, then request "
        "only the specific tables you need using this resource."
    ),
    mime_type="text/plain",
)
async def table_schema(table_name: str) -> str:
    """Return the schema for a single table as DDL text."""
    table = _TABLE_INDEX.get(table_name)
    if table is None:
        return (
            f"-- ERROR: Table '{table_name}' not found in the AACT schema.\n"
            f"-- Available tables: {', '.join(sorted(_TABLE_INDEX.keys()))}\n"
        )

    lines = [_format_table_ddl(table, _FOREIGN_KEYS), ""]

    # Show relationships where this table is the parent
    parent_fks = [fk for fk in _FOREIGN_KEYS if fk["parent_table"] == table_name]
    if parent_fks:
        lines.append(f"-- Tables referencing {table_name}:")
        for fk in parent_fks:
            lines.append(
                f"--   {fk['child_table']}.{fk['child_column']} "
                f"-> {table_name}.{fk['parent_column']}"
            )

    # Show relationships where this table is the child
    child_fks = [fk for fk in _FOREIGN_KEYS if fk["child_table"] == table_name]
    if child_fks:
        lines.append(f"-- {table_name} references:")
        for fk in child_fks:
            lines.append(
                f"--   {table_name}.{fk['child_column']} "
                f"-> {fk['parent_table']}.{fk['parent_column']}"
            )

    return "\n".join(lines) + "\n"


@mcp.resource(
    "aact://tables",
    name="AACT Table List",
    title="List of All AACT Tables",
    description=(
        "START HERE. List of all 48 tables in the AACT database with their "
        "descriptions, column counts, and domain classification. Read this "
        "first to identify which tables are relevant to the user's query, "
        "then use aact://schema/{table_name} for detailed column definitions "
        "of only the tables you need."
    ),
    mime_type="text/plain",
)
async def table_list() -> str:
    """Return a list of all AACT tables with descriptions."""
    lines = [
        "AACT Database Tables (ctgov schema)",
        "=" * 60,
        "",
        f"Total: {len(_TABLES)} tables",
        "",
    ]
    for t in _TABLES:
        name = t["table_name"]
        ncols = len(t["columns"])
        domain = t.get("domain", "")
        rows_per = t.get("rows_per_study", "")
        desc = t.get("description", "")

        meta_parts = []
        if domain:
            meta_parts.append(domain)
        meta_parts.append(f"{ncols} columns")
        if rows_per:
            meta_parts.append(f"{rows_per} per study")
        meta = " | ".join(meta_parts)

        lines.append(f"  {name}  ({meta})")
        if desc:
            short_desc = desc[:200].replace("\n", " ")
            if len(desc) > 200:
                short_desc += "..."
            lines.append(f"    {short_desc}")
        lines.append("")

    return "\n".join(lines) + "\n"


@mcp.resource(
    "aact://relationships",
    name="AACT Relationships",
    title="Foreign Key Relationships",
    description=(
        "All foreign key relationships in the AACT database, showing how "
        "tables connect to each other. Essential for writing correct JOINs. "
        "Includes both nct_id-based joins (linking to studies) and "
        "hierarchical FK chains (e.g. outcomes -> outcome_analyses)."
    ),
    mime_type="text/plain",
)
async def relationships() -> str:
    """Return all foreign key relationships."""
    nct_fks = [fk for fk in _FOREIGN_KEYS if fk["child_column"] == "nct_id"]
    other_fks = [fk for fk in _FOREIGN_KEYS if fk["child_column"] != "nct_id"]

    lines = [
        "AACT Database Foreign Key Relationships",
        "=" * 50,
        "",
        f"Total: {len(_FOREIGN_KEYS)} relationships",
        "",
        "--- nct_id joins (link tables to studies) ---",
        "",
    ]
    for fk in nct_fks:
        lines.append(f"  {fk['child_table']}.nct_id -> studies.nct_id")

    lines.extend([
        "",
        "--- Hierarchical FK relationships ---",
        "--- (Use these for multi-level JOINs beyond nct_id) ---",
        "",
    ])
    for fk in other_fks:
        lines.append(
            f"  {fk['child_table']}.{fk['child_column']} "
            f"-> {fk['parent_table']}.{fk['parent_column']}"
        )

    lines.extend([
        "",
        "--- Common JOIN patterns ---",
        "",
        "-- Get outcomes with their analyses:",
        "--   SELECT * FROM ctgov.outcomes o",
        "--   JOIN ctgov.outcome_analyses oa ON o.id = oa.outcome_id",
        "--   JOIN ctgov.outcome_analysis_groups oag ON oa.id = oag.outcome_analysis_id",
        "",
        "-- Get baseline data with result groups:",
        "--   SELECT * FROM ctgov.baseline_measurements bm",
        "--   JOIN ctgov.result_groups rg ON bm.result_group_id = rg.id",
        "",
        "-- Join any table to studies:",
        "--   SELECT * FROM ctgov.studies s",
        "--   JOIN ctgov.<table_name> t ON s.nct_id = t.nct_id",
    ])

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Health-check endpoint (HTTP mode only)
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """
    Liveness probe for Docker / load balancers and the CT.Sight backend.

    Returns a JSON object with server status, transport mode, and schema
    statistics so that clients can verify connectivity before sending queries.
    """
    return JSONResponse({
        "status": "ok",
        "server": "aact-mcp-server",
        "transport": _TRANSPORT,
        "tables": len(_TABLES),
        "foreign_keys": len(_FOREIGN_KEYS),
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run the AACT MCP server.

    Transport is selected via the AACT_MCP_TRANSPORT environment variable:
      - "stdio"            (default) — for local / subprocess / Claude Desktop use
      - "streamable-http"            — for remote / web-service use

    When using streamable-http, set AACT_MCP_HOST and AACT_MCP_PORT as needed.
    The MCP endpoint will be available at http://<host>:<port>/mcp
    The health endpoint will be available at http://<host>:<port>/health
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if _TRANSPORT == "streamable-http":
        logger.info(
            "Starting AACT MCP Server in HTTP mode on %s:%d", _HOST, _PORT
        )
        logger.info("MCP endpoint : http://%s:%d/mcp", _HOST, _PORT)
        logger.info("Health check : http://%s:%d/health", _HOST, _PORT)
        mcp.run(transport="streamable-http")
    else:
        logger.info("Starting AACT MCP Server in stdio mode")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
