#!/usr/bin/env python3
"""
SEPTA Live – local proxy server + statistics store.

Usage:
    pip install flask requests gunicorn
    python3 server.py          # -> http://localhost:5000
    python3 server.py --port 8080

Production (via Docker / gunicorn):
    gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 4 server:app
"""

import argparse

from pkg.app import create_app

app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    print(f"\n  SEPTA Live  →  http://localhost:{args.port}\n")
    app.run(host="127.0.0.1", port=args.port, debug=False)
