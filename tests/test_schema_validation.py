from __future__ import annotations

from pathlib import Path

import json

import pytest

try:
    from jsonschema import validate
except ModuleNotFoundError:
    pytest.skip("jsonschema not installed", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skip(reason="schemas/ 目录为空，schema 文件不存在")
def test_schema_validation() -> None:
    pass
