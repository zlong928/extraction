from __future__ import annotations

import json


def rows_from_records(records: list, fields: list[str], limit: int = 500, image_by_panel: dict[str, str] | None = None) -> dict:
    rows = []
    for record in records[:limit]:
        if not isinstance(record, dict):
            continue
        rows.append([_cell(_record_cell(record, field, image_by_panel or {})) for field in fields])
    return {"headers": fields, "rows": rows, "total": len(records)}


def _record_cell(record: dict, field: str, image_by_panel: dict[str, str]):
    if field == "source_image":
        return image_by_panel.get(str(record.get("panel_id") or ""), "") or record.get("source_image")
    if field == "x_label":
        return record.get("x_label") or record.get("x_axis_label")
    if field == "y_label":
        return record.get("y_label") or record.get("y_axis_label")
    if field == "fact_id":
        return record.get("fact_id") or record.get("chart_point_id")
    if field == "source_phase":
        return record.get("source_phase") or ("chart_digitization" if record.get("digitization_status") else "")
    if field == "needs_review":
        return record.get("needs_review") if record.get("needs_review") is not None else record.get("needs_verification")
    return record.get(field)


def chart_fact_records(data: dict) -> list[dict]:
    records = list(data.get("chart_facts") or data.get("panel_fact_rows") or data.get("chart_points") or [])
    rebuilt_by_panel: dict[str, list[dict]] = {}
    for result in data.get("chart_digitization_results") or []:
        if not isinstance(result, dict):
            continue
        panel_id = str(result.get("panel_id") or "")
        point_records = _point_records_from_digitization_result(result)
        if panel_id and point_records:
            rebuilt_by_panel[panel_id] = point_records

    if rebuilt_by_panel:
        grouped: dict[str, list[dict]] = {}
        passthrough: list[dict] = []
        for record in records:
            if isinstance(record, dict) and record.get("panel_id"):
                grouped.setdefault(str(record.get("panel_id") or ""), []).append(record)
            else:
                passthrough.append(record)
        records = passthrough
        for panel_id, panel_records in grouped.items():
            if panel_id in rebuilt_by_panel and not any(_chart_fact_record_is_displayable(record) for record in panel_records):
                records.extend(rebuilt_by_panel[panel_id])
            else:
                records.extend(record for record in panel_records if _chart_fact_record_is_displayable(record))
    else:
        records = [record for record in records if not isinstance(record, dict) or _chart_fact_record_is_displayable(record)]

    panels_with_records = {
        str(record.get("panel_id") or "")
        for record in records
        if isinstance(record, dict) and record.get("panel_id")
    }
    for result in data.get("chart_digitization_results") or []:
        if not isinstance(result, dict):
            continue
        panel_id = str(result.get("panel_id") or "")
        if not panel_id or panel_id in panels_with_records:
            continue
        records.extend(rebuilt_by_panel.get(panel_id) or [])
    return records


def _point_records_from_digitization_result(result: dict) -> list[dict]:
    raw_output = result.get("raw_output") if isinstance(result.get("raw_output"), dict) else {}
    point_payloads = _iter_digitization_point_payloads(result.get("points") or raw_output.get("data_points") or [])
    if not point_payloads:
        point_payloads = _iter_digitization_series_points(raw_output.get("series") or [])
    if not point_payloads:
        return []

    x_axis = result.get("x_axis") if isinstance(result.get("x_axis"), dict) else raw_output.get("x_axis") if isinstance(raw_output.get("x_axis"), dict) else {}
    y_axis = result.get("y_axis") if isinstance(result.get("y_axis"), dict) else raw_output.get("y_axis") if isinstance(raw_output.get("y_axis"), dict) else {}
    records: list[dict] = []
    for index, point in enumerate(point_payloads, start=1):
        y_value = _first_present(point, "y_value", "y")
        z_value = _first_present(point, "z_value", "z")
        if y_value in (None, "") and z_value in (None, ""):
            continue
        series_name = _text(point.get("series_name") or point.get("series") or "")
        category = _text(point.get("category_label") or point.get("category") or "")
        x_axis_label = _text(point.get("x_axis_label") or point.get("x_label") or "")
        raw_x = _first_present(point, "x_value", "x")
        x_value = raw_x if raw_x not in (None, "") else category or x_axis_label
        y_axis_label = _text(point.get("y_axis_label") or point.get("y_label") or y_axis.get("label") or "")
        records.append({
            "fact_id": f"{result.get('paper_id') or ''}:{result.get('figure_id') or ''}:{result.get('panel_id') or ''}:{_safe_fact_token(series_name or 'series')}:{index}",
            "paper_id": result.get("paper_id"),
            "figure_id": result.get("figure_id"),
            "panel_id": result.get("panel_id"),
            "chart_type": result.get("chart_type") or raw_output.get("chart_type"),
            "series_name": series_name,
            "point_index": str(index),
            "x_label": x_axis_label or _text(x_axis.get("label") or ""),
            "x_unit": _text(point.get("x_unit") or x_axis.get("unit") or ""),
            "x_value": x_value,
            "y_label": y_axis_label,
            "y_unit": _text(point.get("y_unit") or point.get("unit") or y_axis.get("unit") or ""),
            "y_value": y_value,
            "z_label": _text(point.get("z_label") or point.get("z_axis_label") or point.get("value_label") or ""),
            "z_unit": _text(point.get("z_unit") or ""),
            "z_value": z_value,
            "scale_factor": _text(point.get("scale_factor") or ""),
            "category_label": category,
            "confidence": point.get("confidence") if point.get("confidence") is not None else result.get("extraction_confidence"),
            "digitization_status": result.get("digitization_status") or "digitized",
            "needs_review": point.get("needs_review") if point.get("needs_review") is not None else point.get("needs_verification", result.get("needs_verification")),
            "source_phase": "chart_digitization",
            "warnings": ";".join(str(item) for item in result.get("warnings") or [] if item),
            "value_source": point.get("value_source") or "visual_estimate",
            "evidence_ids": ";".join(str(item) for item in (point.get("evidence_ids") or result.get("evidence_ids") or []) if item),
        })
    return records


def _iter_digitization_point_payloads(value) -> list[dict]:
    points: list[dict] = []
    if not isinstance(value, list):
        return points
    for item in value:
        if not isinstance(item, dict):
            continue
        nested = item.get("data_points") or item.get("points")
        if isinstance(nested, list):
            series_name = item.get("series_name") or item.get("series") or item.get("name")
            for point in _iter_digitization_point_payloads(nested):
                if series_name and not point.get("series_name"):
                    point = {**point, "series_name": series_name}
                points.append(point)
            continue
        points.append(item)
    return points


def _iter_digitization_series_points(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    points: list[dict] = []
    for series in value:
        if not isinstance(series, dict):
            continue
        series_name = series.get("series_name") or series.get("name")
        for point in _iter_digitization_point_payloads(series.get("data_points") or series.get("points") or []):
            if series_name and not point.get("series_name"):
                point = {**point, "series_name": series_name}
            points.append(point)
    return points


def _chart_fact_record_has_point_values(record: dict) -> bool:
    point_index = str(record.get("point_index") or "").strip()
    if not point_index or point_index == "axis":
        return False
    return any(record.get(field) not in (None, "") for field in ("x_value", "y_value", "z_value"))


def _chart_fact_record_is_displayable(record: dict) -> bool:
    if _chart_fact_record_has_point_values(record):
        return True
    status = str(record.get("digitization_status") or "").strip().lower()
    return status in {
        "failed",
        "too_low_resolution",
        "axis_unreadable",
        "legend_unreadable",
        "no_chart_detected",
    }


def _first_present(record: dict, *fields: str):
    for field in fields:
        if field in record and record[field] not in (None, ""):
            return record[field]
    return None


def _safe_fact_token(value: str) -> str:
    return "_".join(str(value or "series").strip().split()) or "series"


def _text(value) -> str:
    return str(value or "").strip()


def panel_image_map(data: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for packet in data.get("evidence_packets") or []:
        if not isinstance(packet, dict):
            continue
        panel_id = str(packet.get("panel_id") or "")
        image_ref = str(packet.get("image_ref") or "")
        if panel_id and image_ref:
            mapping[panel_id] = image_ref.split("/")[-1]
    return mapping


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
