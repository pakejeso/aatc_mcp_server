"""
MCP Flow Visualizer — Backend
=============================
A Flask app that:
1. Serves the interactive MCP architecture demo (index.html)
2. Orchestrates the REAL MCP server via stdio (JSON-RPC)
3. Follows the efficient 3-step resource reading strategy
4. Calls the LLM with only the relevant schema tables
5. Returns every MCP message exchanged for the frontend to display

Configuration via environment variables:
  OPENAI_API_KEY   — Required. Your OpenAI (or compatible) API key.
  OPENAI_BASE_URL  — Optional. Override for OpenAI-compatible endpoints.
  LLM_MODEL        — Optional. Model name (default: gpt-4.1-mini).
  PORT             — Optional. Server port (default: 8090).
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", 8090))
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Path to the MCP server entry point (sibling directory)
MCP_SERVER_DIR = Path(__file__).resolve().parent.parent
MCP_SERVER_CMD = [sys.executable, "-m", "src"]

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=".")
CORS(app)

client = OpenAI()  # reads OPENAI_API_KEY and OPENAI_BASE_URL from env


# ---------------------------------------------------------------------------
# MCP Client — talks to the real MCP server via stdio
# ---------------------------------------------------------------------------
class MCPClient:
    """
    Minimal MCP client that spawns the MCP server as a subprocess and
    communicates via JSON-RPC over stdin/stdout.
    """

    def __init__(self):
        self._msg_id = 0

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _run_session(self, messages: list[dict]) -> list[dict]:
        """
        Spawn the MCP server, send a sequence of JSON-RPC messages,
        and collect all responses.
        """
        input_text = "\n".join(json.dumps(m) for m in messages) + "\n"

        try:
            result = subprocess.run(
                MCP_SERVER_CMD,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(MCP_SERVER_DIR),
            )
        except subprocess.TimeoutExpired:
            logger.error("MCP server subprocess timed out")
            return []

        responses = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        return responses

    def run_full_flow(self, relevant_tables: list[str]) -> dict:
        """
        Execute the complete MCP flow:
          1. initialize
          2. resources/list
          3. resources/read  aact://tables
          4. resources/read  aact://schema/{table}  (for each relevant table)
          5. resources/read  aact://relationships

        Returns a dict with all requests, responses, and extracted data.
        """
        self._msg_id = 0
        flow = {"steps": []}

        # Build all messages for a single session
        messages = []

        # 1. Initialize
        init_req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "aact-visualizer", "version": "1.0"},
            },
        }
        messages.append(init_req)

        # Notification: initialized
        messages.append(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        # 2. resources/list
        list_req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "resources/list",
            "params": {},
        }
        messages.append(list_req)

        # 3. Read table list
        tables_req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "resources/read",
            "params": {"uri": "aact://tables"},
        }
        messages.append(tables_req)

        # 4. Read each relevant table schema
        table_reqs = []
        for tbl in relevant_tables:
            req = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "resources/read",
                "params": {"uri": f"aact://schema/{tbl}"},
            }
            messages.append(req)
            table_reqs.append(req)

        # 5. Read relationships
        rels_req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "resources/read",
            "params": {"uri": "aact://relationships"},
        }
        messages.append(rels_req)

        # Run the session
        responses = self._run_session(messages)

        # Build a response lookup by id
        resp_by_id = {r.get("id"): r for r in responses if "id" in r}

        # Collect flow steps
        # Step: initialize
        init_resp = resp_by_id.get(init_req["id"], {})
        flow["steps"].append({
            "label": "initialize",
            "request": init_req,
            "response": init_resp,
            "instructions": init_resp.get("result", {}).get("instructions", ""),
        })

        # Step: resources/list
        list_resp = resp_by_id.get(list_req["id"], {})
        flow["steps"].append({
            "label": "resources/list",
            "request": list_req,
            "response": list_resp,
        })

        # Step: read aact://tables
        tables_resp = resp_by_id.get(tables_req["id"], {})
        tables_text = ""
        if tables_resp.get("result", {}).get("contents"):
            tables_text = tables_resp["result"]["contents"][0].get("text", "")
        flow["steps"].append({
            "label": "resources/read (aact://tables)",
            "request": tables_req,
            "response": tables_resp,
            "text": tables_text,
        })

        # Steps: read each table schema
        schema_texts = []
        for treq in table_reqs:
            tresp = resp_by_id.get(treq["id"], {})
            ttext = ""
            if tresp.get("result", {}).get("contents"):
                ttext = tresp["result"]["contents"][0].get("text", "")
            schema_texts.append(ttext)
            flow["steps"].append({
                "label": f"resources/read ({treq['params']['uri']})",
                "request": treq,
                "response": tresp,
                "text": ttext,
            })

        # Step: read relationships
        rels_resp = resp_by_id.get(rels_req["id"], {})
        rels_text = ""
        if rels_resp.get("result", {}).get("contents"):
            rels_text = rels_resp["result"]["contents"][0].get("text", "")
        flow["steps"].append({
            "label": "resources/read (aact://relationships)",
            "request": rels_req,
            "response": rels_resp,
            "text": rels_text,
        })

        # Combine schema for LLM
        flow["combined_schema"] = "\n\n".join(
            t for t in schema_texts if t
        )
        flow["relationships"] = rels_text
        flow["tables_list"] = tables_text
        flow["relevant_tables"] = relevant_tables

        return flow


mcp_client = MCPClient()


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def identify_relevant_tables(user_query: str, tables_text: str) -> list[str]:
    """
    Use the LLM to identify which AACT tables are relevant for the query.
    Returns a list of table names.
    """
    prompt = f"""You are an expert on the AACT clinical trials database.
Given a user's natural language query and the list of available tables,
identify which tables are needed to answer the query.

RULES:
- Always include 'studies' — it's the central table.
- Only include tables that are genuinely needed for the query.
- Return ONLY a JSON array of table names, nothing else.
- Example: ["studies", "conditions", "interventions"]

USER QUERY: {user_query}

AVAILABLE TABLES:
{tables_text}"""

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.0,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        tables = json.loads(raw)
        if isinstance(tables, list) and all(isinstance(t, str) for t in tables):
            return tables
    except Exception as e:
        logger.warning("Table identification failed: %s", e)

    # Fallback: common tables
    return ["studies", "conditions", "interventions", "sponsors", "eligibilities"]


def generate_sql(user_query: str, schema_text: str, rels_text: str) -> str:
    """
    Use the LLM to generate a PostgreSQL SELECT query.
    """
    system_prompt = f"""You are a SQL expert for the AACT clinical trials database (PostgreSQL).
The database schema is 'ctgov'. Here are the relevant table definitions:

{schema_text}

And the foreign key relationships:
{rels_text}

Given a user's natural language request (which may be in any language),
generate a valid PostgreSQL SELECT query.
Rules:
- Always use the ctgov schema prefix (e.g., ctgov.studies)
- Use appropriate JOINs based on the foreign keys shown
- Most tables join to studies via nct_id
- Return ONLY the SQL query, no explanation
- Make the query practical and correct"""

    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        max_tokens=500,
        temperature=0.1,
    )

    sql = resp.choices[0].message.content.strip()
    if sql.startswith("```"):
        sql = "\n".join(sql.split("\n")[1:])
    if sql.endswith("```"):
        sql = sql[:-3].strip()
    return sql


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the single-page visualizer."""
    return send_from_directory(".", "index.html")


@app.route("/health")
def health():
    """Health check for Docker / load balancers."""
    return jsonify({"status": "ok", "model": LLM_MODEL})


@app.route("/api/generate-sql", methods=["POST"])
def api_generate_sql():
    """
    Full pipeline:
    1. Use LLM to identify relevant tables from user query
    2. Call real MCP server to get table list, specific schemas, relationships
    3. Use LLM to generate SQL from the targeted schema
    4. Return SQL + all MCP messages for the frontend to display
    """
    data = request.json or {}
    user_query = data.get("query", "").strip()

    if not user_query:
        return jsonify({"error": "No query provided"}), 400

    try:
        logger.info("=== New query: %s", user_query[:80])

        # Step A: Quick MCP call to get the table list
        # (We do a minimal session just for the table list)
        logger.info("Step A: Getting table list from MCP server...")
        quick_flow = mcp_client.run_full_flow([])  # no table schemas yet
        tables_text = quick_flow.get("tables_list", "")

        # Step B: Ask LLM which tables are relevant
        logger.info("Step B: Identifying relevant tables...")
        relevant_tables = identify_relevant_tables(user_query, tables_text)
        logger.info("Relevant tables: %s", relevant_tables)

        # Step C: Full MCP session with the relevant tables
        logger.info("Step C: Reading schemas for %d tables...", len(relevant_tables))
        full_flow = mcp_client.run_full_flow(relevant_tables)

        # Step D: Generate SQL using only the relevant schema
        logger.info("Step D: Generating SQL...")
        sql = generate_sql(
            user_query,
            full_flow["combined_schema"],
            full_flow["relationships"],
        )
        logger.info("SQL generated: %d chars", len(sql))

        # Build response with all MCP messages for the frontend
        return jsonify({
            "sql": sql,
            "relevant_tables": relevant_tables,
            "mcp_flow": full_flow["steps"],
            "schema_tokens": len(full_flow["combined_schema"]) // 4,
            "full_schema_tokens": 10611,  # known full schema size
        })

    except Exception as e:
        logger.error("Pipeline failed: %s", str(e), exc_info=True)
        return jsonify({"error": str(e)}), 502


# ---------------------------------------------------------------------------
# Entry point (development server)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
