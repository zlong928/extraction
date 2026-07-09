from __future__ import annotations

from typing import Any


CHART_KEYWORDS = {"chart", "plot", "graph", "scatter", "line_plot", "bar_chart", "histogram"}


def normalize_panel_type(value: Any) -> str:
    text = str(value or "").strip()
    return text


def is_numeric_chart(panel_type: str) -> bool:
    lower = panel_type.lower().strip()
    return any(kw in lower for kw in CHART_KEYWORDS)
