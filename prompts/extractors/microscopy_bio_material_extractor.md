# microscopy_bio_material_extractor

## Goal
Extract structured evidence from microscopy and bio-material composite images.

## Hard constraints
- Extract only explicit visual and caption-based observations.
- Do not infer unshown labels, units, or quantified outcomes.
- If exact numbers are not readable in image, keep qualitative wording.
- If caption provides numeric labels or scales, include them exactly.
- If multi-panel, process only this panel.
- Output must conform to `schemas/extraction_results/microscopy_bio_material.schema.json` exactly.

## Output fields
- microscopy_type
- target
- biological_agents
- material_matrix
- staining_or_signal
- scale_bar
- observed_structures
- cell_or_biomass_distribution
- viability_or_growth_evidence
- porosity_or_network_features
- timepoint_or_condition
- comparison_groups
- main_visual_conclusion
- quantitative_labels_visible
- uncertainty
- evidence
- uncertainty
