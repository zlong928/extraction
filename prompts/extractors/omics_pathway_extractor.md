# omics_pathway_extractor

## Goal
Extract structured evidence for omics/pathway and mechanism visuals.

## Hard constraints
- Use only explicit image/caption claims.
- Do not infer pathway directions or mechanisms.
- Use qualitative expressions when exact metrics are unreadable.
- Caption-provided significance/fold-change values can be copied.
- Process only the current panel for mixed figures.
- Output must conform to `schemas/extraction_results/omics_pathway.schema.json` exactly.

## Output fields
- analysis_type
- organism_or_cell
- experimental_conditions
- genes
- proteins_or_enzymes
- metabolites
- pathways
- upregulated_items
- downregulated_items
- fold_change_or_significance
- mechanistic_relationships
- main_biological_conclusion
- uncertainty
- evidence
- uncertainty
