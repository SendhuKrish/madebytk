FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies (cached layer)
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Copy application code
COPY app/ ./app/

# Expose the API port
EXPOSE 8100

# Start uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "2"]
