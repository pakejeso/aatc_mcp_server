"""
AACT Schema MCP Server
======================
A read-only MCP server that exposes the AACT (Aggregate Analysis of
ClinicalTrials.gov) PostgreSQL database schema as MCP Resources.

This server provides structural context (tables, columns, types, keys,
relationships) so that an LLM can generate accurate SQL queries. It does
NOT execute any SQL against the database.

Two operating modes:
  1. **Live DB** – queries PostgreSQL information_schema at runtime.
     Requires AACT_DB_* environment variables to be set.
  2. **Static fallback** – uses a bundled JSON snapshot of the schema.
     Activated automatically when DB connection is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
# Schema provider abstraction
# ---------------------------------------------------------------------------

class SchemaProvider:
    """Abstract interface for fetching schema metadata."""

    async def get_tables(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get_table(self, table_name: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def get_foreign_keys(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get_table_names(self) -> list[str]:
        raise NotImplementedError


class LiveDBProvider(SchemaProvider):
    """Fetches schema metadata from a live PostgreSQL instance."""

    def __init__(self):
        self._pool = None
        self._schema = os.getenv("AACT_DB_SCHEMA", "ctgov")

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                host=os.getenv("AACT_DB_HOST", "localhost"),
                port=int(os.getenv("AACT_DB_PORT", "5432")),
                database=os.getenv("AACT_DB_NAME", "aact"),
                user=os.getenv("AACT_DB_USER", ""),
                password=os.getenv("AACT_DB_PASS", ""),
                min_size=1,
                max_size=3,
                command_timeout=30,
            )
        return self._pool

    async def get_table_names(self) -> list[str]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = $1 AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            self._schema,
        )
        return [r["table_name"] for r in rows]

    async def get_tables(self) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        schema = self._schema

        # Columns
        col_rows = await pool.fetch(
            """
            SELECT table_name, column_name, data_type, is_nullable,
                   ordinal_position
            FROM information_schema.columns
            WHERE table_schema = $1
            ORDER BY table_name, ordinal_position
            """,
            schema,
        )

        # Primary keys
        pk_rows = await pool.fetch(
            """
            SELECT kcu.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = $1
            """,
            schema,
        )
        pk_set = {(r["table_name"], r["column_name"]) for r in pk_rows}

        # Foreign keys
        fk_rows = await pool.fetch(
            """
            SELECT
                kcu.table_name  AS child_table,
                kcu.column_name AS child_column,
                ccu.table_name  AS parent_table,
                ccu.column_name AS parent_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema   = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema   = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = $1
            """,
            schema,
        )
        fk_child_set = {(r["child_table"], r["child_column"]) for r in fk_rows}

        # Group columns by table
        tables: dict[str, list[dict]] = {}
        for r in col_rows:
            tname = r["table_name"]
            tables.setdefault(tname, [])
            tables[tname].append({
                "column_name": r["column_name"],
                "data_type": r["data_type"],
                "is_nullable": r["is_nullable"],
                "is_primary_key": (tname, r["column_name"]) in pk_set,
                "is_foreign_key": (tname, r["column_name"]) in fk_child_set,
                "description": "",
            })

        return [
            {"table_name": tname, "table_schema": schema, "columns": cols}
            for tname, cols in sorted(tables.items())
        ]

    async def get_table(self, table_name: str) -> dict[str, Any] | None:
        all_tables = await self.get_tables()
        for t in all_tables:
            if t["table_name"] == table_name:
                return t
        return None

    async def get_foreign_keys(self) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT
                kcu.table_name  AS child_table,
                kcu.column_name AS child_column,
                ccu.table_name  AS parent_table,
                ccu.column_name AS parent_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema   = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema   = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = $1
            ORDER BY kcu.table_name, kcu.column_name
            """,
            self._schema,
        )
        return [dict(r) for r in rows]


class StaticProvider(SchemaProvider):
    """Loads schema metadata from a bundled JSON file."""

    def __init__(self):
        data_path = Path(__file__).parent.parent / "data" / "aact_schema_static.json"
        with open(data_path) as f:
            self._data = json.load(f)
        logger.info(
            "Static schema loaded: %d tables, %d foreign keys",
            len(self._data["tables"]),
            len(self._data["foreign_keys"]),
        )

    async def get_table_names(self) -> list[str]:
        return [t["table_name"] for t in self._data["tables"]]

    async def get_tables(self) -> list[dict[str, Any]]:
        return self._data["tables"]

    async def get_table(self, table_name: str) -> dict[str, Any] | None:
        for t in self._data["tables"]:
            if t["table_name"] == table_name:
                return t
        return None

    async def get_foreign_keys(self) -> list[dict[str, Any]]:
        return self._data["foreign_keys"]


# ---------------------------------------------------------------------------
# Provider initialization
# ---------------------------------------------------------------------------

_provider: SchemaProvider | None = None


async def get_provider() -> SchemaProvider:
    """Return the active schema provider, initializing on first call."""
    global _provider
    if _provider is not None:
        return _provider

    # Try live DB first
    if os.getenv("AACT_DB_HOST") and os.getenv("AACT_DB_USER"):
        try:
            import asyncpg  # noqa: F401
            live = LiveDBProvider()
            # Test the connection
            names = await live.get_table_names()
            logger.info("Live DB connected: %d tables found", len(names))
            _provider = live
            return _provider
        except Exception as e:
            logger.warning("Live DB unavailable (%s), falling back to static schema", e)

    # Fall back to static
    _provider = StaticProvider()
    return _provider


# ---------------------------------------------------------------------------
# Schema formatting helpers
# ---------------------------------------------------------------------------

def format_column_ddl(col: dict[str, Any]) -> str:
    """Format a single column as a DDL line."""
    name = col["column_name"]
    dtype = col["data_type"]
    nullable = "" if col.get("is_nullable", "YES") == "YES" else " NOT NULL"
    pk = " PRIMARY KEY" if col.get("is_primary_key") else ""
    comment = ""
    desc = col.get("description", "")
    if desc:
        # Truncate long descriptions for LLM readability
        short = desc[:120].replace("--", "-").replace("\n", " ")
        if len(desc) > 120:
            short += "..."
        comment = f"  -- {short}"
    return f"    {name} {dtype}{nullable}{pk},{comment}"


def format_table_ddl(table: dict[str, Any], fks: list[dict[str, Any]]) -> str:
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
        lines.append(format_column_ddl(col))

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


def format_full_schema(tables: list[dict], fks: list[dict]) -> str:
    """Format the entire schema as DDL text."""
    header = [
        "-- =============================================================",
        "-- AACT Database Schema (ctgov)",
        "-- Aggregate Analysis of ClinicalTrials.gov",
        "-- =============================================================",
        f"-- {len(tables)} tables | {len(fks)} foreign key relationships",
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

    blocks = []
    for table in tables:
        blocks.append(format_table_ddl(table, fks))

    # Append a relationship summary section
    summary = [
        "",
        "-- =============================================================",
        "-- FOREIGN KEY RELATIONSHIP SUMMARY",
        "-- =============================================================",
    ]
    for fk in fks:
        summary.append(
            f"-- {fk['child_table']}.{fk['child_column']} "
            f"-> {fk['parent_table']}.{fk['parent_column']}"
        )

    return "\n".join(header) + "\n\n".join(blocks) + "\n" + "\n".join(summary) + "\n"


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------

@mcp.resource(
    "postgres://aact/schema",
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
    provider = await get_provider()
    tables = await provider.get_tables()
    fks = await provider.get_foreign_keys()
    return format_full_schema(tables, fks)


@mcp.resource(
    "postgres://aact/schema/{table_name}",
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
    provider = await get_provider()
    table = await provider.get_table(table_name)
    if table is None:
        names = await provider.get_table_names()
        return (
            f"-- ERROR: Table '{table_name}' not found in the AACT schema.\n"
            f"-- Available tables: {', '.join(names)}\n"
        )
    fks = await provider.get_foreign_keys()
    # Include FKs where this table is child OR parent
    relevant_fks = [
        fk for fk in fks
        if fk["child_table"] == table_name or fk["parent_table"] == table_name
    ]

    lines = [format_table_ddl(table, fks), ""]

    # Also show relationships where this table is the parent
    parent_fks = [fk for fk in fks if fk["parent_table"] == table_name]
    if parent_fks:
        lines.append(f"-- Tables referencing {table_name}:")
        for fk in parent_fks:
            lines.append(
                f"--   {fk['child_table']}.{fk['child_column']} "
                f"-> {table_name}.{fk['parent_column']}"
            )

    child_fks = [fk for fk in fks if fk["child_table"] == table_name]
    if child_fks:
        lines.append(f"-- {table_name} references:")
        for fk in child_fks:
            lines.append(
                f"--   {table_name}.{fk['child_column']} "
                f"-> {fk['parent_table']}.{fk['parent_column']}"
            )

    return "\n".join(lines) + "\n"


@mcp.resource(
    "postgres://aact/tables",
    name="AACT Table List",
    title="List of All AACT Tables",
    description=(
        "A concise list of all tables in the AACT database with their "
        "column counts and domain classification. Use this as a quick "
        "reference to identify which tables are relevant before requesting "
        "the full schema or individual table details."
    ),
    mime_type="text/plain",
)
async def table_list() -> str:
    """Return a concise list of all AACT tables."""
    provider = await get_provider()
    tables = await provider.get_tables()

    lines = [
        "AACT Database Tables (ctgov schema)",
        "=" * 50,
        "",
        f"{'Table':<35} {'Cols':>5}  {'Domain':<20}  {'Rows/Study':<10}",
        "-" * 80,
    ]
    for t in tables:
        name = t["table_name"]
        ncols = len(t["columns"])
        domain = t.get("domain", "")
        rows_per = t.get("rows_per_study", "")
        lines.append(f"{name:<35} {ncols:>5}  {domain:<20}  {rows_per:<10}")

    lines.append("")
    lines.append(f"Total: {len(tables)} tables")
    return "\n".join(lines) + "\n"


@mcp.resource(
    "postgres://aact/relationships",
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
    provider = await get_provider()
    fks = await provider.get_foreign_keys()

    nct_fks = [fk for fk in fks if fk["child_column"] == "nct_id"]
    other_fks = [fk for fk in fks if fk["child_column"] != "nct_id"]

    lines = [
        "AACT Database Foreign Key Relationships",
        "=" * 50,
        "",
        f"Total: {len(fks)} relationships",
        "",
        "--- nct_id joins (link tables to studies) ---",
        "",
    ]
    for fk in nct_fks:
        lines.append(
            f"  {fk['child_table']}.nct_id -> studies.nct_id"
        )

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
