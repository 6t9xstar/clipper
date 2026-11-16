"""Shared test helpers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipper.core import token


def authed(app: FastAPI) -> TestClient:
    """TestClient that sends the per-install API token like a script would."""
    return TestClient(app, base_url="http://127.0.0.1", headers={token.HEADER: app.state.token})
