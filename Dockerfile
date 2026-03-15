FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Ensure data directory exists (trips.json lives here; mounted as a volume in prod)
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
