# simulation_modeling_extractor

## Goal
Extract structured evidence from simulation and molecular modeling visuals.

## Hard constraints
- Extract only explicit simulation features, conditions, and outputs.
- Do not invent unseen model assumptions.
- Keep uncertain numeric values as qualitative trend text.
- Do not over-interpret color scales; use explicit text when available.
- Process only current panel for mixed figures.
- Output must conform to `schemas/extraction_results/simulation_modeling.schema.json` exactly.

## Output fields
- simulation_type
- modeled_process
- system_or_domain
- variables
- conditions
- timepoints
- colorbar_or_scale
- spatial_or_molecular_features
- comparison_groups
- reported_parameters
- main_simulation_result
- mechanistic_claim_supported
- uncertainty
- evidence
- uncertainty
