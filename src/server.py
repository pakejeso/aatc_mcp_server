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
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("aact-mcp-server")

# ---------------------------------------------------------------------------
# MCP Server instance
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "aact-schema",
    instructions=(
        "This server provides read-only access to the AACT clinical trials "
        "database **schema**. Use the resources to understand table structure, "
        "column types, primary keys, and foreign key relationships before "
        "writing SQL queries. The database schema is 'ctgov'. "
        "All tables live under the ctgov schema (e.g. ctgov.studies)."
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
        "Complete DDL-style schema of the AACT clinical trials database. "
        "Includes all tables, columns with PostgreSQL types, primary keys, "
        "foreign key constraints, and relationship summary. "
        "Use this to understand the full database structure before writing SQL."
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
        "for the specified table. Use when you need detail on one table "
        "without loading the full schema."
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
        "List of all tables in the AACT database with their descriptions, "
        "column counts, and domain classification. Read this first to identify "
        "which tables are relevant, then use aact://schema/{table_name} for "
        "detailed column definitions."
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
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server via stdio transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
