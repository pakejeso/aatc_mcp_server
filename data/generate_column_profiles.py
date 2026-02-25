#!/usr/bin/env python3
"""
AACT Column Profiler — Generate column_profiles.json
=====================================================
Run this script ONCE against your live AACT PostgreSQL database to produce
a token-efficient statistical profile of every key column.

The output file (column_profiles.json) is consumed by the AACT MCP Server
to expose the ``aact://column-profiles`` resource, giving LLMs the data
awareness they need to generate correct SQL (right values, right case,
right tables).

Usage:
    export AACT_DATABASE_URL="postgresql://user:pass@host:5432/aact"
    python generate_column_profiles.py

    # Or pass the URL as an argument:
    python generate_column_profiles.py "postgresql://user:pass@host:5432/aact"

    # Windows PowerShell:
    $env:AACT_DATABASE_URL = "postgresql://user:pass@host:5432/aact"
    python generate_column_profiles.py

Output:
    data/column_profiles.json  (written next to this script)

Profiling strategy (token-efficient):
    - Enum-like columns (≤50 distinct values):  full list of distinct values + counts
    - High-cardinality varchar/text columns:     n_distinct, 5 random sample values
    - Numeric columns:                           min, max, median, mean, n_nulls
    - Date columns:                              min, max, n_nulls
    - Boolean columns:                           count of true/false/null
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Tables and columns to profile.  We focus on the columns that matter most
# for SQL generation — the ones the LLM needs to know values/case for.
# Format:  (table_name, column_name, profile_type)
#   profile_type:
#     "enum"     → always get full distinct values (for known low-cardinality columns)
#     "auto"     → check cardinality first; if ≤50 → enum, else → sample
#     "numeric"  → min/max/median/mean
#     "date"     → min/max
#     "boolean"  → true/false/null counts
#     "text"     → n_distinct + 5 random samples
#     "skip"     → do not profile (e.g. nct_id, free-text descriptions)

COLUMNS_TO_PROFILE = [
    # --- studies (the central table) ---
    ("studies", "overall_status", "enum"),
    ("studies", "phase", "enum"),
    ("studies", "study_type", "enum"),
    ("studies", "enrollment_type", "enum"),
    ("studies", "source_class", "enum"),
    ("studies", "last_known_status", "enum"),
    ("studies", "biospec_retention", "enum"),
    ("studies", "plan_to_share_ipd", "enum"),
    ("studies", "enrollment", "numeric"),
    ("studies", "number_of_arms", "numeric"),
    ("studies", "number_of_groups", "numeric"),
    ("studies", "is_fda_regulated_drug", "boolean"),
    ("studies", "is_fda_regulated_device", "boolean"),
    ("studies", "has_dmc", "boolean"),
    ("studies", "has_expanded_access", "boolean"),
    ("studies", "fdaaa801_violation", "boolean"),
    ("studies", "start_date", "date"),
    ("studies", "completion_date", "date"),
    ("studies", "primary_completion_date", "date"),
    ("studies", "study_first_submitted_date", "date"),
    ("studies", "results_first_submitted_date", "date"),

    # --- conditions ---
    ("conditions", "downcase_name", "text"),

    # --- interventions ---
    ("interventions", "intervention_type", "enum"),
    ("interventions", "name", "text"),

    # --- design_outcomes (endpoints) ---
    ("design_outcomes", "outcome_type", "enum"),
    ("design_outcomes", "measure", "text"),

    # --- outcomes (results) ---
    ("outcomes", "outcome_type", "enum"),
    ("outcomes", "param_type", "enum"),
    ("outcomes", "dispersion_type", "enum"),
    ("outcomes", "units", "auto"),

    # --- outcome_measurements ---
    ("outcome_measurements", "param_type", "enum"),
    ("outcome_measurements", "dispersion_type", "enum"),
    ("outcome_measurements", "category", "auto"),

    # --- outcome_analyses ---
    ("outcome_analyses", "param_type", "enum"),
    ("outcome_analyses", "dispersion_type", "enum"),
    ("outcome_analyses", "method", "auto"),
    ("outcome_analyses", "non_inferiority_type", "enum"),

    # --- designs ---
    ("designs", "allocation", "enum"),
    ("designs", "intervention_model", "enum"),
    ("designs", "observational_model", "enum"),
    ("designs", "primary_purpose", "enum"),
    ("designs", "time_perspective", "enum"),
    ("designs", "masking", "enum"),

    # --- eligibilities ---
    ("eligibilities", "gender", "enum"),
    ("eligibilities", "sampling_method", "enum"),
    ("eligibilities", "minimum_age", "text"),
    ("eligibilities", "maximum_age", "text"),

    # --- sponsors ---
    ("sponsors", "agency_class", "enum"),
    ("sponsors", "lead_or_collaborator", "enum"),

    # --- facilities ---
    ("facilities", "status", "enum"),
    ("facilities", "country", "auto"),
    ("facilities", "city", "text"),

    # --- design_groups ---
    ("design_groups", "group_type", "enum"),

    # --- reported_events ---
    ("reported_events", "event_type", "enum"),
    ("reported_events", "assessment", "auto"),

    # --- countries ---
    ("countries", "name", "auto"),
    ("countries", "removed", "boolean"),

    # --- documents ---
    ("documents", "document_type", "auto"),

    # --- browse_conditions ---
    ("browse_conditions", "mesh_type", "enum"),

    # --- browse_interventions ---
    ("browse_interventions", "mesh_type", "enum"),

    # --- baseline_measurements ---
    ("baseline_measurements", "param_type", "enum"),
    ("baseline_measurements", "dispersion_type", "enum"),

    # --- result_groups ---
    ("result_groups", "result_type", "enum"),

    # --- id_information ---
    ("id_information", "id_type", "enum"),
]


# ---------------------------------------------------------------------------
# Profiling functions
# ---------------------------------------------------------------------------

def profile_enum(cur, table: str, col: str) -> dict:
    """Get all distinct values with counts (for low-cardinality columns).
    
    Safety: if the column has >50 distinct values, auto-downgrade to sample
    mode to avoid bloating the JSON output.
    """
    cur.execute(f"""
        SELECT {col}, COUNT(*) as cnt
        FROM ctgov.{table}
        WHERE {col} IS NOT NULL
        GROUP BY {col}
        ORDER BY cnt DESC
    """)
    rows = cur.fetchall()
    null_count_q = f"SELECT COUNT(*) FROM ctgov.{table} WHERE {col} IS NULL"
    cur.execute(null_count_q)
    n_null = cur.fetchone()[0]
    
    # Safety cap: if >50 distinct values, this is not really an enum
    if len(rows) > 50:
        return {
            "profile_type": "sample",
            "n_distinct": len(rows),
            "n_null": n_null,
            "sample_values": [str(r[0])[:120] for r in rows[:5]],
        }
    
    return {
        "profile_type": "enum",
        "n_distinct": len(rows),
        "n_null": n_null,
        "values": {str(r[0]): r[1] for r in rows},
    }


def profile_auto(cur, table: str, col: str) -> dict:
    """Check cardinality; if ≤50 use enum, else use text/sample."""
    cur.execute(f"""
        SELECT COUNT(DISTINCT {col})
        FROM ctgov.{table}
        WHERE {col} IS NOT NULL
    """)
    n_distinct = cur.fetchone()[0]
    if n_distinct <= 50:
        return profile_enum(cur, table, col)
    else:
        return profile_text(cur, table, col)


def profile_text(cur, table: str, col: str) -> dict:
    """Get n_distinct + 5 random sample values."""
    cur.execute(f"""
        SELECT COUNT(DISTINCT {col})
        FROM ctgov.{table}
        WHERE {col} IS NOT NULL
    """)
    n_distinct = cur.fetchone()[0]

    cur.execute(f"""
        SELECT COUNT(*)
        FROM ctgov.{table}
        WHERE {col} IS NULL
    """)
    n_null = cur.fetchone()[0]

    # Use a subquery to get random samples — compatible with PostgreSQL
    # (SELECT DISTINCT ... ORDER BY RANDOM() is not allowed in PostgreSQL)
    cur.execute(f"""
        SELECT {col}
        FROM (
            SELECT DISTINCT {col}
            FROM ctgov.{table}
            WHERE {col} IS NOT NULL
        ) sub
        ORDER BY RANDOM()
        LIMIT 5
    """)
    samples = [str(r[0])[:120] for r in cur.fetchall()]

    return {
        "profile_type": "sample",
        "n_distinct": n_distinct,
        "n_null": n_null,
        "sample_values": samples,
    }


def profile_numeric(cur, table: str, col: str) -> dict:
    """Get min, max, median, mean, null count."""
    cur.execute(f"""
        SELECT
            MIN({col}),
            MAX({col}),
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {col}),
            ROUND(AVG({col})::numeric, 2),
            COUNT(*) FILTER (WHERE {col} IS NULL),
            COUNT(*) FILTER (WHERE {col} IS NOT NULL)
        FROM ctgov.{table}
    """)
    row = cur.fetchone()
    return {
        "profile_type": "numeric",
        "min": float(row[0]) if row[0] is not None else None,
        "max": float(row[1]) if row[1] is not None else None,
        "median": float(row[2]) if row[2] is not None else None,
        "mean": float(row[3]) if row[3] is not None else None,
        "n_null": row[4],
        "n_non_null": row[5],
    }


def profile_date(cur, table: str, col: str) -> dict:
    """Get min, max date and null count."""
    cur.execute(f"""
        SELECT
            MIN({col})::text,
            MAX({col})::text,
            COUNT(*) FILTER (WHERE {col} IS NULL),
            COUNT(*) FILTER (WHERE {col} IS NOT NULL)
        FROM ctgov.{table}
    """)
    row = cur.fetchone()
    return {
        "profile_type": "date_range",
        "min": row[0],
        "max": row[1],
        "n_null": row[2],
        "n_non_null": row[3],
    }


def profile_boolean(cur, table: str, col: str) -> dict:
    """Get true/false/null counts."""
    cur.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE {col} = TRUE),
            COUNT(*) FILTER (WHERE {col} = FALSE),
            COUNT(*) FILTER (WHERE {col} IS NULL)
        FROM ctgov.{table}
    """)
    row = cur.fetchone()
    return {
        "profile_type": "boolean",
        "n_true": row[0],
        "n_false": row[1],
        "n_null": row[2],
    }


PROFILE_FUNCTIONS = {
    "enum": profile_enum,
    "auto": profile_auto,
    "text": profile_text,
    "numeric": profile_numeric,
    "date": profile_date,
    "boolean": profile_boolean,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    db_url = (
        sys.argv[1] if len(sys.argv) > 1
        else os.environ.get("AACT_DATABASE_URL", "")
    )
    if not db_url:
        print("ERROR: Provide AACT_DATABASE_URL as env var or first argument.")
        print("Usage: python generate_column_profiles.py 'postgresql://...'")
        sys.exit(1)

    print(f"Connecting to database...")
    conn = psycopg2.connect(db_url)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    # Get total row counts per table (for context)
    table_names = sorted(set(t for t, _, _ in COLUMNS_TO_PROFILE))
    table_row_counts = {}
    for tname in table_names:
        cur.execute(f"SELECT COUNT(*) FROM ctgov.{tname}")
        table_row_counts[tname] = cur.fetchone()[0]
        print(f"  {tname}: {table_row_counts[tname]:,} rows")

    # Profile each column
    profiles = {}
    n_ok = 0
    n_fail = 0
    for table, col, ptype in COLUMNS_TO_PROFILE:
        key = f"{table}.{col}"
        print(f"  Profiling {key} ({ptype})...", end=" ", flush=True)
        try:
            fn = PROFILE_FUNCTIONS[ptype]
            result = fn(cur, table, col)
            result["table"] = table
            result["column"] = col
            profiles[key] = result
            print("OK")
            n_ok += 1
        except Exception as exc:
            print(f"FAILED: {exc}")
            profiles[key] = {
                "table": table,
                "column": col,
                "profile_type": "error",
                "error": str(exc),
            }
            n_fail += 1

    cur.close()
    conn.close()

    # Write output
    output = {
        "_generated_by": "generate_column_profiles.py",
        "_description": (
            "Statistical profiles of key AACT columns. Used by the MCP server "
            "to expose the aact://column-profiles resource. Regenerate by "
            "running this script against the live AACT database."
        ),
        "table_row_counts": table_row_counts,
        "profiles": profiles,
    }

    out_path = Path(__file__).parent / "column_profiles.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nDone! Written to {out_path}")
    print(f"Profiled {len(profiles)} columns across {len(table_names)} tables.")
    print(f"  OK: {n_ok}  |  Failed: {n_fail}")
    if n_fail > 0:
        print("  (Failed columns are recorded as 'error' entries in the JSON)")


if __name__ == "__main__":
    main()
