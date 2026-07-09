# chemical_characterization_extractor

## Goal
Extract explicit evidence from chemical characterization figures.

## Hard constraints
- Only extract what is directly readable in spectrum/photo/text.
- Do not infer species identities or reaction mechanisms.
- If exact numeric labels are unreadable, report qualitative trend.
- Include exact caption values when explicit.
- Process only the current panel for composite figures.
- Output must conform to `schemas/extraction_results/chemical_characterization.schema.json` exactly.

## Output fields
- method
- target_material_or_compound
- sample_conditions
- detected_peaks_or_signals
- elemental_or_phase_information
- reaction_or_degradation_products
- supports_claim
- quantitative_values
- main_conclusion
- uncertainty
- evidence
- uncertainty
