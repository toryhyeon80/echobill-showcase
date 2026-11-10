"""Shared helpers for the HTTP-only comparison adapters (HyperCLOVA X, Claude — PM 10/22)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / "backend" / ".env"


def env_key(name: str) -> str | None:
    """An API key from the environment, else backend/.env. Kept out of backend Settings (measurement only)."""
    key = os.environ.get(name)
    if key:
        return key
    from dotenv import dotenv_values

    return dotenv_values(BACKEND_ENV_FILE).get(name) if BACKEND_ENV_FILE.exists() else None


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """A pydantic JSON schema with its `$defs` inlined (no `$ref`), for vendors that want one flat schema."""
    defs = schema.get("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, list):
            return [inline(item) for item in node]
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(defs[ref.removeprefix("#/$defs/")])
            return {k: inline(v) for k, v in node.items() if k != "$defs"}
        return node

    return inline(schema)
