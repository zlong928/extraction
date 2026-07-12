from __future__ import annotations
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any



CANONICAL_METRIC_CATEGORIES: tuple[str, ...] = (
    "performance_metric",
    "material_property",
    "biological_activity",
    "treatment_condition",
    "other_benchmark_metric",
    "material_structure_metric",
    "mechanical_rheological_metric",
    "biological_activity_metric",
    "degradation_treatment_metric",
    "gas_energy_carbon_metric",
    "product_yield_metric",
    "qualitative_observation_metric",
)


def allowed_metric_categories() -> set[str]:
    return set(CANONICAL_METRIC_CATEGORIES)


def canonical_metric_category(category: str, *, context: Any = "") -> str:
    text = str(category or "").strip()
    allowed = allowed_metric_categories()
    if text in allowed:
        return text
    normalized = _normalize(" ".join([text, _context_text(context)]))
    inferred = _infer_category(normalized, allowed)
    return inferred or "other_benchmark_metric"


def normalize_target_metric_group(group: dict[str, Any]) -> dict[str, Any]:
    out = dict(group)
    original_category = str(out.get("metric_category") or "").strip()
    canonical = canonical_metric_category(original_category, context=out)
    if original_category:
        out.setdefault("domain_metric_category", original_category)
    out["metric_category"] = canonical
    return out


def target_group_index(plan: Any) -> dict[str, dict[str, Any]]:
    groups = getattr(plan, "target_metric_groups", []) or []
    return {
        str(group.get("group_id")): normalize_target_metric_group(group)
        for group in groups
        if isinstance(group, dict) and group.get("group_id")
    }


def canonicalize_metric_payload(payload: dict[str, Any], *, target_group: dict[str, Any] | None, fallback_category: str = "") -> dict[str, Any]:
    out = dict(payload)
    group = normalize_target_metric_group(target_group or {}) if target_group else None
    original = str(out.get("metric_category") or fallback_category or "").strip()
    if group and group.get("metric_category"):
        if original and original != group.get("metric_category"):
            out.setdefault("domain_metric_category", original)
        out["metric_category"] = str(group.get("metric_category") or "")
        if group.get("application_task"):
            out["application_task"] = str(group.get("application_task") or "")
        if group.get("assay"):
            out["assay"] = str(group.get("assay") or "")
    else:
        canonical = canonical_metric_category(original, context=out)
        if original and original != canonical:
            out.setdefault("domain_metric_category", original)
        out["metric_category"] = canonical
    return out


def _infer_category(text: str, allowed: set[str]) -> str:
    scored: list[tuple[int, str]] = []
    for category, terms in _CATEGORY_TERMS.items():
        if category not in allowed:
            continue
        score = sum(1 for term in terms if term in text)
        if score:
            scored.append((score, category))
    if not scored:
        return ""
    scored.sort(reverse=True)
    return scored[0][1]


def _context_text(value: Any) -> str:
    if isinstance(value, dict):
        chunks: list[str] = []
        for key, item in value.items():
            chunks.append(str(key))
            if isinstance(item, (list, tuple, set)):
                chunks.extend(str(x) for x in item)
            elif isinstance(item, dict):
                chunks.append(_context_text(item))
            else:
                chunks.append(str(item))
        return " ".join(chunks)
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value)
    return str(value or "")


def _normalize(value: str) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").replace(".", " ").replace("/", " ").split())


_CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "gas_energy_carbon_metric": (
        "carbon", "co2", "co 2", "gas", "capture", "uptake", "fixation", "ppm", "ppb", "energy",
    ),
    "mechanical_rheological_metric": (
        "mechanical", "rheolog", "compress", "strength", "stress", "strain", "modulus", "stiffness", "load", "force", "mpa", "kpa", "gpa", "young",
    ),
    "biological_activity_metric": (
        "bio", "biological", "bacteria", "cell", "microbial", "growth", "cfu", "mpn", "od600", "optical density", "biomass", "fluorescence", "photosynthesis", "chlorophyll",
    ),
    "material_structure_metric": (
        "material", "structure", "porosity", "pore", "water", "uptake", "retention", "loss", "wicking", "swelling", "density", "scaffold", "ceramic", "hydrogel",
    ),
    "degradation_treatment_metric": (
        "degradation", "degrade", "treatment", "soaking", "leaching", "retention", "release", "stability",
    ),
    "product_yield_metric": (
        "yield", "product", "production", "conversion", "titer", "acetate", "alcohol", "metabolite",
    ),
    "qualitative_observation_metric": (
        "visible", "observed", "presence", "absence", "qualitative", "morphology", "image observation",
    ),
    "performance_metric": (
        "performance", "response", "recovery", "limit of detection", "lod", "signal", "detection",
    ),
    "material_property": (
        "property", "material property", "matrix", "composition",
    ),
    "biological_activity": (
        "biological activity", "activity",
    ),
    "treatment_condition": (
        "condition", "temperature", "time", "dose", "concentration", "ph", "treatment",
    ),
}


@dataclass(slots=True)
class ProjectedMetricRow:
    """A single metric row ready for CSV projection."""
    paper_id: str
    figure_id: str
    panel_id: str
    material_or_matrix: str
    biological_agent: str
    application_task: str
    assay: str
    metric_name: str
    metric_category: str
    target: str
    condition: str
    comparison: str
    value: str
    value_min: str | None = None
    value_max: str | None = None
    unit: str = ""
    value_type: str = ""
    direction: str = ""
    source_panel_id: str = ""
    evidence_text: str = ""
    evidence_level: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    validation_status: str = ""
    release_status: str = ""
    rejection_reason: str = ""
    review_reason: str = ""
    matched_target_group_id: str = ""
    extraction_source: str = ""
    needs_digitization_verification: bool = False
    verifier_status: str = ""
    verifier_reason: str = ""
    raw_output: dict[str, Any] = field(default_factory=dict)
    chart_point_id: str = ""
    extraction_id: str = ""
    source_row_index: int = 0
