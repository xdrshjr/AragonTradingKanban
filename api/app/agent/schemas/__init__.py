"""Schema accessors shared by the app and its tests."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


def schema_path() -> Path:
    return Path(__file__).resolve().parent / "report_doc.schema.json"


@lru_cache(maxsize=1)
def load_schema_dict() -> dict:
    return json.loads(schema_path().read_text(encoding="utf-8"))


__all__ = ["load_schema_dict", "schema_path"]
