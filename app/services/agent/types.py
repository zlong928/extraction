from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ImageType(Enum):
    """Chart type classification (production core)"""
    # Data chart types
    LINE_PLOT = "line_plot"
    BIPHASIC_TIME_SERIES = "biphasic_time_series"
    MULTI_LINE_PLOT = "multi_line_plot"
    COORDINATE_PLOT = "generic_coordinate_plot"
    RHEOLOGY_FLOW_CURVE = "rheology_flow_curve"
    RHEOLOGY_STRAIN_SWEEP = "rheology_strain_sweep"
    RHEOLOGY_STEP_TIME_SWEEP = "rheology_step_time_sweep"
    SCATTER_PLOT = "scatter_plot"
    BAR_CHART = "bar_chart"
    GROUPED_BAR = "grouped_bar"
    BAR_OR_LINE_WITH_ERRORBAR = "bar_or_line_with_errorbar"
    BOX_PLOT = "box_plot"
    DUAL_AXIS_PLOT = "dual_axis_plot"
    HEATMAP = "heatmap"
    HEATMAP_MATRIX = "heatmap_matrix"
    SPECTRUM_CURVE = "spectrum_curve"
    FIELD_2D_MAP = "2d_field_map"
    TABLE_IMAGE = "table_image"
    GENERIC_COORDINATE_PLOT = "generic_coordinate_plot"
    MULTI_PANEL_COMPOSITE = "multi_panel_composite"

    # Non-data types
    NON_DATA_IMAGE = "non_data_image"
    IMAGE_EVIDENCE = "non_data_image"
    MICROSCOPY_QUANT = "microscopy_quant"
    SCHEMATIC = "schematic"
    SCHEMATIC_OR_PHOTO = "schematic_or_photo"

    # 新增类型
    WESTERN_BLOT = "western_blot"
    GEL_IMAGE = "gel_image"
    FLUORESCENCE_MICROSCOPY = "fluorescence_microscopy"
    SEM_TEM = "sem_tem"
    MULTI_CHANNEL_MICROSCOPY = "multi_channel_microscopy"
    UNKNOWN = "unknown"


class VisualCategory(Enum):
    DATA_CHART = "data_chart"
    MICROSCOPY = "microscopy"
    PROTEIN_ASSAY = "protein_assay"
    NON_DATA_VISUAL = "non_data_visual"
    UNKNOWN = "unknown"


EXTRACTION_POINT_SCHEMA_VERSION = "v2.1"


@dataclass
class ExtractionPoint:
    """单点提取结果（统一 schema）"""
    figure_id: str = ""
    image_path: str = ""
    source_type: str = ""
    extraction_method: str = ""
    route_family: str = ""

    image_type: str = ""
    panel_id: str = ""
    panel_count: int = 1
    classification_confidence: float = 0.0

    series_name: str = ""
    series_color: str = ""
    x_value: float | None = None
    y_value: float | None = None
    y_right_value: float | None = None
    z_value: float | str | None = None
    x_unit: str = ""
    y_unit: str = ""
    z_unit: str = ""
    x_label: str = ""
    y_label: str = ""
    z_label: str = ""
    error_bar: str = ""
    significance: str = ""

    x_axis_type: str = "linear"
    y_axis_type: str = "linear"
    has_dual_y: bool = False

    scale_bar_value: float | None = None
    scale_bar_unit: str = ""
    pixel_size: float | None = None
    object_class: str = ""
    object_count: int = 0
    object_area_physical: float | None = None
    object_diameter_physical: float | None = None
    object_circularity: float | None = None
    object_area_fraction: float | None = None

    band_label: str = ""
    band_intensity: float | None = None
    band_intensity_norm: float | None = None
    molecular_weight_kda: float | None = None
    target_protein: str = ""
    loading_control: str = ""
    lane_number: int | None = None

    overall_description: str = ""
    qualitative: str = ""
    text_evidence: str = ""
    extra_annotations: dict[str, Any] = field(default_factory=dict)

    confidence: float = 0.5
    needs_review: bool = True
    review_reason: str = ""
    quality_tags: list[str] = field(default_factory=list)

    channel: str = ""


@dataclass
class FigureExtractionPlan:
    figure_id: str = ""
    image_path: str = ""
    caption: str = ""
    tasks: list[ExtractionTask] = field(default_factory=list)
    review_notes: str = ""
    image_type: ImageType | None = None
    panel_count: int = 1
    nearby_text: str = ""


@dataclass
class ExtractionTask:
    metric_name: str = ""
    text_context: str = ""
    specific_instruction: str = ""


@dataclass
class ExtractionMap:
    figures: dict[str, FigureExtractionPlan] = field(default_factory=dict)
    text_only_metrics: list[dict] = field(default_factory=list)


@dataclass
class FigureInfo:
    figure_id: str = ""
    image_path: str = ""
    caption: str = ""
    context: str = ""


@dataclass
class TableInfo:
    table_id: str = ""
    label: str = ""
    page_number: int | None = None
    headers: list[str] = field(default_factory=list)
    row_count: int = 0
    markdown: str = ""
    caption: str = ""


@dataclass
class PaperData:
    paper_id: str = ""
    title: str = ""
    content: str = ""
    figures: list[FigureInfo] = field(default_factory=list)
    tables: list[TableInfo] = field(default_factory=list)


@dataclass
class SupervisorState:
    mapping_adjusted: bool = False
    visual_retries: dict[str, int] = field(default_factory=dict)
    reflection_notes: list[dict] = field(default_factory=list)


@dataclass
class ChartMetadata:
    visual_category: VisualCategory = VisualCategory.UNKNOWN
    chart_type: ImageType = ImageType.UNKNOWN
    panel_count: int = 1
    confidence: float = 0.0


@dataclass
class CaptionBinding:
    """Caption 对齐结果"""
    figure_id: str = ""
    caption_text: str = ""
    nearby_text: str = ""
    fig_label_in_text: str = ""       # e.g. "Figure 3a"
    fig_label_in_image: str = ""      # e.g. "Fig. 3A"
    page_number: int | None = None
    reference_count: int = 0          # 正文引用次数
    has_panel_labels: bool = False
    panel_labels: list[str] = field(default_factory=list)
    caption_verified: bool = False    # caption 是否与图像一致
    caption_confidence: float = 0.0


EXTRACTION_POINT_FIELDS = [
    "figure_id", "image_path", "source_type", "extraction_method", "route_family",
    "image_type", "panel_id", "panel_count", "classification_confidence",
    "series_name", "series_color", "x_value", "y_value", "y_right_value", "z_value",
    "x_unit", "y_unit", "z_unit", "x_label", "y_label", "z_label",
    "error_bar", "significance",
    "x_axis_type", "y_axis_type", "has_dual_y",
    "scale_bar_value", "scale_bar_unit", "pixel_size",
    "object_class", "object_count", "object_area_physical",
    "object_diameter_physical", "object_circularity", "object_area_fraction",
    "band_label", "band_intensity", "band_intensity_norm",
    "molecular_weight_kda", "target_protein", "loading_control", "lane_number",
    "overall_description", "qualitative", "text_evidence",
    "confidence", "needs_review", "review_reason",
    "channel",
]


def extraction_point_to_dict(pt: ExtractionPoint) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for fn in EXTRACTION_POINT_FIELDS:
        val = getattr(pt, fn, None)
        if val is None:
            d[fn] = ""
        elif isinstance(val, bool):
            d[fn] = "true" if val else "false"
        elif isinstance(val, list):
            d[fn] = ";".join(str(v) for v in val) if val else ""
        else:
            d[fn] = val
    if pt.extra_annotations:
        import json
        try:
            d["extra_annotations"] = json.dumps(pt.extra_annotations, ensure_ascii=False)
        except Exception:
            d["extra_annotations"] = str(pt.extra_annotations)
    return d


def extraction_point_from_dict(d: dict[str, Any]) -> ExtractionPoint:
    kwargs: dict[str, Any] = {}
    for fn in EXTRACTION_POINT_FIELDS:
        val = d.get(fn, "")
        kwargs[fn] = val
    return ExtractionPoint(**kwargs)
