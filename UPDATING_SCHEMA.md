# Guide: Updating the AACT Static Schema

This document explains how to regenerate the `data/aact_schema_static.json` file when the AACT database schema changes. The goal is to create a comprehensive JSON snapshot that includes not just the raw database structure (tables, columns, types, keys) but also **rich descriptions** sourced from official AACT documentation.

**The process is semi-automated and requires an LLM with advanced reasoning capabilities.** It is not a simple script because the rich descriptions are not stored in the database itself; they must be intelligently extracted and mapped from external sources.

## Why is this a semi-automated process?

The core challenge is that the three main sources of schema information are disconnected and have different formats:

| Source | What it provides | Format | Location |
| :--- | :--- | :--- | :--- |
| **PostgreSQL `information_schema`** | Tables, columns, data types, PKs, FKs | SQL query results | Your AACT database instance |
| **AACT Data Dictionary** | Rich, human-readable descriptions of each table and column | HTML web page | [aact.ctti-clinicaltrials.org/data_dictionary](https://aact.ctti-clinicaltrials.org/data_dictionary) |
| **AACT `schema.rb`** | The canonical source of truth for table structure and foreign keys | Ruby on Rails schema file | [github.com/ctti-clinicaltrials/aact](https://github.com/ctti-clinicaltrials/aact) |

No single source has all the information. A human or an advanced AI is needed to **synthesize** these sources: mapping the descriptions from the data dictionary to the correct tables and columns defined in `schema.rb` or the live database.

## The LLM-Powered Workflow

Here is the step-by-step workflow to regenerate the schema. This is the exact process that was used to create the current `aact_schema_static.json` file.

### Step 1: Gather Raw Schema from `schema.rb`

The `schema.rb` file from the official AACT GitHub repository is the most reliable source for the database structure.

**Action:**
1.  Clone the official AACT repo: `git clone https://github.com/ctti-clinicaltrials/aact.git`
2.  Locate the `db/schema.rb` file.
3.  Provide this file to an LLM with the following prompt:

> "Parse this Ruby on Rails `schema.rb` file. Extract all table definitions, including table names, column names, and PostgreSQL data types. Also, parse all `add_foreign_key` statements to identify all foreign key relationships. Output this as a preliminary JSON structure."

### Step 2: Gather Rich Descriptions from the Data Dictionary

The AACT website hosts a data dictionary with detailed descriptions for most tables and columns.

**Action:**
1.  Visit the [AACT Data Dictionary](https://aact.ctti-clinicaltrials.org/data_dictionary).
2.  The data is presented in a series of HTML tables. You may need to scroll or paginate to see all of them.
3.  Provide the full HTML source of this page to the LLM with the following prompt:

> "Parse this HTML. It contains a data dictionary for the AACT database. Extract all table definitions, including the table name, table description, and a list of all columns with their corresponding descriptions. The data is in multiple `<table>` elements. Output this as a second JSON structure, mapping table names to their metadata."

### Step 3: Synthesize and Merge the Data

This is the most critical step, where the LLM's reasoning is required.

**Action:**
1.  Provide the two JSON files generated in Steps 1 and 2 to the LLM.
2.  Use the following prompt:

> "You have two JSON files.
> - The first contains the raw database schema (tables, columns, types, FKs) extracted from `schema.rb`.
> - The second contains rich descriptions for tables and columns extracted from an HTML data dictionary.
>
> Your task is to **merge** these two sources into a single, comprehensive JSON file (`aact_schema_static.json`).
>
> **Instructions:**
> 1.  Iterate through each table in the raw schema from Step 1.
> 2.  For each table, find the corresponding table in the data dictionary from Step 2.
> 3.  Add the `description`, `domain`, and `rows_per_study` from the dictionary to the table object.
> 4.  For each column in the table, find its corresponding description in the dictionary data and add it to the column object.
> 5.  Ensure all foreign keys from Step 1 are preserved in a top-level `foreign_keys` array.
> 6.  The final output should be a single JSON object with two keys: `tables` and `foreign_keys`.
> 7.  Pay close attention to mapping, as table or column names might have slight variations between the sources. Use fuzzy matching or logical inference where necessary."

### Step 4: Final Review and Formatting

The LLM should now produce a final `aact_schema_static.json` file. Before using it, perform a quick manual review to ensure the merge was successful and the structure is correct.

**Action:**
1.  Inspect the generated JSON file.
2.  Check that a few key tables (e.g., `studies`, `conditions`, `outcomes`) have both their columns and their descriptions.
3.  Verify that the `foreign_keys` array is present and contains the expected relationships.
4.  Replace the old `data/aact_schema_static.json` with this new file.

This semi-automated, LLM-driven process ensures that the static schema remains not only structurally accurate but also enriched with the high-quality, human-readable context necessary for the LLM to generate the best possible SQL queries.
