from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

BANNED_KEYWORDS = [
    "MHN@TA", "MHN", "microalgae", "Chlorella", "Chlorella zofingiensis",
    "before soaking", "after soaking", "soaking in water",
    "negligible leakage", "fluorescence retention",
    "fluorescence intensity retention",
]
FILES_TO_SCAN = [
    "app/services/mineru_asset_builder.py",
    "app/services/local_image_profiler.py",
]


def test_no_domain_hardcoded_keywords() -> None:
    for rel_path in FILES_TO_SCAN:
        content = (ROOT / rel_path).read_text(encoding="utf-8")
        for keyword in BANNED_KEYWORDS:
            for i, line in enumerate(content.split('\n'), 1):
                stripped = line.strip()
                if keyword in stripped and not stripped.startswith('#'):
                    pytest.fail(f"{rel_path}:{i}: contains '{keyword}'")
