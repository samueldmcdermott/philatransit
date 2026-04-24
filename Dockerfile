FROM python:3.11-slim

WORKDIR /app

ENV TZ=America/New_York
RUN ln -sf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Version label (read dynamically from VERSION file at build time)
ARG APP_VERSION
RUN APP_VERSION=${APP_VERSION:-$(cat VERSION 2>/dev/null || echo "unknown")} && \
    echo "Building version: $APP_VERSION"
LABEL version="${APP_VERSION}"

# Ensure data directory exists (today.json + daily_cdfs.json live here; mounted as a volume in prod)
RUN mkdir -p data

EXPOSE 5000

# 1 worker + 4 threads: keeps background cache/tracker threads in shared memory.
# Increase --threads if you need more concurrent request capacity.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "server:app"]
