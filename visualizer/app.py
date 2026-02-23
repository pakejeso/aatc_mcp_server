"""
MCP Flow Visualizer — Backend Proxy

A lightweight Flask app that:
1. Serves the interactive MCP architecture demo (index.html)
2. Proxies LLM calls so the API key stays server-side

Configuration via environment variables:
  OPENAI_API_KEY   — Required. Your OpenAI (or compatible) API key.
  OPENAI_BASE_URL  — Optional. Override for OpenAI-compatible endpoints.
  LLM_MODEL        — Optional. Model name (default: gpt-4.1-mini).
  PORT             — Optional. Server port (default: 8090).
"""

import os
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", 8090))
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=".")
CORS(app)

client = OpenAI()  # reads OPENAI_API_KEY and OPENAI_BASE_URL from env

# Load the AACT schema text (used as LLM context)
schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_compact.txt")
with open(schema_path) as f:
    SCHEMA_TEXT = f.read()
logger.info("Loaded AACT schema: %d lines, %d chars", SCHEMA_TEXT.count("\n"), len(SCHEMA_TEXT))

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
def generate_sql():
    """
    Accept a natural-language query (any language) and return a PostgreSQL
    SELECT statement for the AACT database.
    """
    data = request.json or {}
    user_query = data.get("query", "").strip()

    if not user_query:
        return jsonify({"error": "No query provided"}), 400

    system_prompt = f"""You are a SQL expert for the AACT clinical trials database (PostgreSQL).
The database schema is 'ctgov'. Here is the complete schema:

{SCHEMA_TEXT}

Given a user's natural language request (which may be in any language), generate a valid PostgreSQL SELECT query.
Rules:
- Always use the ctgov schema prefix (e.g., ctgov.studies)
- Use appropriate JOINs based on the foreign keys shown
- Most tables join to studies via nct_id
- Return ONLY the SQL query, no explanation
- Make the query practical and correct"""

    try:
        logger.info("Generating SQL for: %s", user_query[:80])
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

        # Strip markdown code fences if present
        if sql.startswith("```"):
            sql = "\n".join(sql.split("\n")[1:])
        if sql.endswith("```"):
            sql = sql[:-3].strip()

        logger.info("SQL generated successfully (%d chars)", len(sql))
        return jsonify({"sql": sql})

    except Exception as e:
        logger.error("LLM call failed: %s", str(e))
        return jsonify({"error": str(e)}), 502


# ---------------------------------------------------------------------------
# Entry point (development server)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
