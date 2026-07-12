#!/usr/bin/env python3
"""Import pipeline_batch papers into the web UI database and link existing content_pipeline_results.

Run: .venv/bin/python scripts/import_to_webui.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR
from app.db import SessionLocal
from app.services.pdf.artifact_service import LocalMinerUArtifactService
from app.services.pdf.audit import audit_table_path_for_paper
from app.services.storage import StorageService

BATCH_ROOT = DATA_DIR / "pipeline_batch"
RESULTS_ROOT = DATA_DIR / "content_pipeline_results"

PIPELINE_BATCH_DIRS = [
    "001-2017_3D_HA_3D_printing_of_bacteria_into_functional_complex_materials",
    "002-2023_Highly_Efficient_Nitrogen_Fixing_Microbial_Hydrogel_Device",
    "003-2024_Living_Porous_Ceramics_for_Bacteria_Regulated_Gas_Sensing_and_Carbon_Capture",
    "004-2025_Dual_carbon_sequestration_with_photosynthetic_living_materials",
    "005-2025_Retrievable_hydrogel_networks_with_confined",
    "006-2026_Mesospace_domain_orchestrates_microbial",
]


def find_content_list(dir_name: str) -> Path | None:
    structured = BATCH_ROOT / dir_name / "structured"
    candidates = list(structured.rglob("*content_list_v2*.json"))
    return candidates[0] if candidates else None


def has_existing_results(dir_name: str) -> bool:
    return (RESULTS_ROOT / dir_name).is_dir()


def copy_existing_results(source_dir: str, paper_id: int) -> None:
    src = RESULTS_ROOT / source_dir
    dst = RESULTS_ROOT / f"paper_{paper_id}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"  Copied {src.name} -> {dst.name}")


def main():
    db = SessionLocal()
    try:
        service = LocalMinerUArtifactService(db, storage=StorageService())

        for dir_name in PIPELINE_BATCH_DIRS:
            content_list = find_content_list(dir_name)
            if not content_list:
                print(f"  SKIP {dir_name}: no content_list_v2 found")
                continue

            print(f"Importing {dir_name} ...")
            try:
                paper = service.import_artifact(str(content_list))
            except ValueError as e:
                print(f"  FAIL {dir_name}: {e}")
                continue

            print(f"  Created paper#{paper.id}: {paper.title}")

            has_results = has_existing_results(dir_name)
            if has_results:
                copy_existing_results(dir_name, paper.id)
                print(f"  Linked existing content_pipeline_results -> paper_{paper.id}")

                audit_path, source = audit_table_path_for_paper(paper)
                if audit_path:
                    print(f"  ✅ Audit table found: {audit_path.name} (source={source})")
                    with open(audit_path) as f:
                        data = json.load(f)
                    print(f"     chart_facts={len(data.get('chart_facts') or [])}, "
                          f"heatmap_candidates={len(data.get('heatmap_candidates') or [])}, "
                          f"image_observations={len(data.get('image_observations') or [])}")
                else:
                    print("  ⚠️  Audit table NOT found via historical matching")
            else:
                print("  ℹ️  No existing results; needs chart-only run from frontend")

        print("\n=== Done ===")
    finally:
        db.close()


if __name__ == "__main__":
    main()
