from __future__ import annotations

from pathlib import Path

import pytest
from content_pipeline.graph.document_graph import DocumentGraphBuilder
from content_pipeline.graph.figure_panel_graph import FigurePanelGraphBuilder
from content_pipeline.graph.layout_graph import LayoutGraphBuilder
from content_pipeline.mineru.content_block_normalizer import ContentBlockNormalizer
from content_pipeline.evidence.context_selector import EvidenceContextSelector
from content_pipeline.evidence.evidence_packet import EvidencePacketBuilder

BASE = Path("data/pipeline_batch/004-2025_Dual_carbon_sequestration_with_photosynthetic_living_materials")
CONTENT_LIST_V2 = BASE / "structured" / "content_list_v2.json"
IMAGE_ROOT = BASE / "structured" / "images"
LAYOUT_PATH = BASE / "structured" / "layout.json"


@pytest.fixture(scope="session")
def real_blocks():
    if not CONTENT_LIST_V2.is_file() or not IMAGE_ROOT.is_dir():
        pytest.skip("real pipeline fixture data is not present")
    normalizer = ContentBlockNormalizer(image_root=str(IMAGE_ROOT))
    return normalizer.load(str(CONTENT_LIST_V2))


@pytest.fixture(scope="session")
def real_doc_graph(real_blocks):
    return DocumentGraphBuilder().build(real_blocks)


@pytest.fixture(scope="session")
def real_fp_graph(real_doc_graph):
    if not LAYOUT_PATH.is_file():
        pytest.skip("real pipeline layout fixture is not present")
    layout_graph = LayoutGraphBuilder().build(str(LAYOUT_PATH), real_doc_graph)
    return FigurePanelGraphBuilder().build(real_doc_graph, layout_graph)


def test_real_data_has_blocks(real_blocks):
    assert len(real_blocks) > 0


def test_real_data_has_images(real_doc_graph):
    assert len(real_doc_graph.image_blocks) >= 10


def test_real_data_has_figures(real_fp_graph):
    assert len(real_fp_graph.figures) >= 1


def test_real_data_has_panels(real_fp_graph):
    assert len(real_fp_graph.panel_nodes()) > 0


def test_real_data_evidence_packets(real_doc_graph, real_fp_graph):
    selector = EvidenceContextSelector()
    packet_builder = EvidencePacketBuilder()
    packet_count = 0
    for figure in real_fp_graph.figures:
        for panel in figure.panels:
            selected = selector.select_for_panel(real_doc_graph, figure, panel)
            packet = packet_builder.build(
                paper_id="test", document_graph=real_doc_graph,
                figure=figure, panel=panel, selected=selected,
            )
            assert packet.primary_caption is not None
            packet_count += 1
    assert packet_count > 0


def test_real_data_has_pipeline_inputs_manifest():
    import json
    manifest_path = BASE / "structured" / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("real pipeline manifest fixture is not present")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("pipeline_ready") is not False
    assert manifest.get("rebuilt") is True
