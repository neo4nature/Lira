"""Backward-compatible entrypoint.

Run with:
  uvicorn api.api_lira:app --host 0.0.0.0 --port 8080

Preferred:
  uvicorn api.main:app --host 0.0.0.0 --port 8080
"""

from .main import app
