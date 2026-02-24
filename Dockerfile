FROM python:3.11-slim

LABEL maintainer="pakejeso"
LABEL description="AACT MCP Server + Flow Visualizer — Interactive demo of the MCP architecture"

WORKDIR /app

# Install visualizer dependencies
COPY visualizer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install MCP server dependency
RUN pip install --no-cache-dir "mcp>=1.0"

# Copy the MCP server source and data
COPY src/ ./src/
COPY data/ ./data/

# Copy the visualizer
COPY visualizer/app.py ./visualizer/app.py
COPY visualizer/index.html ./visualizer/index.html

# Non-root user for security
RUN useradd -m appuser
USER appuser

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

# app.py expects MCP_SERVER_DIR to be the parent of visualizer/
# Since we COPY visualizer/ into /app/visualizer/, the parent is /app/
# which contains src/ and data/ — exactly what the MCP server needs.
CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "2", "--timeout", "120", "--chdir", "/app/visualizer", "app:app"]
