# Dockerfile for SwimCoach AI FastAPI Backend
# Optimized for Fly.io deployment

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml ./

# Install Python dependencies
# We install without dev dependencies for production
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source code
COPY src/ ./src/

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose port (Fly.io will map this)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health')"

# Run uvicorn server
# Using 0.0.0.0 to accept connections from any interface
# Fly.io requires listening on 0.0.0.0, not 127.0.0.1
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]

