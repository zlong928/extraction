from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LocalImageProfile:
    asset_scope: str
    evidence_shape: str
    figure_role: str
    recommended_extractor: str
    extraction_readiness: str
    skip_reason: str | None
    confidence: float
    uncertainty_reason: str


class LocalImageProfiler:
    """Deterministic local image profile hints before any LLM/VLM call.

    The profiler uses only MinerU/local metadata such as type, bbox, image size,
    caption, and page context. It does not perform OCR, CV segmentation, or chart
    digitization.
    """

    MIN_EXTRACTABLE_DIMENSION = 32
    MIN_EXTRACTABLE_FILE_SIZE_BYTES = 256

    _SHAPE_TO_EXTRACTOR = {
        "conceptual_schematic": "overview_schematic_extractor",
        "fabrication_workflow": "fabrication_device_extractor",
        "device_architecture": "fabrication_device_extractor",
        "macro_material_photo": "macro_sample_extractor",
        "microscopy_structure": "microscopy_bio_material_extractor",
        "experimental_performance_plot": "plot_numeric_extractor",
        "omics_or_pathway_analysis": "omics_pathway_extractor",
        "chemical_characterization": "chemical_characterization_extractor",
        "simulation_spatial_field": "simulation_modeling_extractor",
    }

    _SHAPE_TO_ROLE = {
        "conceptual_schematic": "system_overview",
        "fabrication_workflow": "fabrication_evidence",
        "device_architecture": "fabrication_evidence",
        "macro_material_photo": "structure_characterization",
        "microscopy_structure": "structure_characterization",
        "experimental_performance_plot": "functional_performance_evidence",
        "omics_or_pathway_analysis": "molecular_mechanism_evidence",
        "chemical_characterization": "structure_characterization",
        "simulation_spatial_field": "simulation_support",
    }

    @classmethod
    def profile(
        cls,
        *,
        caption: str,
        nearby_text: str = "",
        width: int | None = None,
        height: int | None = None,
        file_size: int = 0,
        mineru_type: str = "",
        panel_id: str | None = None,
        bbox: list[Any] | None = None,
        layout_page: dict[str, Any] | None = None,
    ) -> LocalImageProfile:
        combined = " ".join(part for part in [caption, nearby_text] if part).lower()
        skip_reasons: list[str] = []
        if width is not None and height is not None:
            if width < cls.MIN_EXTRACTABLE_DIMENSION or height < cls.MIN_EXTRACTABLE_DIMENSION:
                skip_reasons.append(f"too small: {width}x{height}")
        if file_size < cls.MIN_EXTRACTABLE_FILE_SIZE_BYTES:
            skip_reasons.append(f"file too small: {file_size} bytes")
        if not caption.strip() and not nearby_text.strip():
            skip_reasons.append("missing caption and nearby context")

        evidence_shape = cls.evidence_shape_from_hints(
            asset_scope="",
            mineru_type=mineru_type,
            caption=caption,
            nearby_text=nearby_text,
            bbox=bbox,
            layout_page=layout_page,
        )
        asset_scope = cls.asset_scope_from_hints(
            mineru_type=mineru_type,
            caption=caption,
            panel_id=panel_id,
            evidence_shape=evidence_shape,
        )
        if skip_reasons:
            asset_scope = "noise"
            readiness = "skip"
            skip_reason = "; ".join(skip_reasons)
            confidence = 0.9
            uncertainty = "Local readiness gate marked image as non-extractable"
        elif not caption.strip() or panel_id:
            readiness = "low_confidence"
            skip_reason = None
            confidence = 0.55
            uncertainty = "Local asset has weak caption or panel-only labeling"
        else:
            readiness = "ready"
            skip_reason = None
            confidence = 0.7
            uncertainty = "Local MinerU metadata based profile"

        return LocalImageProfile(
            asset_scope=asset_scope,
            evidence_shape=evidence_shape,
            figure_role=cls.figure_role(evidence_shape),
            recommended_extractor=cls.recommended_extractor(evidence_shape),
            extraction_readiness=readiness,
            skip_reason=skip_reason,
            confidence=confidence,
            uncertainty_reason=uncertainty,
        )

    @classmethod
    def evidence_shape_from_hints(
        cls,
        *,
        asset_scope: str = "",
        mineru_type: str = "",
        caption: str = "",
        nearby_text: str = "",
        bbox: list[Any] | None = None,
        layout_page: dict[str, Any] | None = None,
    ) -> str:
        asset_scope = asset_scope.lower().strip()
        mineru_type = mineru_type.lower().strip()
        text = f"{caption} {nearby_text}".lower()
        if asset_scope == "chart_crop" or mineru_type == "chart":
            return "experimental_performance_plot"

        if asset_scope == "microscopy_crop" or any(word in text for word in ["sem", "tem", "afm", "confocal", "microscopy", "microscope", "scale bar"]):
            return "microscopy_structure"
        if any(word in text for word in ["ftir", "raman", "xrd", "xps", "spectrum", "spectra"]):
            return "chemical_characterization"
        if any(word in text for word in ["omics", "transcript", "gene", "pathway", "metabol"]):
            return "omics_or_pathway_analysis"
        if any(word in text for word in ["simulation", "model", "mesh", "finite element"]):
            return "simulation_spatial_field"
        if any(word in text for word in ["photo", "sample", "hydrogel", "colony", "gel", "bead", "macro"]):
            return "macro_material_photo"
        if bbox and layout_page and cls._bbox_covers_large_page_area(bbox, layout_page):
            return "conceptual_schematic"
        return "macro_material_photo" if asset_scope == "panel_crop" else "conceptual_schematic"

    @classmethod
    def asset_scope_from_hints(
        cls,
        *,
        mineru_type: str,
        caption: str,
        panel_id: str | None,
        evidence_shape: str,
    ) -> str:
        normalized_type = mineru_type.lower().strip()
        if normalized_type == "chart":
            return "chart_crop"
        if evidence_shape == "microscopy_structure":
            return "microscopy_crop"
        if panel_id and not cls.figure_label(caption):
            return "panel_crop"
        return "full_figure"

    @classmethod
    def recommended_extractor(cls, evidence_shape: str) -> str:
        return cls._SHAPE_TO_EXTRACTOR.get(evidence_shape, "overview_schematic_extractor")

    @classmethod
    def figure_role(cls, evidence_shape: str) -> str:
        return cls._SHAPE_TO_ROLE.get(evidence_shape, "unknown")

    @staticmethod
    def panel_id(text: str) -> str | None:
        stripped = text.strip()
        match = re.fullmatch(r"(?i)(?:panel\s*)?([a-z])\)?", stripped)
        return match.group(1).lower() if match else None

    @staticmethod
    def figure_label(text: str) -> str | None:
        match = re.search(r"(?i)\b(fig(?:ure)?\.?\s*\d+[a-z]?)\b", text)
        return match.group(1) if match else None

    @staticmethod
    def parent_figure_id(text: str, fallback: str) -> str:
        return LocalImageProfiler.figure_label(text) or fallback

    @staticmethod
    def _bbox_covers_large_page_area(bbox: list[Any], layout_page: dict[str, Any]) -> bool:
        try:
            x1, y1, x2, y2 = [float(value) for value in bbox]
            page_width = float(layout_page.get("width") or layout_page.get("page_width") or 0)
            page_height = float(layout_page.get("height") or layout_page.get("page_height") or 0)
        except Exception:
            return False
        if page_width <= 0 or page_height <= 0:
            return False
        return max(0.0, x2 - x1) * max(0.0, y2 - y1) >= page_width * page_height * 0.15
