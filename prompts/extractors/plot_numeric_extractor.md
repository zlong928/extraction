# plot_numeric_extractor

## Goal
Extract structured evidence from plots and performance/property charts.

## Hard constraints
- Extract only values and trends explicitly readable in the current panel image, `panel_context`, or `context_entities`.
- Treat `panel_context.panel_caption`, `panel_context.panel_nearby_text`, and `panel_context.citation_context` as the allowed strong context for this crop.
- Do not use other parent-figure caption fragments as strong evidence for this panel.
- Never estimate unreadable values as exact numbers.
- If no OCR/CV/curve digitization was performed, set `digitization_performed=false`, `numeric_extraction_status="not_performed"` unless numeric values are explicitly present in text, and use `extraction_mode="qualitative_visual_trend"` or `"numeric_from_text"`.
- Caption/nearby-text numeric values can be copied only into `reported_values_from_text` with explicit source attribution; do not describe them as image-digitized curve values.
- If value is only shown in plot and uncertain, return qualitative trend text.
- If multiple units/axes exist, list each explicitly.
- If this is a multi-panel figure or crop, process only this panel/crop.
- Output must conform to `schemas/extraction_results/plot_numeric.schema.json` exactly.

## Forbidden in values_reported_in_text_or_labels
Do NOT put full caption sentences, prose descriptions, or explanation text.
Only short labels or numeric values explicitly visible on the figure itself.
✅ "97.7%", "24 h", "Before soaking", "0.8 ± 0.1"
❌ "Fluorescence intensity across a typical MHN@TA filament before and after soaking"

## Output fields
- plot_type
- x_axis
- y_axis
- series
- comparison_groups
- main_metric
- best_performing_group
- statistical_annotations
- main_result
- domain_interpretation
- uncertainty
- evidence
- uncertainty
