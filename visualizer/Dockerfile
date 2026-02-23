FROM python:3.11-slim

LABEL maintainer="pakejeso"
LABEL description="MCP Flow Visualizer — Interactive demo of the MCP architecture with AACT database"

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY index.html .
COPY schema_compact.txt .

# Non-root user for security
RUN useradd -m appuser
USER appuser

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "2", "--timeout", "120", "app:app"]
