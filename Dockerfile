# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Keep Python lean and logs unbuffered for container-friendly output.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY alarm_integration ./alarm_integration
COPY main.py pyproject.toml ./

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Default command runs the polling workflow. Configuration comes from the
# environment (see .env.example). Override the command for one-off runs, e.g.:
#   docker run --env-file .env <image> run --once
ENTRYPOINT ["python", "-m", "alarm_integration"]
CMD ["run"]
