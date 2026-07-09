from __future__ import annotations

from typing import Any

from content_pipeline.adapters.visual_fact_adapters import image_observations_from_payload, visual_fact_result_from_payload
from content_pipeline.contracts.blocks import PanelMarkerCandidate
from content_pipeline.contracts.evidence import EvidencePacket, panel_evidence_contract
from content_pipeline.contracts.visual import VisualExtractionContext
from content_pipeline.evidence.context_selector import EvidenceContextSelector
from content_pipeline.evidence.evidence_packet import EvidencePacketBuilder
from content_pipeline.graph.document_graph import DocumentGraphBuilder
from content_pipeline.graph.figure_panel_graph import FigurePanelGraphBuilder
from content_pipeline.graph.layout_graph import LayoutGraphBuilder
from content_pipeline.llm.semantic_phases import _packet_inputs
from content_pipeline.mineru.content_block_normalizer import ContentBlockNormalizer


def _packet_for_panel(
    pages: list[list[dict[str, Any]]],
    *,
    panel_label: str = "a",
    marker_detector: Any | None = None,
) -> EvidencePacket:
    blocks = ContentBlockNormalizer().normalize_pages(pages)
    graph = DocumentGraphBuilder().build(blocks)
    fgraph = FigurePanelGraphBuilder(marker_detector=marker_detector).build(graph, LayoutGraphBuilder().build(None, graph))
    figure = fgraph.figures[0]
    panel = next(panel for panel in figure.panels if panel.panel_label == panel_label)
    selected = EvidenceContextSelector().select_for_panel(graph, figure, panel)
    packet = EvidencePacketBuilder().build(paper_id="p1", document_graph=graph, figure=figure, panel=panel, selected=selected)
    return packet


def _image(caption: str, bbox: list[int], *, footnote: str = "") -> dict[str, Any]:
    block: dict[str, Any] = {"type": "image", "image_caption": caption, "bbox": bbox}
    if footnote:
        block["image_footnote"] = footnote
    return block


def test_exact_panel_segment_is_authoritative_without_legacy_caption_duplicates() -> None:
    caption = "Fig. 1. (a) Alpha. (b) Beta."
    packet = _packet_for_panel(
        [[
            _image(caption, [0, 50, 180, 200]),
            _image(caption, [200, 50, 380, 200]),
        ]],
        panel_label="a",
    )

    contract = panel_evidence_contract(packet)
    segment = contract["caption"]["caption_segment"]
    inputs = _packet_inputs(packet)

    assert segment["status"] == "exact"
    assert "Alpha" in segment["text"]
    assert "Beta" not in segment["text"]
    assert inputs["panel_evidence_contract"]["caption"]["caption_segment"]["text"] == segment["text"]
    assert "caption_text" not in inputs
    assert "caption_rich_text" not in inputs
    assert "caption_structured" not in inputs
    assert "legacy_panel_caption_focus" not in inputs
    assert "panel_caption_focus" not in inputs
    assert "paper_context" not in inputs
    assert "spatial_context" not in inputs
    assert "reading_context" not in inputs
    assert "paper_task_plan_summary" not in inputs
    assert "target_metric_hints" not in inputs


def test_mineru_caption_footnote_is_not_part_of_caption_segment_and_is_supporting_evidence() -> None:
    packet = _packet_for_panel(
        [[
            _image(
                "Fig. 2. (a) Alpha process.",
                [0, 50, 180, 200],
                footnote="Footnote: repeated over three independent runs.",
            ),
        ]],
        panel_label="a",
    )

    contract = panel_evidence_contract(packet)
    segment = contract["caption"]["caption_segment"]
    footnotes = contract["caption"]["figure_footnotes"]
    supporting = contract["evidence"]["supporting"]

    assert segment["status"] == "exact"
    assert "Alpha process" in segment["text"]
    assert "independent runs" not in segment["text"]
    assert footnotes
    assert footnotes[0]["text_level"] == "footnote"
    assert footnotes[0]["evidence_role"] != "primary"
    assert any(item["text_level"] == "footnote" and item["evidence_role"] != "primary" for item in supporting)


def test_grouped_caption_segment_has_group_labels_and_lower_confidence_than_exact_panel() -> None:
    caption = "Fig. 3. (a-d) Shared process. (e) Control."
    detector = _GroupedCaptionMarkerDetector()
    pages = [[
        _image(caption, [0, 50, 90, 150]),
        _image(caption, [100, 50, 190, 150]),
        _image(caption, [200, 50, 290, 150]),
        _image(caption, [300, 50, 390, 150]),
        _image(caption, [400, 50, 490, 150]),
    ]]

    panel_a = _packet_for_panel(pages, panel_label="a", marker_detector=detector)
    panel_e = _packet_for_panel(pages, panel_label="e", marker_detector=detector)

    segment_a = panel_evidence_contract(panel_a)["caption"]["caption_segment"]
    segment_e = panel_evidence_contract(panel_e)["caption"]["caption_segment"]
    inputs_a = _packet_inputs(panel_a)

    assert segment_a["status"] == "grouped_shared"
    assert "Shared process" in segment_a["text"]
    assert "Control" not in segment_a["text"]
    assert segment_a["grouped_panel_labels"] == ["a", "b", "c", "d"]
    assert segment_e["status"] == "exact"
    assert "Control" in segment_e["text"]
    assert segment_a["confidence"] < segment_e["confidence"]
    assert "panel_caption_focus" not in inputs_a
    assert "legacy_panel_caption_focus" not in inputs_a


def test_background_evidence_is_contract_visible_but_not_extractable_prompt_evidence() -> None:
    packet = _packet_for_panel(
        [[
            {"type": "paragraph", "content": "Background-only assay setup from the prior section."},
            {"type": "paragraph", "content": ""},
            {"type": "paragraph", "content": ""},
            {"type": "paragraph", "content": ""},
            _image("Fig. 4. (a) Alpha process.", [0, 100, 180, 250]),
        ]],
        panel_label="a",
    )

    contract = panel_evidence_contract(packet)
    inputs = _packet_inputs(packet)
    background_ids = {item["evidence_id"] for item in contract["evidence"]["background"]}
    prompt_ids = {item["evidence_id"] for item in inputs["evidence_map"]}
    extractable_prompt_ids = {
        item["evidence_id"]
        for item in inputs["evidence_map"]
        if item["use_policy"] == "use_for_extraction"
    }

    assert background_ids
    assert not background_ids & prompt_ids
    assert not background_ids & extractable_prompt_ids
    assert "paper_context" not in inputs


def test_single_visual_full_caption_is_exact_task_and_marker_segment_stays_exact() -> None:
    fallback_packet = _packet_for_panel(
        [[_image("Fig. 5. Growth overview without panel markers.", [0, 50, 180, 200])]],
        panel_label="a",
    )
    exact_packet = _packet_for_panel(
        [[_image("Fig. 6. (a) Alpha process.", [0, 50, 180, 200])]],
        panel_label="a",
    )

    fallback_inputs = _packet_inputs(fallback_packet)
    exact_inputs = _packet_inputs(exact_packet)

    assert fallback_inputs["panel_evidence_contract"]["caption"]["caption_segment"]["status"] == "exact"
    assert "legacy_panel_caption_focus" not in fallback_inputs
    assert exact_inputs["panel_evidence_contract"]["caption"]["caption_segment"]["status"] == "exact"
    assert "panel_caption_focus" not in exact_inputs
    assert "legacy_panel_caption_focus" not in exact_inputs


def test_description_only_visual_payload_is_not_primary_visual_fact_output() -> None:
    context = VisualExtractionContext(
        paper_id="p1",
        figure_id="fig1",
        panel_id="fig1-a",
        image_ref="image.png",
        visual_type="image",
        evidence_map=[{
            "evidence_id": "ev-visual",
            "source_type": "image",
            "use_policy": "use_for_extraction",
            "text_level": "visual",
        }],
        panel_evidence_contract={
            "caption": {"caption_segment": {"status": "exact", "text": "Alpha"}},
        },
    )
    payload = {
        "image_kind": "microscopy",
        "description": "Free-form description without slot candidates.",
        "confidence": 0.6,
        "evidence_ids": ["ev-visual"],
    }

    result = visual_fact_result_from_payload(payload, context)
    observations = image_observations_from_payload(payload, context)

    assert result.visual_fact_candidates == []
    assert observations == []


def test_full_structured_caption_tokens_do_not_leak_sibling_panel_text() -> None:
    caption = [
        {"type": "text", "content": "Fig. 7. (a) Alpha."},
        {"type": "text", "content": "(b) Beta."},
    ]
    packet = _packet_for_panel(
        [[
            {"type": "image", "image_caption": caption, "bbox": [0, 50, 180, 200]},
            {"type": "image", "image_caption": caption, "bbox": [200, 50, 380, 200]},
        ]],
        panel_label="a",
    )

    segment = panel_evidence_contract(packet)["caption"]["caption_segment"]

    assert segment["status"] == "exact"
    assert "Alpha" in segment["text"]
    assert "Beta" not in segment["text"]
    assert segment["structured_tokens"] == {}
    assert segment["formatted_tokens"] == ""


class _GroupedCaptionMarkerDetector:
    def detect(self, caption_text: str, figure_label: str | None = None) -> list[PanelMarkerCandidate]:
        del figure_label
        text_len = len(caption_text)
        return [
            PanelMarkerCandidate(label, text_len + index, text_len + index + 1, 0.92, "test_visual_identity")
            for index, label in enumerate(["a", "b", "c", "d", "e"])
        ]
