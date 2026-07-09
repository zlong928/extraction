# macro_sample_extractor

## Goal
Extract structured evidence for macro sample and visible appearance figures.

## Hard constraints
- Extract only directly supported visual observations and caption statements.
- Do not fabricate dimensions/conditions if absent.
- If uncertain numeric labels exist, use qualitative terms, not exact values.
- If caption has explicit readable labels, include them.
- If multi-panel, process current panel only.
- Output must conform to `schemas/extraction_results/macro_sample.schema.json` exactly.

## Output fields
- object_or_sample_type
- visible_form
- shape
- color
- surface_or_internal_pattern
- scale_bar_or_size
- condition_or_timepoint
- comparison_groups
- visible_change
- application_context
- interpretation
- uncertainty
- evidence
- uncertainty
