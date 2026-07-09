# quantitative_metric_extractor

Return JSON matching the attached schema.

The metric_rows array is the only source for the CSV. It must contain only compact benchmark metric rows with these fields: metric_name, metric_category, target, condition, comparison, value, value_min, value_max, unit, value_type, direction.

Do not put identifiers, file paths, evidence, confidence, axis labels, scale bars, image type, prose summaries, or cross-panel context values into metric_rows.

Use review for human explanation: what the image shows, how to read it, why rows are kept, what was excluded, context values not written to CSV, and limitations.

Allowed value_type values: exact_numeric, approximate_numeric, ordinal, categorical, qualitative_trend.

Use qualitative_trend only when the trend itself is the benchmark metric. Use ordinal for visual biological proxies such as abundance, coverage, density, enrichment, depletion, or viability level. Use categorical for present, absent, detected, not detected, retained, or similar.

## Examples

### ✅ GOOD: qualitative trend
Input: caption says "similar intensity before and after"
Output metric_rows entry:
{"metric_name": "intensity retention", "metric_category": "intensity_retention",
 "value": "similar", "value_type": "qualitative_trend", ...}

### ❌ BAD: caption sentence as value (DO NOT DO THIS)
{"metric_name": "Fluorescence intensity",
 "value": "Fluorescence intensity across a typical MHN@TA filament",
 "value_type": "categorical"}
This will be REJECTED. value must be a compact benchmark value.

Return JSON only.
