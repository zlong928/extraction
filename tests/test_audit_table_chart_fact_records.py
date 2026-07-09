from __future__ import annotations

from app.services.audit_table_service import chart_fact_records, panel_image_map, rows_from_records


def test_chart_fact_records_rebuilds_stale_axis_placeholder_from_raw_digitization_points() -> None:
    data = {
        "chart_facts": [
            {
                "fact_id": "paper-7:fig-3:fig-3-l:axis",
                "paper_id": "paper-7",
                "figure_id": "fig-3",
                "panel_id": "fig-3-l",
                "chart_type": "scatter_plot",
                "point_index": "",
                "x_label": "",
                "x_value": "",
                "y_label": "Toughness",
                "y_unit": "J m^-3",
                "y_value": "",
                "digitization_status": "partially_digitized",
            }
        ],
        "chart_digitization_results": [
            {
                "paper_id": "paper-7",
                "figure_id": "fig-3",
                "panel_id": "fig-3-l",
                "chart_type": "scatter_plot",
                "digitization_status": "partially_digitized",
                "needs_verification": True,
                "raw_output": {
                    "data_points": [
                        {
                            "series_name": "Abiotic",
                            "point_index": 1,
                            "x_value": None,
                            "x_axis_label": "Day 2",
                            "y_value": 1800,
                            "y_axis_label": "Toughness",
                            "y_unit": "J m^-3",
                            "confidence": 0.7,
                            "needs_verification": True,
                            "evidence_ids": ["ev-visual"],
                        },
                        {
                            "series_name": "Abiotic",
                            "point_index": 2,
                            "x_value": None,
                            "x_axis_label": "Day 30",
                            "y_value": 1200,
                            "y_axis_label": "Toughness",
                            "y_unit": "J m^-3",
                            "confidence": 0.7,
                            "needs_verification": True,
                            "evidence_ids": ["ev-visual"],
                        },
                    ]
                },
            }
        ],
    }

    records = chart_fact_records(data)

    assert len(records) == 2
    assert [record["x_value"] for record in records] == ["Day 2", "Day 30"]
    assert [record["y_value"] for record in records] == [1800, 1200]
    assert all(record["point_index"] for record in records)


def test_chart_fact_records_preserves_existing_point_rows() -> None:
    data = {
        "chart_facts": [
            {
                "fact_id": "existing",
                "paper_id": "paper-7",
                "figure_id": "fig-3",
                "panel_id": "fig-3-l",
                "chart_type": "scatter_plot",
                "series_name": "Abiotic",
                "point_index": "1",
                "x_value": "Day 2",
                "y_value": "1800",
                "digitization_status": "partially_digitized",
            }
        ],
        "chart_digitization_results": [
            {
                "paper_id": "paper-7",
                "figure_id": "fig-3",
                "panel_id": "fig-3-l",
                "chart_type": "scatter_plot",
                "digitization_status": "partially_digitized",
                "raw_output": {
                    "data_points": [
                        {
                            "series_name": "Abiotic",
                            "point_index": 1,
                            "x_axis_label": "Day 30",
                            "y_value": 1200,
                        }
                    ]
                },
            }
        ],
    }

    records = chart_fact_records(data)

    assert records == data["chart_facts"]


def test_chart_fact_records_rebuilds_heatmap_xyz_points() -> None:
    data = {
        "chart_facts": [
            {
                "fact_id": "axis",
                "paper_id": "paper-4",
                "figure_id": "fig-4",
                "panel_id": "fig-4-k",
                "chart_type": "heatmap",
                "point_index": "",
                "digitization_status": "partially_digitized",
            }
        ],
        "chart_digitization_results": [
            {
                "paper_id": "paper-4",
                "figure_id": "fig-4",
                "panel_id": "fig-4-k",
                "chart_type": "heatmap",
                "digitization_status": "digitized",
                "raw_output": {
                    "data_points": [
                        {
                            "series_name": "MHN@TA (12 h)",
                            "x_value": 1.25,
                            "x_axis_label": "Distance",
                            "x_unit": "mm",
                            "y_value": 0.0,
                            "y_axis_label": "Distance",
                            "y_unit": "mm",
                            "z_value": 0.3,
                            "z_label": "Concentration",
                            "z_unit": "mol m^-3",
                            "scale_factor": "1e-5",
                            "category_label": "center",
                        }
                    ]
                },
            }
        ],
    }

    records = chart_fact_records(data)

    assert len(records) == 1
    assert records[0]["y_value"] == 0.0
    assert records[0]["z_value"] == 0.3
    assert records[0]["z_label"] == "Concentration"
    assert records[0]["category_label"] == "center"


def test_fig_4_k_heatmap_candidates_audit_table_exports_llm_candidate_fields() -> None:
    data = {
        "evidence_packets": [
            {
                "paper_id": "paper-4",
                "figure_id": "fig-4",
                "panel_id": "fig-4-k",
                "image_ref": "/tmp/extracted/images/fig-4-k.png",
            }
        ],
        "heatmap_candidates": [
            {
                "candidate_id": "paper-4:fig-4:fig-4-k:heatmap:1",
                "figure_id": "fig-4",
                "panel_id": "fig-4-k",
                "metric_name": "concentration distribution pattern",
                "series": "SM",
                "condition": "1.0 h",
                "value": "nearly uniform high concentration",
                "evidence_type": "heatmap_pattern",
                "confidence": 0.8,
                "source_phase": "heatmap_candidate",
            },
            {
                "candidate_id": "paper-4:fig-4:fig-4-k:heatmap:3",
                "figure_id": "fig-4",
                "panel_id": "fig-4-k",
                "metric_name": "center concentration",
                "series": "MHN@TA",
                "condition": "12 h",
                "value_min": "0.2",
                "value_max": "0.4",
                "unit": "mol m^-3",
                "scale_factor": "1e-5",
                "evidence_type": "heatmap_visual_estimate",
                "confidence": 0.6,
                "needs_review": True,
                "source_phase": "heatmap_candidate",
            },
        ],
    }

    table = rows_from_records(data["heatmap_candidates"], [
        "source_image", "candidate_id", "figure_id", "panel_id", "metric_name", "series", "condition",
        "value", "value_min", "value_max", "unit", "scale_factor", "evidence_type", "confidence",
        "needs_review", "source_phase",
    ], image_by_panel=panel_image_map(data))

    headers = table["headers"]
    rows = table["rows"]
    assert table["total"] == 2
    assert rows[0][headers.index("source_image")] == "fig-4-k.png"
    assert rows[0][headers.index("panel_id")] == "fig-4-k"
    assert rows[0][headers.index("metric_name")] == "concentration distribution pattern"
    assert rows[1][headers.index("metric_name")] == "center concentration"
    assert rows[1][headers.index("value_min")] == "0.2"
    assert rows[1][headers.index("value_max")] == "0.4"
    assert rows[1][headers.index("source_phase")] == "heatmap_candidate"


def test_chart_fact_records_preserves_terminal_status_rows_without_raw_points() -> None:
    data = {
        "chart_facts": [
            {
                "fact_id": "failed",
                "paper_id": "paper-6",
                "figure_id": "fig-4",
                "panel_id": "fig-4-b",
                "chart_type": "chart",
                "point_index": "",
                "x_value": "",
                "y_value": "",
                "digitization_status": "failed",
                "errors": "LLM phase failed",
            },
            {
                "fact_id": "no-chart",
                "paper_id": "paper-6",
                "figure_id": "fig-4",
                "panel_id": "fig-4-a",
                "chart_type": "image",
                "point_index": "",
                "x_value": "",
                "y_value": "",
                "digitization_status": "no_chart_detected",
            },
        ],
        "chart_digitization_results": [
            {
                "paper_id": "paper-6",
                "figure_id": "fig-4",
                "panel_id": "fig-4-a",
                "chart_type": "image",
                "digitization_status": "no_chart_detected",
                "raw_output": {"data_points": []},
            }
        ],
    }

    records = chart_fact_records(data)

    assert records == data["chart_facts"]
