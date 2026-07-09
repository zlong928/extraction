from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_extraction_schemas_directory_removed() -> None:
    extraction_dir = ROOT / "schemas" / "extraction_results"
    assert not extraction_dir.exists(), (
        "schemas/extraction_results/ was removed as part of dead code cleanup. "
        "All domain-specific extraction schemas were unused in production."
    )
