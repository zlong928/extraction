#!/usr/bin/env python3
"""Run content_pipeline end-to-end with synthetic data.

Usage:
    .venv/bin/python scripts/run_pipeline_demo.py [--real-llm]

FLAGS:
    --real-llm   Use real LLM (requires VLM_API_KEY in .env)
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT))

from content_pipeline import run_content_graph_pipeline
from content_pipeline.contracts.audit import ExtractionPipelineOptions

REAL_LLM = "--real-llm" in sys.argv


def _write_png(path: Path, width: int = 200, height: int = 150) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import zlib
    import struct as st
    raw = b"".join(b"\x00" + bytes([200, 200, 200] * width) for _ in range(height))
    compressed = zlib.compress(raw)
    def chunk(t: int, d: bytes) -> bytes:
        c = st.pack(">I", t) + d
        crc = st.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return st.pack(">I", len(d)) + c + crc
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(0x49484452, st.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(0x49444154, compressed)
        + chunk(0x49454E44, b"")
    )


def build_content_list(tmp: Path) -> Path:
    images_dir = tmp / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    _write_png(images_dir / "fig1.png", 400, 300)
    _write_png(images_dir / "fig1_panel_a.png", 180, 240)
    _write_png(images_dir / "fig1_panel_b.png", 180, 240)
    _write_png(images_dir / "fig2.png", 400, 300)

    pages = [
        [
            {"type": "title", "content": "Results and Discussion", "level": 1, "bbox": [0, 0, 500, 50]},
            {"type": "paragraph", "content": "We characterized the mechanical properties of the hydrogel.", "bbox": [0, 60, 500, 100]},
        ],
        [
            {"type": "title", "content": "Figure 1", "level": 2, "bbox": [0, 0, 500, 40]},
            {
                "type": "image",
                "content": {
                    "image_caption": [
                        {"type": "text", "content": "Fig. 1 | (a) Compressive stress-strain curves; (b) Swelling ratio over time."}
                    ],
                    "image_source": {"path": "images/fig1.png"},
                },
                "bbox": [20, 50, 400, 320],
            },
            {
                "type": "image",
                "content": {
                    "image_caption": [
                        {"type": "text", "content": "(a) Stress-strain curves of PEGDA hydrogels at different crosslinking densities."}
                    ],
                    "image_source": {"path": "images/fig1_panel_a.png"},
                },
                "bbox": [20, 50, 200, 280],
            },
            {
                "type": "image",
                "content": {
                    "image_caption": [
                        {"type": "text", "content": "(b) Swelling kinetics in PBS buffer at 37°C."}
                    ],
                    "image_source": {"path": "images/fig1_panel_b.png"},
                },
                "bbox": [210, 50, 400, 280],
            },
            {"type": "paragraph", "content": "Fig. 1 demonstrates the mechanical tunability of the hydrogel system.", "bbox": [0, 330, 500, 370]},
        ],
        [
            {"type": "title", "content": "Figure 2", "level": 2, "bbox": [0, 0, 500, 40]},
            {
                "type": "chart",
                "content": {
                    "chart_caption": [
                        {"type": "text", "content": "Figure 2. Cell viability measured by Live/Dead assay after 7 days of culture."}
                    ],
                    "image_source": {"path": "images/fig2.png"},
                },
                "bbox": [20, 50, 460, 340],
            },
            {"type": "paragraph", "content": "Over 90% cell viability was maintained across all formulations.", "bbox": [0, 350, 500, 390]},
        ],
        [
            {"type": "paragraph", "content": "These results confirm the cytocompatibility of the hydrogel scaffolds.", "bbox": [0, 0, 500, 40]},
        ],
    ]

    path = tmp / "content_list_v2.json"
    path.write_text(json.dumps(pages, ensure_ascii=False))
    return path


def main():
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="pipeline_demo_"))
    print(f"📁 Work dir: {tmp}")

    content_list_path = build_content_list(tmp)
    print(f"📄 Content list: {content_list_path}")
    print(f"🖼️  Images: {list((tmp/'images').iterdir())}")

    output_dir = tmp / "output"
    output_dir.mkdir()

    if REAL_LLM:
        from app.services.pdf.pipeline import build_backend_content_pipeline_client
        client = build_backend_content_pipeline_client()
        if client is None:
            print("❌ No LLM client — set VLM_API_KEY or OPENAI_API_KEY in .env")
            sys.exit(1)
        print("🤖 Using REAL LLM client")
    else:
        from content_pipeline.llm.client import FakeContentPipelineClient
        client = FakeContentPipelineClient(behavior="valid")
        print("🤖 Using FAKE LLM client (add --real-llm to use real LLM)")

    result = run_content_graph_pipeline(
        content_list_path=str(content_list_path),
        layout_path=None,
        image_root=str(tmp),
        paper_id="demo-paper-001",
        query=None,
        model_client=client,
        output_dir=str(output_dir),
        options=ExtractionPipelineOptions(
            fail_fast=False,
            max_workers=2,
            llm_max_workers=2,
            chart_only=False,
        ),
    )

    print(f"\n{'='*60}")
    print(f"✅ Status: {result.status}")
    print(f"📊 Figures: {result.figure_panel_graph.get('figure_count', '?')}")
    print(f"📐 Panels:  {result.figure_panel_graph.get('panel_count', '?')}")
    print(f"📦 Evidence packets: {len(result.evidence_packets)}")
    print(f"📝 Audit trace events: {len(result.audit_trace)}")
    if result.errors:
        print(f"⚠️  Errors ({len(result.errors)}):")
        for e in result.errors[:5]:
            print(f"   - {e}")

    print("\n📂 Output files:")
    for k, v in result.output_paths.items():
        if v:
            print(f"   {k}: {v}")

    if result.audit_trace:
        events = {}
        for r in result.audit_trace:
            ev = r.get("event", "(empty)")
            events[ev] = events.get(ev, 0) + 1
        print("\n📊 Event summary:")
        for ev, cnt in sorted(events.items(), key=lambda x: -x[1]):
            print(f"   {ev:45s} {cnt:3d}")

    print(f"\n📁 Work dir preserved: {tmp}")
    print(f"Run: rm -rf {tmp}")


if __name__ == "__main__":
    main()
