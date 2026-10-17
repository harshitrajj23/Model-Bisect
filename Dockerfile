FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    git config --system --add safe.directory '*' && \
    rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser

WORKDIR /app

# Install dependencies first for optimal layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy Model Bisect source package
COPY model_bisect /app/model_bisect

# Environment configuration
ENV PYTHONUNBUFFERED=1 \
    MODEL_BISECT_DB_PATH=/data/model_bisect.db \
    MODEL_BISECT_TARGETS_FILE=/app/targets.toml \
    MODEL_BISECT_REPO_ROOT=/repos

# Prepare directories with permissions for non-root user
RUN mkdir -p /data /repos && chown -R appuser:appuser /app /data /repos

USER appuser

EXPOSE 8000

CMD ["uvicorn", "model_bisect.api:app", "--host", "0.0.0.0", "--port", "8000"]
