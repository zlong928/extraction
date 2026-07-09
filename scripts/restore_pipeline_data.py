#!/usr/bin/env python3
"""Re-parse the 6 PDFs through MinerU and restore data/pipeline_batch/.

Run: .venv/bin/python scripts/restore_pipeline_data.py [--skip-existing]
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import RESULT_DIR
from app.services.mineru_parser import MinerUParserService

SKIP_EXISTING = "--skip-existing" in sys.argv

PDFS = [
    ("001-2017_3D_HA_3D_printing_of_bacteria_into_functional_complex_materials",
     "pdf/2017 3D打印苯酚降解HA混合物 3D printing of bacteria into functional complex materials.pdf"),
    ("002-2023_Highly_Efficient_Nitrogen_Fixing_Microbial_Hydrogel_Device",
     "pdf/2023 Highly Efficient Nitrogen-Fixing Microbial Hydrogel Device.pdf"),
    ("003-2024_Living_Porous_Ceramics_for_Bacteria_Regulated_Gas_Sensing_and_Carbon_Capture",
     "pdf/2024 Living Porous Ceramics for Bacteria‐Regulated Gas Sensing and Carbon Capture.pdf"),
    ("004-2025_Dual_carbon_sequestration_with_photosynthetic_living_materials",
     "pdf/2025 Dual carbon sequestration with photosynthetic living materials.pdf"),
    ("005-2025_Retrievable_hydrogel_networks_with_confined",
     "pdf/2025 Retrievable hydrogel networks with confined.pdf"),
    ("006-2026_Mesospace_domain_orchestrates_microbial",
     "pdf/2026 Mesospace domain orchestrates microbial.pdf"),
]


def restore(name: str, pdf_rel: str) -> None:
    out_dir = ROOT / "data" / "pipeline_batch" / name / "structured"
    if SKIP_EXISTING and out_dir.exists() and (out_dir / "content_list_v2.json").is_file():
        print(f"  ⏭️  {name} — already exists, skip")
        return

    pdf_path = ROOT / pdf_rel
    if not pdf_path.is_file():
        print(f"  ❌ {name} — PDF not found: {pdf_path}")
        return

    print(f"  📄 Parsing: {pdf_path.name} ...")

    mineru = MinerUParserService().parse_pdf_file(
        str(pdf_path),
        data_id=name,
        output_root=str(RESULT_DIR / "mineru_restore"),
    )

    if not mineru.content_list_path:
        print(f"  ⚠️  No content_list in MinerU result for {name}")
        return

    # Copy structures to pipeline_batch dir
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    src_extract = Path(mineru.extract_dir)
    if (src_extract / "content_list_v2.json").is_file():
        shutil.copy2(src_extract / "content_list_v2.json", out_dir / "content_list_v2.json")
    elif (src_extract / "content_list.json").is_file():
        shutil.copy2(src_extract / "content_list.json", out_dir / "content_list.json")

    images_src = src_extract / "images"
    if images_src.is_dir():
        dst_images = out_dir / "images"
        dst_images.mkdir(exist_ok=True)
        for img in images_src.iterdir():
            if img.is_file():
                shutil.copy2(img, dst_images / img.name)

    layout_src = src_extract / "layout.json"
    if layout_src.is_file():
        shutil.copy2(layout_src, out_dir / "layout.json")

    print(f"  ✅ {name} — restored to {out_dir}")


def main():
    print(f"MinerU API: {MinerUParserService().api_key[:16]}...")
    print()

    for name, pdf_rel in PDFS:
        try:
            restore(name, pdf_rel)
        except Exception as e:
            print(f"  ❌ {name} — failed: {e}")
        print()

    print("=== Restore complete ===")
    print(f"Output: {ROOT / 'data' / 'pipeline_batch'}")


if __name__ == "__main__":
    main()
