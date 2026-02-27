"""
AACT Schema MCP Server
======================
A read-only MCP server that exposes the AACT (Aggregate Analysis of
ClinicalTrials.gov) database schema as MCP Resources.

This server provides structural context (tables, columns, types, keys,
relationships, and rich descriptions) so that a consuming application
(such as the CT.Sight backend) or an LLM can generate accurate SQL
queries. It does NOT execute any SQL against the database.

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
        "Step 1: Read aact://glossary FIRST. It maps clinical trial "
        "terminology (endpoints, sites, adverse events, etc.) to the correct "
        "AACT tables and warns about Protocol vs Results domain tables.\n"
        "\n"
        "Step 2: Read aact://tables to see all 48 tables with their "
        "descriptions, column counts, and domain classification. Use the "
        "descriptions to identify which tables are relevant to the user's query.\n"
        "\n"
        "Step 3: Read aact://schema/{table_name} for ONLY the tables you "
        "identified as relevant. This gives you the full column definitions, "
        "data types, and foreign key constraints for that specific table.\n"
        "\n"
        "Step 4: Read aact://column-profiles/{table_name} for ONLY the "
        "tables you identified as relevant. This gives you actual data values, "
        "enumerations, and case conventions for key columns. Essential for "
        "generating correct WHERE clauses. Do NOT read aact://column-profiles "
        "(the summary) unless you need to discover which tables have profiles.\n"
        "\n"
        "Step 5: Read aact://query-patterns for tested SQL templates that "
        "cover common clinical trial questions. Adapt these to the user's query.\n"
        "\n"
        "Step 6: If you need to understand how tables connect, read "
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
# Load supplementary data files (glossary, column profiles, query patterns)
# ---------------------------------------------------------------------------

_GLOSSARY_PATH = Path(__file__).parent.parent / "data" / "glossary.json"
_COLUMN_PROFILES_PATH = Path(__file__).parent.parent / "data" / "column_profiles.json"
_QUERY_PATTERNS_PATH = Path(__file__).parent.parent / "data" / "query_patterns.json"

_GLOSSARY: dict[str, Any] = {}
if _GLOSSARY_PATH.exists():
    with open(_GLOSSARY_PATH) as _f:
        _GLOSSARY = json.load(_f)
    logger.info("Glossary loaded: %d terminology entries", len(_GLOSSARY.get("terminology", [])))
else:
    logger.warning("Glossary file not found at %s", _GLOSSARY_PATH)

_COLUMN_PROFILES: dict[str, Any] = {}
_COLUMN_PROFILES_BY_TABLE: dict[str, dict[str, Any]] = {}  # table_name -> {col_key: profile}
if _COLUMN_PROFILES_PATH.exists():
    with open(_COLUMN_PROFILES_PATH) as _f:
        _COLUMN_PROFILES = json.load(_f)
    # Build per-table index for efficient per-table resource serving
    for _prof_key, _prof_val in _COLUMN_PROFILES.get("profiles", {}).items():
        _tbl = _prof_val.get("table", "")
        if _tbl:
            _COLUMN_PROFILES_BY_TABLE.setdefault(_tbl, {})[_prof_key] = _prof_val
    logger.info(
        "Column profiles loaded: %d profiles across %d tables",
        len(_COLUMN_PROFILES.get("profiles", {})),
        len(_COLUMN_PROFILES_BY_TABLE),
    )
else:
    logger.warning(
        "Column profiles file not found at %s. "
        "Run data/generate_column_profiles.py against your AACT database to generate it.",
        _COLUMN_PROFILES_PATH,
    )

_QUERY_PATTERNS: dict[str, Any] = {}
if _QUERY_PATTERNS_PATH.exists():
    with open(_QUERY_PATTERNS_PATH) as _f:
        _QUERY_PATTERNS = json.load(_f)
    logger.info("Query patterns loaded: %d patterns", len(_QUERY_PATTERNS.get("patterns", [])))
else:
    logger.warning("Query patterns file not found at %s", _QUERY_PATTERNS_PATH)


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
# New MCP Resources: Glossary, Column Profiles, Query Patterns
# ---------------------------------------------------------------------------


def _format_glossary() -> str:
    """Format the glossary JSON into a compact, LLM-readable text."""
    if not _GLOSSARY:
        return "Glossary not available. No glossary.json file found.\n"

    lines = [
        "AACT CLINICAL TRIAL TERMINOLOGY GLOSSARY",
        "=" * 50,
        "",
    ]

    # Domain rules
    for rule in _GLOSSARY.get("domain_rules", []):
        lines.append(f"RULE: {rule['rule']}")
        if "explanation" in rule:
            lines.append(f"  {rule['explanation']}")
        if "tables" in rule:
            lines.append(f"  Tables: {', '.join(rule['tables'])}")
        lines.append("")

    # Terminology mappings
    lines.append("TERMINOLOGY MAPPINGS")
    lines.append("-" * 40)
    lines.append("")

    for entry in _GLOSSARY.get("terminology", []):
        terms = ', '.join(f'"{t}"' for t in entry.get("terms", []))
        lines.append(f"Terms: {terms}")
        if "context" in entry:
            lines.append(f"  Context: {entry['context']}")
        lines.append(f"  Table: {entry['table']}")
        lines.append(f"  Domain: {entry['domain']}")
        if "key_columns" in entry:
            for col, desc in entry["key_columns"].items():
                lines.append(f"    {col}: {desc}")
        if "join_pattern" in entry:
            lines.append(f"  JOIN: {entry['join_pattern']}")
        if "warning" in entry:
            lines.append(f"  WARNING: {entry['warning']}")
        if "note" in entry:
            lines.append(f"  NOTE: {entry['note']}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _format_column_profile_entry(p: dict[str, Any]) -> str:
    """Format a single column profile entry as a compact one-liner."""
    col = p.get("column", "")
    ptype = p.get("profile_type", "")

    if ptype == "enum":
        values = p.get("values", {})
        val_list = ", ".join(
            f"{v} ({c:,})" for v, c in
            sorted(values.items(), key=lambda x: -x[1])[:20]
        )
        extra = ""
        if len(values) > 20:
            extra = f" ... and {len(values) - 20} more"
        return f"  {col} (enum, {len(values)} values): {val_list}{extra}"
    elif ptype == "sample":
        n_dist = p.get("n_distinct", "?")
        samples = p.get("sample_values", [])
        sample_str = ", ".join(f'"{ s}"' for s in samples[:5])
        return f"  {col} (text, ~{n_dist:,} distinct): samples: {sample_str}"
    elif ptype == "numeric":
        return (
            f"  {col} (numeric): "
            f"min={p.get('min')}, max={p.get('max')}, "
            f"median={p.get('median')}, mean={p.get('mean')}"
        )
    elif ptype == "date_range":
        return f"  {col} (date): range {p.get('min')} to {p.get('max')}"
    elif ptype == "boolean":
        return (
            f"  {col} (boolean): "
            f"true={p.get('n_true', 0):,}, "
            f"false={p.get('n_false', 0):,}, "
            f"null={p.get('n_null', 0):,}"
        )
    elif ptype == "error":
        return f"  {col}: PROFILE ERROR - {p.get('error', '')}"
    return f"  {col}: unknown profile type '{ptype}'"


def _format_column_profiles_summary() -> str:
    """Format a lightweight summary of available column profiles (table list only)."""
    if not _COLUMN_PROFILES:
        return (
            "Column profiles not available. Run data/generate_column_profiles.py "
            "against your AACT database to generate column_profiles.json.\n"
        )

    lines = [
        "AACT COLUMN VALUE PROFILES — SUMMARY",
        "=" * 50,
        "",
        "Column profiles are available per table. Request specific tables",
        "using aact://column-profiles/{table_name} to get the actual values.",
        "",
    ]

    # Table row counts
    row_counts = _COLUMN_PROFILES.get("table_row_counts", {})
    if row_counts:
        lines.append("TABLE ROW COUNTS AND PROFILED COLUMNS:")
        for tname in sorted(row_counts.keys()):
            cnt = row_counts[tname]
            n_cols = len(_COLUMN_PROFILES_BY_TABLE.get(tname, {}))
            if n_cols > 0:
                lines.append(f"  {tname}: {cnt:,} rows, {n_cols} profiled columns")
            else:
                lines.append(f"  {tname}: {cnt:,} rows")
        lines.append("")

    lines.append(
        "To get column profiles for a specific table, read:\n"
        "  aact://column-profiles/{table_name}\n"
        "Example: aact://column-profiles/studies"
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _format_column_profiles_for_table(table_name: str) -> str:
    """Format column profiles for a single table."""
    if not _COLUMN_PROFILES:
        return (
            "Column profiles not available. Run data/generate_column_profiles.py "
            "against your AACT database to generate column_profiles.json.\n"
        )

    table_profiles = _COLUMN_PROFILES_BY_TABLE.get(table_name)
    if not table_profiles:
        return f"No column profiles available for table '{table_name}'.\n"

    row_counts = _COLUMN_PROFILES.get("table_row_counts", {})
    row_count = row_counts.get(table_name, "unknown")

    lines = [
        f"COLUMN PROFILES FOR: {table_name}",
        f"Row count: {row_count:,}" if isinstance(row_count, int) else f"Row count: {row_count}",
        "-" * 40,
    ]

    for key in sorted(table_profiles.keys()):
        p = table_profiles[key]
        lines.append(_format_column_profile_entry(p))

    lines.append("")
    return "\n".join(lines) + "\n"


def _format_query_patterns() -> str:
    """Format query patterns into a compact, LLM-readable text."""
    if not _QUERY_PATTERNS:
        return "Query patterns not available. No query_patterns.json file found.\n"

    lines = [
        "AACT COMMON SQL QUERY PATTERNS",
        "=" * 50,
        "",
        "Tested SQL templates for common clinical trial questions.",
        "Use these as a starting point and adapt to the user's specific query.",
        "",
    ]

    for i, pattern in enumerate(_QUERY_PATTERNS.get("patterns", []), 1):
        lines.append(f"Pattern {i}: {pattern['intent']}")
        lines.append(f"  When to use: {pattern['when_to_use']}")
        lines.append(f"  Tables: {', '.join(pattern['tables'])}")
        lines.append(f"  SQL:")
        for sql_line in pattern["sql"].split("\n"):
            lines.append(f"    {sql_line}")
        if "notes" in pattern:
            lines.append(f"  Notes: {pattern['notes']}")
        lines.append("")

    # SQL conventions
    conventions = _QUERY_PATTERNS.get("sql_conventions", [])
    if conventions:
        lines.append("SQL CONVENTIONS:")
        for conv in conventions:
            lines.append(f"  - {conv}")
        lines.append("")

    return "\n".join(lines) + "\n"


@mcp.resource(
    "aact://glossary",
    name="AACT Glossary",
    title="Clinical Trial Terminology Glossary",
    description=(
        "Maps clinical trial terminology (endpoints, sites, adverse events, "
        "arms, eligibility, etc.) to the correct AACT database tables and "
        "columns. CRITICAL for translating natural language queries into SQL. "
        "Includes warnings about Protocol vs Results domain tables — e.g. "
        "'endpoints' maps to design_outcomes (Protocol, always populated), "
        "NOT outcomes (Results, only for completed trials). Read this BEFORE "
        "generating SQL to avoid querying the wrong tables."
    ),
    mime_type="text/plain",
)
async def glossary() -> str:
    """Return the clinical trial terminology glossary."""
    return _format_glossary()


@mcp.resource(
    "aact://column-profiles",
    name="AACT Column Profiles Summary",
    title="Column Value Profiles — Summary (lightweight)",
    description=(
        "Lightweight summary listing which tables have column profiles available "
        "and how many profiled columns each has. Use this to discover available "
        "profiles, then read aact://column-profiles/{table_name} for the actual "
        "values of specific tables. Do NOT read this if you already know which "
        "tables you need — go directly to the per-table resources."
    ),
    mime_type="text/plain",
)
async def column_profiles_summary() -> str:
    """Return a lightweight summary of available column profiles."""
    return _format_column_profiles_summary()


@mcp.resource(
    "aact://column-profiles/{table_name}",
    name="AACT Column Profiles (per table)",
    title="Column Value Profiles for a Specific Table",
    description=(
        "Statistical profiles of key columns for a specific AACT table. "
        "Shows actual data values, enumerations (with counts), ranges, and "
        "sample values. Essential for generating correct SQL — tells you the "
        "exact values stored in enum-like columns (e.g. overall_status stores "
        "'RECRUITING' in UPPERCASE), date ranges, numeric ranges, etc. "
        "Request only the tables you need to keep token usage low."
    ),
    mime_type="text/plain",
)
async def column_profiles_for_table(table_name: str) -> str:
    """Return column profiles for a specific table."""
    return _format_column_profiles_for_table(table_name)


@mcp.resource(
    "aact://query-patterns",
    name="AACT Query Patterns",
    title="Common SQL Query Patterns",
    description=(
        "Tested SQL query templates for the most common clinical trial "
        "questions: listing endpoints, finding sites, getting results, "
        "counting trials by status/phase, eligibility criteria, adverse "
        "events, etc. Each pattern includes the correct tables, JOINs, "
        "and SQL conventions. Use as starting points and adapt to the "
        "user's specific query."
    ),
    mime_type="text/plain",
)
async def query_patterns() -> str:
    """Return common SQL query patterns."""
    return _format_query_patterns()


# ---------------------------------------------------------------------------
# Health-check endpoint (HTTP mode only)
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """
    Liveness probe for the CT.Sight backend or any other client.

    Returns a JSON object with server status, transport mode, and schema
    statistics so that clients can verify connectivity before sending queries.
    """
    return JSONResponse({
        "status": "ok",
        "server": "aact-mcp-server",
        "transport": _TRANSPORT,
        "tables": len(_TABLES),
        "foreign_keys": len(_FOREIGN_KEYS),
        "glossary_loaded": bool(_GLOSSARY),
        "column_profiles_loaded": bool(_COLUMN_PROFILES),
        "query_patterns_loaded": bool(_QUERY_PATTERNS),
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
