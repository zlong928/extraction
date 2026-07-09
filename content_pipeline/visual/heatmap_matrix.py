from __future__ import annotations

from pathlib import Path
from typing import Any


def heatmap_matrix_payload(payload: dict[str, Any], *, image_ref: str) -> tuple[list[dict[str, Any]], list[str]]:
    if "heatmap" not in str(payload.get("chart_type") or "").lower():
        return [], []

    explicit = _explicit_matrix_points(payload)
    if explicit:
        return explicit, []

    panels = _heatmap_panels(payload)
    colorbar = _dict(payload.get("colorbar") or payload.get("z_axis") or payload.get("legend"))
    if not panels:
        return [], ["heatmap_matrix_missing_panel_bbox"]
    if not colorbar:
        return [], ["heatmap_matrix_missing_colorbar"]
    if not image_ref or not Path(image_ref).is_file():
        return [], ["heatmap_matrix_missing_image"]

    try:
        from PIL import Image  # type: ignore
    except Exception:
        return [], ["heatmap_matrix_requires_pillow"]

    try:
        with Image.open(image_ref) as image:
            rgb_image = image.convert("RGB")
            width, height = rgb_image.size
            palette, palette_values = _colorbar_palette(rgb_image, colorbar, width, height)
            if not palette:
                return [], ["heatmap_matrix_colorbar_bbox_unreadable"]
            return _sample_heatmap_panels(
                image=rgb_image,
                image_width=width,
                image_height=height,
                panels=panels,
                payload=payload,
                colorbar=colorbar,
                palette=palette,
                palette_values=palette_values,
            ), []
    except Exception as exc:
        return [], [f"heatmap_matrix_failed:{type(exc).__name__}"]


def _explicit_matrix_points(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("heatmap_matrix", "matrix_points"):
        points = payload.get(key)
        if isinstance(points, dict):
            points = points.get("data_points") or points.get("points")
        if isinstance(points, list):
            normalized = [dict(point) for point in points if isinstance(point, dict) and _present(point.get("z_value") if "z_value" in point else point.get("z"))]
            if normalized:
                return normalized
    return []


def _heatmap_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("heatmap_panels") or payload.get("heatmap_regions") or payload.get("regions")
    if isinstance(raw, dict):
        raw = raw.get("panels") or raw.get("regions")
    panels = [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if panels:
        return panels
    bbox = payload.get("heatmap_bbox") or payload.get("plot_bbox")
    return [{"bbox": bbox, "series_name": payload.get("series_name") or payload.get("condition") or "heatmap"}] if bbox else []


def _sample_heatmap_panels(
    *,
    image: Any,
    image_width: int,
    image_height: int,
    panels: list[dict[str, Any]],
    payload: dict[str, Any],
    colorbar: dict[str, Any],
    palette: list[tuple[int, int, int]],
    palette_values: list[float],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    x_axis = _dict(payload.get("x_axis"))
    y_axis = _dict(payload.get("y_axis"))
    z_label = _text(colorbar.get("label") or colorbar.get("title") or colorbar.get("z_label") or payload.get("z_label"))
    z_unit = _text(colorbar.get("unit") or payload.get("z_unit"))
    scale_factor = _text(colorbar.get("scale_factor") or payload.get("scale_factor"))
    evidence_ids = payload.get("evidence_ids") if isinstance(payload.get("evidence_ids"), list) else []

    for panel in panels:
        bbox = _bbox(panel.get("bbox") or panel.get("plot_bbox") or panel, image_width, image_height)
        if bbox is None:
            continue
        left, top, right, bottom = bbox
        x_values = _axis_values(panel.get("x_values") or panel.get("x_tick_values") or x_axis.get("tick_values"), x_axis, default_count=5)
        y_values = _axis_values(panel.get("y_values") or panel.get("y_tick_values") or y_axis.get("tick_values"), y_axis, default_count=5)
        if not x_values or not y_values:
            continue
        series_name = _text(panel.get("series_name") or panel.get("series") or panel.get("label") or payload.get("series_name") or "heatmap")
        condition = _text(panel.get("condition"))
        if condition and condition not in series_name:
            series_name = f"{series_name} ({condition})"
        for y_index, y_value in enumerate(y_values):
            y_fraction = 0.5 if len(y_values) == 1 else y_index / (len(y_values) - 1)
            pixel_y = _clamp_int(round(bottom - y_fraction * (bottom - top)), top, bottom)
            for x_index, x_value in enumerate(x_values):
                x_fraction = 0.5 if len(x_values) == 1 else x_index / (len(x_values) - 1)
                pixel_x = _clamp_int(round(left + x_fraction * (right - left)), left, right)
                color = image.getpixel((pixel_x, pixel_y))
                z_value = _nearest_palette_value(color, palette, palette_values)
                points.append({
                    "series_name": series_name,
                    "point_index": len(points) + 1,
                    "x_value": x_value,
                    "x_axis_label": _text(x_axis.get("label") or panel.get("x_label")),
                    "x_unit": _text(x_axis.get("unit") or panel.get("x_unit")),
                    "y_value": y_value,
                    "y_axis_label": _text(y_axis.get("label") or panel.get("y_label")),
                    "y_unit": _text(y_axis.get("unit") or panel.get("y_unit")),
                    "z_value": z_value,
                    "z_label": z_label,
                    "z_unit": z_unit,
                    "scale_factor": scale_factor,
                    "category_label": f"grid_{y_index + 1}_{x_index + 1}",
                    "value_source": "colorbar_calibrated_pixel",
                    "confidence": 0.75,
                    "needs_verification": True,
                    "evidence_ids": evidence_ids,
                })
    return points


def _colorbar_palette(image: Any, colorbar: dict[str, Any], width: int, height: int) -> tuple[list[tuple[int, int, int]], list[float]]:
    bbox = _bbox(colorbar.get("bbox") or colorbar, width, height)
    if bbox is None:
        return [], []
    left, top, right, bottom = bbox
    orientation = _text(colorbar.get("orientation")).lower()
    if not orientation:
        orientation = "vertical" if (bottom - top) >= (right - left) else "horizontal"
    value_min, value_max = _value_range(colorbar)
    if value_min is None or value_max is None:
        return [], []

    samples = 80
    palette: list[tuple[int, int, int]] = []
    values: list[float] = []
    for index in range(samples):
        frac = index / (samples - 1)
        if orientation == "horizontal":
            x = _clamp_int(round(left + frac * (right - left)), left, right)
            y = _clamp_int(round((top + bottom) / 2), top, bottom)
            value = value_min + frac * (value_max - value_min)
        else:
            x = _clamp_int(round((left + right) / 2), left, right)
            y = _clamp_int(round(top + frac * (bottom - top)), top, bottom)
            value = value_max - frac * (value_max - value_min)
        palette.append(image.getpixel((x, y)))
        values.append(value)
    return palette, values


def _value_range(colorbar: dict[str, Any]) -> tuple[float | None, float | None]:
    ticks = [value for value in (_float(item) for item in _list(colorbar.get("tick_values") or colorbar.get("ticks"))) if value is not None]
    value_min = _float(colorbar.get("value_min") if colorbar.get("value_min") is not None else colorbar.get("min"))
    value_max = _float(colorbar.get("value_max") if colorbar.get("value_max") is not None else colorbar.get("max"))
    if ticks:
        value_min = min(ticks) if value_min is None else value_min
        value_max = max(ticks) if value_max is None else value_max
    return value_min, value_max


def _nearest_palette_value(color: tuple[int, int, int], palette: list[tuple[int, int, int]], values: list[float]) -> float:
    best_index = 0
    best_distance = float("inf")
    for index, sample in enumerate(palette):
        distance = sum((int(color[channel]) - int(sample[channel])) ** 2 for channel in range(3))
        if distance < best_distance:
            best_distance = distance
            best_index = index
    return round(values[best_index], 6)


def _axis_values(raw_values: Any, axis: dict[str, Any], *, default_count: int) -> list[float]:
    values = [value for value in (_float(item) for item in _list(raw_values)) if value is not None]
    if values:
        return sorted(dict.fromkeys(values))[:10]
    range_min = _float(axis.get("range_min") if axis.get("range_min") is not None else axis.get("min"))
    range_max = _float(axis.get("range_max") if axis.get("range_max") is not None else axis.get("max"))
    axis_range = axis.get("range")
    if isinstance(axis_range, list) and len(axis_range) >= 2:
        range_min = _float(axis_range[0]) if range_min is None else range_min
        range_max = _float(axis_range[1]) if range_max is None else range_max
    if range_min is None or range_max is None:
        return []
    count = min(default_count, 10)
    if count <= 1:
        return [range_min]
    return [round(range_min + index * (range_max - range_min) / (count - 1), 6) for index in range(count)]


def _bbox(raw: Any, width: int, height: int) -> tuple[int, int, int, int] | None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        left, top, right, bottom = raw[:4]
    elif not isinstance(raw, dict):
        return None
    elif all(key in raw for key in ("left", "top", "right", "bottom")):
        left, top, right, bottom = raw["left"], raw["top"], raw["right"], raw["bottom"]
    elif all(key in raw for key in ("x0", "y0", "x1", "y1")):
        left, top, right, bottom = raw["x0"], raw["y0"], raw["x1"], raw["y1"]
    elif all(key in raw for key in ("x", "y", "width", "height")):
        left, top = raw["x"], raw["y"]
        right, bottom = _float(left, 0.0) + _float(raw["width"], 0.0), _float(top, 0.0) + _float(raw["height"], 0.0)
    else:
        return None
    coords = [_float(value) for value in (left, top, right, bottom)]
    if any(value is None for value in coords):
        return None
    left_f, top_f, right_f, bottom_f = (float(value) for value in coords if value is not None)
    if max(abs(left_f), abs(top_f), abs(right_f), abs(bottom_f)) <= 1.0:
        left_f, right_f = left_f * width, right_f * width
        top_f, bottom_f = top_f * height, bottom_f * height
    left_i = _clamp_int(round(min(left_f, right_f)), 0, width - 1)
    right_i = _clamp_int(round(max(left_f, right_f)), 0, width - 1)
    top_i = _clamp_int(round(min(top_f, bottom_f)), 0, height - 1)
    bottom_i = _clamp_int(round(max(top_f, bottom_f)), 0, height - 1)
    if right_i <= left_i or bottom_i <= top_i:
        return None
    return left_i, top_i, right_i, bottom_i


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _present(value: Any) -> bool:
    return value not in (None, "")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))
