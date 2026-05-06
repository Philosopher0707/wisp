# Wisp Cloud Server Dockerfile
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml setup.py ./
COPY wisp/ ./wisp/

# Install Python dependencies
RUN pip install --no-cache-dir -e "."

# Create workspace directory
RUN mkdir -p /workspace
ENV WISP_WORKSPACE=/workspace

# Expose the server port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default command
CMD ["wisp", "server", "--host", "0.0.0.0", "--port", "8000"]
