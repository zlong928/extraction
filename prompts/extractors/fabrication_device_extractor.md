# fabrication_device_extractor

## Goal
Extract structured evidence for fabrication workflows and device/architecture figures.

## Hard constraints
- Extract only what is directly visible or caption-declared.
- Do not infer missing methods, compositions, or exact values.
- If a numeric value appears in the image and is unreadable, report trend and avoid exact fabrication parameters.
- If caption gives explicit numeric parameters, include them and mark source as `caption` in evidence.
- If this is a multi-panel figure, process only the current panel.
- Output must conform to `schemas/extraction_results/fabrication_device.schema.json` exactly.

## Output fields
- fabrication_or_device_type
- materials
- biological_agents
- fabrication_steps
- crosslinking_or_stabilization
- device_architecture
- dimensions_or_scale
- final_form
- application_target
- uncertainty
- evidence
- uncertainty
