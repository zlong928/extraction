from __future__ import annotations

import json
from pathlib import Path

from content_pipeline.graph.document_graph import DocumentGraphBuilder
from content_pipeline.graph.figure_panel_graph import FigurePanelGraphBuilder
from content_pipeline.graph.layout_graph import LayoutGraphBuilder
from content_pipeline.mineru.content_block_normalizer import ContentBlockNormalizer


def _graph_blocks():
    pages = [[
        {"type": "title", "content": {"title_content": "Results", "level": 1}, "bbox": [0, 0, 400, 40]},
        {"type": "image", "content": {"image_caption": "Fig. 1 | (a) before; (b) after"}, "bbox": [0, 50, 180, 200]},
        {"type": "image", "content": {"image_caption": "Fig. 1 | (a) before; (b) after"}, "bbox": [200, 50, 380, 200]},
        {"type": "table", "content": {"table_html": "<table></table>"}, "bbox": [0, 220, 380, 300]},
    ]]
    return ContentBlockNormalizer().normalize_pages(pages)


def _two_figure_blocks():
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Growth rate over time"}, "bbox": [0, 50, 180, 200]},
        {"type": "image", "content": {"image_caption": "Fig. 2 | Cell viability"}, "bbox": [200, 50, 380, 200]},
    ]]
    return ContentBlockNormalizer().normalize_pages(pages)


def test_document_graph_page_reading_order_and_indexes() -> None:
    graph = DocumentGraphBuilder().build(_graph_blocks())

    assert graph.summary()["block_count"] == 4
    assert [b.reading_order for b in graph.pages[0].blocks_by_reading_order] == [0, 1, 2, 3]
    assert len(graph.image_blocks) == 2
    assert len(graph.table_blocks) == 1
    assert graph.heading_blocks[0].text == "Results"


def test_document_graph_filters_page_noise_and_compresses_order() -> None:
    pages = [[
        {"type": "page_header", "content": "Journal header"},
        {"type": "title", "content": {"title_content": "Results", "level": 1}},
        {"type": "page_number", "content": "1"},
        {"type": "page_footnote", "content": "publisher footnote"},
        {"type": "page_aside_text", "content": "aside"},
        {"type": "paragraph", "content": "Main text"},
        {"type": "page_footer", "content": "Footer"},
    ]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)

    assert [b.normalized_type for b in graph.blocks] == ["heading", "text"]
    assert [b.reading_order for b in graph.pages[0].blocks_by_reading_order] == [0, 1]
    assert [b.global_order for b in graph.blocks] == [0, 1]
    assert graph.blocks[1].metadata["mineru_reading_order"] == 5
    assert graph.summary()["filtered_block_count"] == 5
    assert graph.summary()["filtered_type_counts"]["page_header"] == 1
    assert graph.summary()["filtered_type_counts"]["aside"] == 1
    assert all("filtered_reason" in block.metadata for block in graph.filtered_blocks)


def test_document_graph_reference_list_enters_reference_blocks() -> None:
    pages = [[
        {"type": "list", "content": {"list_type": "reference_list", "items": ["[1] Smith et al."]}},
    ]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)

    assert graph.reference_blocks
    assert graph.summary()["reference_count"] > 0


def test_layout_panel_iou_matching(tmp_path: Path) -> None:
    blocks = _graph_blocks()
    graph = DocumentGraphBuilder().build(blocks)
    layout = [{"page_idx": 0, "preproc_blocks": [{"type": "panel", "bbox": [0, 50, 180, 200]}]}]
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(json.dumps(layout), encoding="utf-8")

    layout_graph = LayoutGraphBuilder().build(layout_path, graph)

    assert blocks[1].block_id in layout_graph.matches
    assert layout_graph.matches[blocks[1].block_id].layout_matched_panel is True


def test_figure_graph_builds_independent_panel_nodes() -> None:
    graph = DocumentGraphBuilder().build(_graph_blocks())
    layout_graph = LayoutGraphBuilder().build(None, graph)

    figure_graph = FigurePanelGraphBuilder().build(graph, layout_graph)

    assert len(figure_graph.figures) == 1
    panels = figure_graph.panel_nodes()
    assert len(panels) == 2
    assert panels[0].panel_id != panels[1].panel_id
    assert panels[0].local_context_block_ids != panels[1].local_context_block_ids
    assert panels[0].sibling_panel_ids == [panels[1].panel_id]


def test_figure_graph_does_not_merge_different_figure_captions() -> None:
    blocks = _two_figure_blocks()
    graph = DocumentGraphBuilder().build(blocks)
    figure_graph = FigurePanelGraphBuilder().build(graph, LayoutGraphBuilder().build(None, graph))

    assert len(figure_graph.figures) == 2
    assert figure_graph.figures[0].label == "Fig. 1"
    assert figure_graph.figures[1].label == "Fig. 2"


def test_figure_graph_composite_figure_when_same_caption_has_ab_markers() -> None:
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | (a) before treatment; (b) after treatment"}, "bbox": [0, 50, 180, 250]},
        {"type": "image", "content": {"image_caption": "Fig. 1 | (a) before treatment; (b) after treatment"}, "bbox": [200, 50, 380, 250]},
    ]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)
    figure_graph = FigurePanelGraphBuilder().build(graph, LayoutGraphBuilder().build(None, graph))

    assert len(figure_graph.figures) == 1
    assert len(figure_graph.figures[0].panels) == 2
    prov = figure_graph.figures[0].provenance
    assert "same_caption_composite" in prov.get("all_provenances", prov.get("method", ""))


def test_figure_graph_conservative_split_uncertain() -> None:
    pages = [[
        {"type": "image", "content": {"image_caption": "Growth curve"}, "bbox": [0, 50, 180, 200]},
        {"type": "image", "content": {"image_caption": "Viability assay"}, "bbox": [200, 50, 380, 200]},
    ]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)
    figure_graph = FigurePanelGraphBuilder().build(graph, LayoutGraphBuilder().build(None, graph))

    assert len(figure_graph.figures) >= 1


def test_bbox_spatial_cluster_merges_incomplete_same_figure_panels() -> None:
    pages = [[
        {"type": "image", "content": {}, "bbox": [0, 50, 180, 200]},
        {"type": "image", "content": {}, "bbox": [195, 55, 375, 205]},
    ]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)
    figure_graph = FigurePanelGraphBuilder().build(graph, LayoutGraphBuilder().build(None, graph))

    assert len(figure_graph.figures) == 1
    assert len(figure_graph.figures[0].panels) == 2
    assert figure_graph.figures[0].provenance["method"] == "bbox_spatial_cluster"


def test_bbox_spatial_cluster_keeps_different_figure_numbers_split() -> None:
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | A"}, "bbox": [0, 50, 180, 200]},
        {"type": "image", "content": {"image_caption": "Fig. 2 | B"}, "bbox": [195, 55, 375, 205]},
    ]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)
    figure_graph = FigurePanelGraphBuilder().build(graph, LayoutGraphBuilder().build(None, graph))

    assert len(figure_graph.figures) == 2
    assert all(fig.provenance["method"] == "separate_caption" for fig in figure_graph.figures)


def test_mineru_nested_composite_caption_groups_prior_visual_blocks() -> None:
    pages = [[
        {"type": "image", "img_path": "a.jpg", "image_caption": ["a)"], "bbox": [0, 0, 100, 100]},
        {"type": "chart", "img_path": "b.jpg", "chart_caption": ["b)"], "bbox": [110, 0, 210, 100]},
        {"type": "chart", "img_path": "c.jpg", "chart_caption": ["Figure 3. Porous ceramics. a) Wicking. b) Evaporation. c) Bacterial density."], "bbox": [220, 0, 320, 100]},
    ]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)
    figure_graph = FigurePanelGraphBuilder().build(graph, LayoutGraphBuilder().build(None, graph))

    assert len(figure_graph.figures) == 1
    figure = figure_graph.figures[0]
    assert figure.figure_id == "figure-3"
    assert len(figure.panels) == 3
    assert [panel.parent_figure_id for panel in figure.panels] == ["figure-3", "figure-3", "figure-3"]
    assert figure.provenance["method"] == "mineru_nested_composite_caption"
    assert figure.provenance["full_caption_block_ids"] == [blocks[2].block_id]
    assert blocks[2].block_id in figure.panels[0].caption_block_ids
    assert blocks[2].block_id in figure.panels[1].caption_block_ids
    assert blocks[2].block_id in figure.panels[2].caption_block_ids


def test_panel_node_independent_context() -> None:
    graph = DocumentGraphBuilder().build(_graph_blocks())
    figure_graph = FigurePanelGraphBuilder().build(graph, LayoutGraphBuilder().build(None, graph))

    for panel in figure_graph.panel_nodes():
        assert panel.local_context_block_ids
        assert panel.caption_block_ids
        assert isinstance(panel.related_table_ids, list)
        assert isinstance(panel.related_formula_ids, list)
        assert isinstance(panel.related_reference_ids, list)
        assert isinstance(panel.sibling_panel_ids, list)
        assert panel.provenance


def test_figure_graph_provenance_recorded() -> None:
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Test image"}, "bbox": [0, 50, 180, 200]},
    ]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)
    figure_graph = FigurePanelGraphBuilder().build(graph, LayoutGraphBuilder().build(None, graph))

    assert figure_graph.figures[0].provenance
