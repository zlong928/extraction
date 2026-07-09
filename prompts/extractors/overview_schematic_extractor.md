# overview_schematic_extractor

## Goal
Extract structured evidence for schematic/overview figures only.

## Hard constraints
- Extract only information explicitly visible in the image or directly stated in caption/context.
- Do not infer missing experimental conditions, exact numeric values, or causal claims.
- If a numeric value appears in the image and is unreadable, report it as `qualitative_trend` and never fabricate exact values.
- If caption provides explicit values, copy those values exactly into extracted fields.
- Every conclusion must include evidence text fragments.
- If this is a multi-panel figure, process only this panel in isolation.
- Output must conform to `schemas/extraction_results/overview_schematic.schema.json` exactly.

## Output fields
- system_name
- scientific_goal
- input_entities
- biological_agents
- material_components
- device_or_structure_components
- process_flow
- functional_outputs
- claimed_advantages
- mechanism_keywords
- extracted_claim
- limitations_or_uncertainty
- evidence
- uncertainty
