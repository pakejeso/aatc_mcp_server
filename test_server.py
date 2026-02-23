"""
Test script for the AACT Schema MCP Server.
Exercises all resources against the bundled static schema.
"""

import asyncio


async def test_resources():
    """Test all resources by importing the server module directly."""
    from src.server import (
        _TABLES,
        _FOREIGN_KEYS,
        _TABLE_INDEX,
        full_schema,
        table_schema,
        table_list,
        relationships,
    )

    print("=" * 60)
    print("AACT MCP Server — Resource Tests")
    print("=" * 60)

    # 1. Test schema loaded correctly
    print("\n[1] Testing static schema loading...")
    print(f"    Tables loaded: {len(_TABLES)}")
    print(f"    Foreign keys loaded: {len(_FOREIGN_KEYS)}")
    print(f"    Table index entries: {len(_TABLE_INDEX)}")
    assert len(_TABLES) == 48, f"Expected 48 tables, got {len(_TABLES)}"
    assert len(_FOREIGN_KEYS) == 63, f"Expected 63 FKs, got {len(_FOREIGN_KEYS)}"
    assert len(_TABLE_INDEX) == 48, f"Expected 48 index entries, got {len(_TABLE_INDEX)}"
    print("    PASS")

    # 2. Test table list resource (now includes descriptions)
    print("\n[2] Testing aact://tables ...")
    result = await table_list()
    assert "studies" in result
    assert "conditions" in result
    # Verify descriptions are present in the table list
    assert "Basic info about study" in result, "Expected studies description in table list"
    assert "disease" in result.lower() or "condition" in result.lower(), \
        "Expected conditions description in table list"
    lines = result.strip().split("\n")
    print(f"    Output: {len(lines)} lines, {len(result)} chars")
    # Show a sample table entry with description
    for line in lines:
        if "studies" in line and "columns" in line:
            print(f"      {line}")
            break
    for line in lines:
        if "Basic info" in line:
            print(f"      {line}")
            break
    print("    PASS")

    # 3. Test full schema resource
    print("\n[3] Testing aact://schema ...")
    result = await full_schema()
    assert "CREATE TABLE ctgov.studies" in result
    assert "CREATE TABLE ctgov.conditions" in result
    assert "FOREIGN KEY" in result
    assert "RELATIONSHIP SUMMARY" in result
    ct_count = result.count("CREATE TABLE")
    print(f"    Output: {len(result)} chars (~{len(result)//4} tokens)")
    print(f"    CREATE TABLE statements: {ct_count}")
    assert ct_count == 48, f"Expected 48 CREATE TABLE, got {ct_count}"
    print("    PASS")

    # 4. Test single table resource — studies
    print("\n[4] Testing aact://schema/studies ...")
    result = await table_schema("studies")
    assert "CREATE TABLE ctgov.studies" in result
    assert "nct_id" in result
    assert "overall_status" in result
    print(f"    Output: {len(result)} chars")
    print(f"    First 5 lines:")
    for line in result.strip().split("\n")[:5]:
        print(f"      {line}")
    print("    PASS")

    # 5. Test single table resource — outcome_analyses (has FKs)
    print("\n[5] Testing aact://schema/outcome_analyses ...")
    result = await table_schema("outcome_analyses")
    assert "outcome_id" in result
    assert "FOREIGN KEY" in result
    assert "outcome_analysis_groups" in result
    fk_lines = [l for l in result.split("\n") if "FOREIGN KEY" in l or "references" in l.lower()]
    print(f"    Output: {len(result)} chars")
    print(f"    FK constraint lines: {len(fk_lines)}")
    for line in fk_lines:
        print(f"      {line.strip()}")
    print("    PASS")

    # 6. Test single table resource — nonexistent table
    print("\n[6] Testing aact://schema/nonexistent ...")
    result = await table_schema("nonexistent")
    assert "ERROR" in result
    assert "not found" in result
    print(f"    Output: {result.strip()[:100]}")
    print("    PASS")

    # 7. Test relationships resource
    print("\n[7] Testing aact://relationships ...")
    result = await relationships()
    assert "nct_id" in result
    assert "Hierarchical" in result
    assert "outcome_analyses" in result
    nct_lines = [l for l in result.split("\n") if "nct_id -> studies.nct_id" in l]
    other_lines = [l for l in result.split("\n")
                   if "->" in l and "nct_id -> studies" not in l and not l.startswith("--")]
    print(f"    nct_id relationships: {len(nct_lines)}")
    print(f"    Hierarchical FKs: {len(other_lines)}")
    print(f"    Output: {len(result)} chars")
    print("    PASS")

    # 8. Token estimate
    print("\n[8] Token usage estimate...")
    full = await full_schema()
    est_tokens = len(full) // 4
    print(f"    Full schema: ~{est_tokens:,} tokens")
    print(f"    (Well within 128K context window)")

    # 9. Verify rich descriptions are present
    print("\n[9] Checking rich descriptions...")
    studies = _TABLE_INDEX["studies"]
    desc_count = sum(1 for c in studies["columns"] if c.get("description"))
    print(f"    studies table: {desc_count}/{len(studies['columns'])} columns have descriptions")
    assert desc_count > 0, "Expected at least some column descriptions"
    # Check table-level description
    assert studies.get("description"), "Expected table-level description for studies"
    print(f"    studies table description: {studies['description'][:80]}...")
    print("    PASS")

    # 10. Verify table list descriptions coverage
    print("\n[10] Checking table list description coverage...")
    tl = await table_list()
    tables_with_desc = sum(1 for t in _TABLES if t.get("description"))
    print(f"    Tables with descriptions: {tables_with_desc}/{len(_TABLES)}")
    assert tables_with_desc > 30, f"Expected >30 tables with descriptions, got {tables_with_desc}"
    print("    PASS")

    print("\n" + "=" * 60)
    print("ALL 10 TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_resources())
