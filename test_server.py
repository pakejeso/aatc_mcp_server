"""
Test script for the AACT Schema MCP Server.
Exercises all resources using the static fallback (no DB required).
"""

import asyncio
import sys
import os

# Ensure no DB env vars are set so we use static fallback
for key in ["AACT_DB_HOST", "AACT_DB_USER", "AACT_DB_PASS", "AACT_DB_NAME"]:
    os.environ.pop(key, None)


async def test_resources():
    """Test all resources by importing the server module directly."""
    # Import after clearing env vars
    from src.server import (
        get_provider,
        format_full_schema,
        format_table_ddl,
        full_schema,
        table_schema,
        table_list,
        relationships,
    )

    print("=" * 60)
    print("AACT MCP Server — Resource Tests (Static Fallback)")
    print("=" * 60)

    # 1. Test provider initialization
    print("\n[1] Testing provider initialization...")
    provider = await get_provider()
    print(f"    Provider type: {type(provider).__name__}")
    names = await provider.get_table_names()
    print(f"    Tables found: {len(names)}")
    assert len(names) == 48, f"Expected 48 tables, got {len(names)}"
    print("    ✓ PASS")

    # 2. Test table list resource
    print("\n[2] Testing postgres://aact/tables ...")
    result = await table_list()
    assert "studies" in result
    assert "conditions" in result
    lines = result.strip().split("\n")
    print(f"    Output: {len(lines)} lines, {len(result)} chars")
    print(f"    First 3 lines:")
    for line in lines[:3]:
        print(f"      {line}")
    print("    ✓ PASS")

    # 3. Test full schema resource
    print("\n[3] Testing postgres://aact/schema ...")
    result = await full_schema()
    assert "CREATE TABLE ctgov.studies" in result
    assert "CREATE TABLE ctgov.conditions" in result
    assert "FOREIGN KEY" in result
    assert "RELATIONSHIP SUMMARY" in result
    print(f"    Output: {len(result)} chars (~{len(result)//4} tokens)")
    # Count CREATE TABLE statements
    ct_count = result.count("CREATE TABLE")
    print(f"    CREATE TABLE statements: {ct_count}")
    assert ct_count == 48, f"Expected 48 CREATE TABLE, got {ct_count}"
    print("    ✓ PASS")

    # 4. Test single table resource — studies
    print("\n[4] Testing postgres://aact/schema/studies ...")
    result = await table_schema("studies")
    assert "CREATE TABLE ctgov.studies" in result
    assert "nct_id" in result
    assert "overall_status" in result
    print(f"    Output: {len(result)} chars")
    print(f"    First 5 lines:")
    for line in result.strip().split("\n")[:5]:
        print(f"      {line}")
    print("    ✓ PASS")

    # 5. Test single table resource — outcome_analyses (has FKs)
    print("\n[5] Testing postgres://aact/schema/outcome_analyses ...")
    result = await table_schema("outcome_analyses")
    assert "outcome_id" in result
    assert "FOREIGN KEY" in result
    assert "outcome_analysis_groups" in result  # child table reference
    print(f"    Output: {len(result)} chars")
    # Check FK lines
    fk_lines = [l for l in result.split("\n") if "FOREIGN KEY" in l or "references" in l.lower()]
    print(f"    FK constraint lines: {len(fk_lines)}")
    for line in fk_lines:
        print(f"      {line.strip()}")
    print("    ✓ PASS")

    # 6. Test single table resource — nonexistent table
    print("\n[6] Testing postgres://aact/schema/nonexistent ...")
    result = await table_schema("nonexistent")
    assert "ERROR" in result
    assert "not found" in result
    print(f"    Output: {result.strip()[:100]}")
    print("    ✓ PASS")

    # 7. Test relationships resource
    print("\n[7] Testing postgres://aact/relationships ...")
    result = await relationships()
    assert "nct_id" in result
    assert "Hierarchical" in result
    assert "outcome_analyses" in result
    nct_lines = [l for l in result.split("\n") if "nct_id -> studies.nct_id" in l]
    other_lines = [l for l in result.split("\n") if "->" in l and "nct_id -> studies" not in l and not l.startswith("--")]
    print(f"    nct_id relationships: {len(nct_lines)}")
    print(f"    Hierarchical FKs: {len(other_lines)}")
    print(f"    Output: {len(result)} chars")
    print("    ✓ PASS")

    # 8. Token estimate for full schema
    print("\n[8] Token usage estimate...")
    full = await full_schema()
    est_tokens = len(full) // 4  # rough estimate
    print(f"    Full schema: ~{est_tokens:,} tokens")
    print(f"    (Well within 128K context window)")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✓")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_resources())
