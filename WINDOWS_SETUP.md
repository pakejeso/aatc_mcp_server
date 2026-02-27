# Running the AACT MCP Server on Windows (for CT.Sight)

This guide explains how to run the AACT MCP Server locally on Windows so that the CT.Sight web application (running in Docker) can reach it.

## Why Does This Run Outside Docker?

The CT.Sight backend, frontend, and database all run inside Docker via `docker compose`. The MCP server, however, runs **directly on your Windows machine** as a lightweight Python process. The CT.Sight backend reaches it through Docker Desktop's `host.docker.internal` hostname, which resolves to your host machine from inside a container.

```
┌─────────────────────────────────────────────────────┐
│  Docker (CT.Sight)                                  │
│                                                     │
│  frontend:3000 ──► backend:8000 ──────────────────────────► host.docker.internal:8001
│                        │                            │              │
│                        ▼                            │              ▼
│                   postgres:5432                     │     AACT MCP Server
│                                                     │     (this repo, on Windows)
└─────────────────────────────────────────────────────┘
```

The MCP server is a pure data-serving process. It loads bundled JSON files into memory and serves them over HTTP. It does not call any LLM, does not connect to any database, and requires no API keys.

## Prerequisites

- **Python 3.10+** installed on Windows and available in your PATH
- **Docker Desktop** installed and running (for CT.Sight)
- The [CT.Sight repository](https://github.com/pakejeso/clinical-trials-search) cloned locally

## Step 1: Clone and Install the MCP Server

Open a **PowerShell** terminal:

```powershell
# Clone the repository (if not already done)
git clone https://github.com/pakejeso/aatc_mcp_server.git
cd aatc_mcp_server

# Install in editable mode (one-time)
pip install -e .
```

This installs the `aact-mcp-server` command and the single dependency (`mcp>=1.0.0`).

## Step 2: Start the MCP Server in HTTP Mode

In the **same PowerShell terminal**, set the environment variables and start the server:

```powershell
$env:AACT_MCP_TRANSPORT = "streamable-http"
$env:AACT_MCP_HOST = "0.0.0.0"
$env:AACT_MCP_PORT = "8001"

aact-mcp-server
```

You should see output like:

```
2025-xx-xx [aact-mcp-server] INFO: Starting AACT MCP Server in HTTP mode on 0.0.0.0:8001
2025-xx-xx [aact-mcp-server] INFO: MCP endpoint : http://0.0.0.0:8001/mcp
2025-xx-xx [aact-mcp-server] INFO: Health check : http://0.0.0.0:8001/health
```

### Verify It Is Running

Open a browser or a second PowerShell terminal and check the health endpoint:

```powershell
Invoke-RestMethod http://localhost:8001/health
```

Expected response:

```json
{
  "status": "ok",
  "server": "aact-mcp-server",
  "transport": "streamable-http",
  "tables": 48,
  "foreign_keys": 63,
  "glossary_loaded": true,
  "column_profiles_loaded": true,
  "query_patterns_loaded": true
}
```

## Step 3: Start CT.Sight (Docker)

Open a **second PowerShell terminal** and start the CT.Sight stack:

```powershell
cd C:\path\to\clinical-trials-search

# First time: create your .env
copy .env.example .env
# Edit .env to set OPENAI_API_KEY, AACT_DATABASE_URL, etc.

docker compose up --build
```

The CT.Sight `docker-compose.yml` already configures the backend to reach the MCP server:

```yaml
AACT_MCP_URL: ${AACT_MCP_URL:-http://host.docker.internal:8001}
```

No changes needed — Docker Desktop on Windows resolves `host.docker.internal` to your host machine, where the MCP server is listening on port 8001.

## Step 4: Verify End-to-End Connectivity

Once both are running, you can verify the CT.Sight backend can reach the MCP server:

```powershell
# From the host — check the backend health
Invoke-RestMethod http://localhost:8000/api/v1/health

# From inside the backend container — check MCP connectivity
docker exec clinical-trials-backend python -c "import httpx; print(httpx.get('http://host.docker.internal:8001/health').json())"
```

## Summary: Two Terminals

| Terminal | What to Run | Stays Running? |
|:---|:---|:---|
| **Terminal 1** | `aact-mcp-server` (with env vars set) | Yes — leave open |
| **Terminal 2** | `docker compose up --build` (in CT.Sight repo) | Yes — leave open |

## Troubleshooting

### "Connection refused" from the backend

- Ensure the MCP server is running **before** starting Docker Compose.
- Ensure `AACT_MCP_HOST` is set to `0.0.0.0` (not `127.0.0.1`). The Docker container connects via `host.docker.internal`, which requires the server to listen on all interfaces.
- Check that Windows Firewall is not blocking port 8001.

### "aact-mcp-server" command not found

- Make sure you ran `pip install -e .` from the `aatc_mcp_server` directory.
- Verify your Python Scripts directory is in your PATH. You can also run the server as:
  ```powershell
  python -m src
  ```

### Environment variables reset after closing PowerShell

PowerShell `$env:` variables are session-scoped. If you close the terminal, you need to set them again. To make them persistent, you can either:

1. **Create a start script** (e.g., `start_mcp.ps1`):
   ```powershell
   $env:AACT_MCP_TRANSPORT = "streamable-http"
   $env:AACT_MCP_HOST = "0.0.0.0"
   $env:AACT_MCP_PORT = "8001"
   aact-mcp-server
   ```
   Then run: `.\start_mcp.ps1`

2. **Set system environment variables** via Windows Settings > System > Advanced > Environment Variables.

### Port 8001 already in use

Change the port in both places:

```powershell
# MCP server side
$env:AACT_MCP_PORT = "8002"

# CT.Sight side (.env file)
AACT_MCP_URL=http://host.docker.internal:8002
```
