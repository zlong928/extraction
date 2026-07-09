# Benchmark Ontology V1 - Runtime Knowledge Model

## Scope

BENCHMARK_ONTOLOGY_V1 is the stable runtime knowledge model for the content pipeline. It is not a web-search layer and it is not a loose vocabulary. It defines the benchmark universe, target groups, assay-to-metric contracts, evidence permissions, rejection rules, and the handoff path for unknown concepts.

Runtime Pipeline:

- reads only this stable ontology file;
- does not call web search, Tavily, OBO, EFO, OBI, or external literature services;
- uses this model for Paper Benchmark Plan, panel target matching, metric extraction, verification, CSV, audit, and review.

Offline Ontology Builder:

- may use web search, including the project `.env` `TAVILY_API_KEY`;
- may use OBO, EFO, OBI, literature, and project corpus evidence;
- produces proposed ontology updates only;
- never mutates this runtime ontology without review.

## Layer 1: Domain Layer

Purpose: classify the paper into the benchmark universe before extraction.

Each domain entry contains: domain_id, aliases, materials, biological_agents, typical_applications, typical_assays, typical_evidence_shapes, not_enough_for_metric_examples.

### living_materials

- domain_id: living_materials
- aliases: engineered living materials, microbial living materials, living functional materials
- materials: hydrogel, porous_ceramic, scaffold, bioink, polymer_network, film, monolith
- biological_agents: bacteria, engineered_bacteria, cyanobacteria, microalgae, microbial_consortium
- typical_applications: microbial_growth_support, gas_sensing, carbon_capture, wastewater_treatment, biological_activity
- typical_assays: OD600_growth_assay, CFU_assay, fluorescence_microscopy, gas_sensing_response_assay, CO2_capture_assay
- typical_evidence_shapes: line_plot, bar_plot, fluorescence_image, microscopy_image, table, caption_text
- not_enough_for_metric_examples: organism name in schematic; green color in photo; conceptual living material label

### porous_ceramics

- domain_id: porous_ceramics
- aliases: living porous ceramic, hierarchical porous ceramic, clay ceramic scaffold
- materials: porous_ceramic, hierarchical_porous_ceramic, clay_ceramic, ceramic_scaffold
- biological_agents: E_coli, cyanobacteria, ureolytic_bacteria
- typical_applications: water_transport, mechanical_reinforcement, microbial_growth_support, carbon_capture, gas_sensing
- typical_assays: water_uptake_assay, evaporation_or_retention_assay, SEM_or_morphology, compression_test, gas_sensing_response_assay
- typical_evidence_shapes: line_plot, stress_strain_plot, bar_plot, SEM_image, schematic
- not_enough_for_metric_examples: pore visible in SEM without quantification; arrow showing water wicking; gas molecule icons

### hydrogels

- domain_id: hydrogels
- aliases: microbial hydrogel, double network hydrogel, retrievable hydrogel network
- materials: alginate_hydrogel, PEGDA_hydrogel, gelatin_hydrogel, microbial_hydrogel_network, retrievable_hydrogel_network
- biological_agents: bacteria, microalgae, Chlamydomonas_reinhardtii, microbial_consortium
- typical_applications: hydrogel_swelling_retention, leakage_or_retention, microbial_growth_support, antibiotic_or_pollutant_degradation
- typical_assays: swelling_assay, leakage_or_retention_assay, rheology, fluorescence_microscopy, OD600_growth_assay
- typical_evidence_shapes: bar_plot, line_plot, fluorescence_image, representative_photo, table
- not_enough_for_metric_examples: hydrogel photo without scale or value; "retrievable" label without retention/leakage measurement

### microbial_scaffolds

- domain_id: microbial_scaffolds
- aliases: printed microbial scaffold, biohybrid scaffold, bacteria-loaded scaffold
- materials: scaffold, lattice, bioink, printed_living_material
- biological_agents: E_coli, Pseudomonas_putida, Acetobacter_xylinum, ureolytic_bacteria
- typical_applications: microbial_growth_support, mechanical_reinforcement, biomineralization_or_biocementation
- typical_assays: fluorescence_microscopy, CFU_assay, compression_test, biomineralization_assay
- typical_evidence_shapes: microscopy_image, fluorescence_image, bar_plot, stress_strain_plot, table
- not_enough_for_metric_examples: cell presence in image without measured density, OD600, CFU, or allowed qualitative metric

### microalgae_or_photosynthetic_systems

- domain_id: microalgae_or_photosynthetic_systems
- aliases: photosynthetic living material, microalgae hydrogel, cyanobacteria material
- materials: hydrogel, film, bead, photosynthetic_living_material
- biological_agents: microalgae, Chlamydomonas_reinhardtii, cyanobacteria, Synechococcus_PCC_7002
- typical_applications: carbon_capture, microalgae_activity_or_retention, pollutant_treatment
- typical_assays: chlorophyll_proxy_assay, CO2_capture_assay, fluorescence_microscopy, leakage_or_retention_assay
- typical_evidence_shapes: line_plot, bar_plot, fluorescence_image, table, caption_text
- not_enough_for_metric_examples: algae shown in schematic; chloroplast icon; green material photo without chlorophyll or biomass measurement

### biohybrid_materials

- domain_id: biohybrid_materials
- aliases: biohybrid living material, engineered cell-material hybrid
- materials: polymer_network, hydrogel, ceramic, scaffold
- biological_agents: engineered_bacteria, microalgae, microbial_consortium
- typical_applications: gas_sensing, product_yield, biological_activity, stress_tolerance
- typical_assays: gas_sensing_response_assay, chromatogram, fluorescence_reporter_assay, live_dead_staining
- typical_evidence_shapes: line_plot, bar_plot, chromatogram, fluorescence_image, table
- not_enough_for_metric_examples: pathway diagram; genetic circuit icon; named reporter without signal value

### biomineralized_materials

- domain_id: biomineralized_materials
- aliases: MICP material, biocemented scaffold, carbonate-mineralized material
- materials: scaffold, ceramic, hydrogel, biomineralized_matrix
- biological_agents: ureolytic_bacteria, Sporosarcina_pasteurii, microbial_consortium
- typical_applications: biomineralization_or_biocementation, mechanical_reinforcement, carbon_capture
- typical_assays: biomineralization_assay, calcium_carbonate_quantification, XRD, FTIR, compression_test
- typical_evidence_shapes: bar_plot, XRD_pattern, FTIR_spectrum, SEM_image, stress_strain_plot
- not_enough_for_metric_examples: precipitate visible in SEM without carbonate quantification; mineralization schematic

## Layer 2: Benchmark Intent Layer

Purpose: define what the paper is comparing.

Each intent contains: intent_id, benchmark_goal, database_value, comparison_target, required_target_metric_groups, typical_positive_evidence, not_a_benchmark_metric_examples.

### water_transport

- intent_id: water_transport
- benchmark_goal: compare water uptake, water loss, evaporation, or retention across material designs or conditions
- database_value: true
- comparison_target: material, geometry, time, treatment, humidity condition
- required_target_metric_groups: water_transport.water_uptake
- typical_positive_evidence: plot/table/caption with water_uptake, water_loss, evaporation_loss, or water_retention
- not_a_benchmark_metric_examples: water arrows, wicking schematic, wet-looking sample photo

### microbial_growth_support

- intent_id: microbial_growth_support
- benchmark_goal: compare microbial growth, viability, colonization, biomass, or reporter activity supported by a material
- database_value: true
- comparison_target: material, organism, condition, time, nutrient supply
- required_target_metric_groups: microbial_growth_support.growth_activity; microbial_growth_support.biomass_accumulation
- typical_positive_evidence: OD600 curve, CFU/MPN table, fluorescence intensity plot, biomass or chlorophyll proxy
- not_a_benchmark_metric_examples: organism icon, qualitative green image without quantitative support

### mechanical_reinforcement

- intent_id: mechanical_reinforcement
- benchmark_goal: compare mechanical strength, modulus, load, or mineral reinforcement across materials or treatments
- database_value: true
- comparison_target: material, mineralization condition, strain, treatment
- required_target_metric_groups: mechanical_reinforcement.compressive_test; mechanical_rheology
- typical_positive_evidence: compression curve, stress-strain plot, modulus table, carbonate fraction tied to reinforcement
- not_a_benchmark_metric_examples: sample photo, SEM texture alone

### carbon_capture

- intent_id: carbon_capture
- benchmark_goal: compare CO2 uptake, fixation, capture rate, capture efficiency, biomass proxy, or carbonate storage across designs or conditions
- database_value: true
- comparison_target: material, organism, treatment, condition, time
- required_target_metric_groups: carbon_capture.co2_capture
- typical_positive_evidence: CO2 uptake plot, carbon capture efficiency table, chlorophyll/biomass proxy linked to capture, carbonate content plot
- not_a_benchmark_metric_examples: schematic CO2 arrows, conceptual application labels, organism presence without measured capture signal

### gas_sensing

- intent_id: gas_sensing
- benchmark_goal: compare gas response, reporter signal, response time, or product concentration across gases, strains, or material formats
- database_value: true
- comparison_target: gas, concentration, strain, material, time
- required_target_metric_groups: gas_sensing.response
- typical_positive_evidence: response curve, reporter intensity plot, chromatogram, product concentration table
- not_a_benchmark_metric_examples: gas molecule icon, genetic circuit schematic, label "formaldehyde sensing"

### hydrogel_swelling_retention

- intent_id: hydrogel_swelling_retention
- benchmark_goal: compare swelling, deswelling, retention, leakage, or recovery across hydrogel designs and cycles
- database_value: true
- comparison_target: hydrogel composition, time, cycle, stress condition
- required_target_metric_groups: hydrogel_swelling_retention
- typical_positive_evidence: swelling ratio plot, water retention table, leakage percentage, recovery curve
- not_a_benchmark_metric_examples: hydrogel photograph without measured ratio or retention

### microalgae_activity_or_retention

- intent_id: microalgae_activity_or_retention
- benchmark_goal: compare photosynthetic activity, chlorophyll proxy, biomass, leakage, retention, or survival of microalgae systems
- database_value: true
- comparison_target: material, time, pollutant, stress, cycle
- required_target_metric_groups: microalgae_activity_or_retention
- typical_positive_evidence: chlorophyll fluorescence, biomass trend, retained cell fraction, leakage percentage
- not_a_benchmark_metric_examples: green color or algae icon

### biomineralization_or_biocementation

- intent_id: biomineralization_or_biocementation
- benchmark_goal: compare carbonate precipitation, biomineral content, cementation, or reinforcement due to microbial mineralization
- database_value: true
- comparison_target: strain, scaffold, ion concentration, incubation time
- required_target_metric_groups: biomineralization_or_biocementation
- typical_positive_evidence: calcium carbonate content, carbonate fraction, XRD/FTIR identified mineral with quantitative context
- not_a_benchmark_metric_examples: precipitate visible without measurement, mineralization schematic

### antibacterial_or_biological_activity

- intent_id: antibacterial_or_biological_activity
- benchmark_goal: compare antibacterial effect or biological activity using measured inhibition, viability, degradation, or activity outcomes
- database_value: true
- comparison_target: material, organism, dose, treatment, time
- required_target_metric_groups: antibacterial_or_biological_activity
- typical_positive_evidence: inhibition zone, survival ratio, activity percentage, degradation rate
- not_a_benchmark_metric_examples: pathogen icon, "antibacterial" text label without measured outcome

## Layer 3: Experiment / Assay Layer

Purpose: define what each experiment can and cannot output.

Each assay contains: assay_id, aliases, input_materials, biological_agents, outputs_allowed, outputs_forbidden, typical_evidence_shapes, valid_units, instruments_or_methods, conditions, digitization_policy, common_failure_modes.

### water_uptake_assay

- aliases: wicking assay, capillary water uptake, absorption assay
- input_materials: porous_ceramic, hydrogel, scaffold
- biological_agents: optional
- outputs_allowed: water_uptake, water_loss, water_retention
- outputs_forbidden: OD600, compressive_strength, CO2_capture_rate
- typical_evidence_shapes: line_plot, bar_plot, table, caption_text
- valid_units: g, mg, %, g/g, mg/g
- instruments_or_methods: balance, gravimetric measurement
- conditions: time, humidity, material geometry
- digitization_policy: visual plot values require needs_digitization_verification=true
- common_failure_modes: treating water arrows as data

### evaporation_or_retention_assay

- aliases: evaporation assay, drying assay, retention assay
- outputs_allowed: water_loss, evaporation_loss, water_retention
- outputs_forbidden: biomass_accumulation, gas_response
- typical_evidence_shapes: line_plot, bar_plot, table
- valid_units: %, g, mg, h, min
- digitization_policy: visual plot values require verification
- common_failure_modes: converting wet/dry photos into exact values

### OD600_growth_assay

- aliases: optical density, OD600 curve
- outputs_allowed: OD600, growth_rate
- outputs_forbidden: CFU, compressive_strength, CO2_capture_rate
- typical_evidence_shapes: line_plot, table, caption_text
- valid_units: OD600, a.u., 1/h
- digitization_policy: visual OD curves require verification
- common_failure_modes: using OD label without value

### CFU_assay

- aliases: colony forming unit, plate count
- outputs_allowed: CFU, CFU_per_mL, viability_count
- outputs_forbidden: OD600, fluorescence_intensity unless separately measured
- typical_evidence_shapes: bar_plot, table, caption_text
- valid_units: CFU, CFU/mL, log CFU/mL
- digitization_policy: bar values need verification unless exact text/table exists
- common_failure_modes: colony photo treated as count without stated count

### MPN_assay

- aliases: most probable number
- outputs_allowed: MPN, MPN_per_mL
- outputs_forbidden: CFU, OD600 unless independently measured
- typical_evidence_shapes: table, bar_plot
- valid_units: MPN/mL, cells/mL
- digitization_policy: table exact preferred
- common_failure_modes: replacing MPN with generic cell_density without support

### fluorescence_microscopy

- aliases: GFP image, confocal fluorescence, chlorophyll fluorescence image
- outputs_allowed: fluorescence_intensity, microbial_colonization, chlorophyll_proxy only when quantified or explicitly allowed qualitative/categorical metric
- outputs_forbidden: OD600, CFU, water_uptake, compressive_strength
- typical_evidence_shapes: fluorescence_image, microscopy_image, bar_plot, caption_text
- valid_units: a.u., %, categorical
- digitization_policy: image-only qualitative observation is not numeric digitization
- common_failure_modes: turning visible fluorescence into present metric when panel target does not allow it

### SEM_or_morphology

- aliases: SEM, morphology, microstructure image
- outputs_allowed: porosity, morphology, colonization, precipitate presence as supporting observation
- outputs_forbidden: compressive_strength, water_uptake, CO2_capture_rate, OD600
- typical_evidence_shapes: SEM_image, microscopy_image, representative_photo
- valid_units: um, nm, %, categorical only when allowed
- digitization_policy: scale-bar observations are supporting unless metric contract permits them
- common_failure_modes: extracting benchmark metrics from morphology alone

### compression_test

- aliases: compressive test, uniaxial compression, compressive stress
- outputs_allowed: compressive_strength, compressive_modulus, peak_load, stress_at_failure
- outputs_forbidden: water_uptake, biological_growth, CO2_capture_rate
- typical_evidence_shapes: stress_strain_plot, bar_plot, box_plot, table, caption_text
- valid_units: Pa, kPa, MPa, N
- digitization_policy: visually read stress-strain or bars require verification
- common_failure_modes: confusing peak_load with strength

### stress_strain_test

- aliases: mechanical test, strain curve
- outputs_allowed: compressive_strength, compressive_modulus, stress_at_failure, strain_at_failure
- outputs_forbidden: water_retention, CFU, gas_response
- typical_evidence_shapes: stress_strain_plot, line_plot, table
- valid_units: Pa, kPa, MPa, %, strain
- digitization_policy: visual estimates require verification
- common_failure_modes: treating axis title as value

### CO2_capture_assay

- aliases: CO2 uptake, CO2 fixation, carbon sequestration assay
- outputs_allowed: CO2_capture_rate, CO2_uptake, carbon_capture_efficiency, biomass_proxy, chlorophyll_proxy, carbonate_content
- outputs_forbidden: gas_response, OD600 unless specifically growth proxy target, compressive_strength
- typical_evidence_shapes: line_plot, bar_plot, table, caption_text
- valid_units: %, mmol, umol/m2/s, mg, mg/g, a.u.
- digitization_policy: plot-derived values require verification
- common_failure_modes: extracting CO2 arrows as measured capture

### gas_sensing_response_assay

- aliases: response assay, reporter assay, volatile sensing, gas exposure
- outputs_allowed: gas_response, reporter_signal, response_intensity, response_time, product_concentration, isoamyl_acetate_concentration, isoamyl_alcohol_concentration
- outputs_forbidden: CO2_capture_rate, water_uptake, compressive_strength
- typical_evidence_shapes: line_plot, bar_plot, chromatogram, table, caption_text
- valid_units: a.u., min, s, ppm, uM, mg/L
- digitization_policy: visual response curves require verification
- common_failure_modes: treating pathway labels as product concentrations

### FTIR

- aliases: infrared spectrum, FTIR spectrum
- outputs_allowed: functional_group_presence, carbonate_presence as supporting evidence; carbonate_fraction only with quantified value
- outputs_forbidden: compressive_strength, water_uptake, OD600
- typical_evidence_shapes: spectrum, line_plot
- valid_units: cm^-1, %, categorical
- digitization_policy: peak labels are observations unless quantified
- common_failure_modes: using peak presence as benchmark performance

### XRD

- aliases: diffraction pattern, X-ray diffraction
- outputs_allowed: mineral_phase_presence, carbonate_presence, crystallinity only when quantified
- outputs_forbidden: growth_rate, gas_response, water_uptake
- typical_evidence_shapes: XRD_pattern, line_plot
- valid_units: degree, %, categorical
- digitization_policy: phase ID is supporting unless contract allows measured metric
- common_failure_modes: turning phase label into carbonate_fraction

### rheology

- aliases: storage modulus, loss modulus, viscosity, frequency sweep
- outputs_allowed: storage_modulus, loss_modulus, viscosity, yield_stress
- outputs_forbidden: compressive_strength unless compression assay; biomass_accumulation
- typical_evidence_shapes: line_plot, bar_plot, table
- valid_units: Pa, kPa, mPa.s, Pa.s
- digitization_policy: visual curves require verification
- common_failure_modes: mixing rheology modulus with compressive modulus

### swelling_assay

- aliases: swelling ratio, swelling kinetics
- outputs_allowed: swelling_ratio, swelling_degree, water_retention
- outputs_forbidden: CFU, OD600, gas_response
- typical_evidence_shapes: line_plot, bar_plot, table
- valid_units: %, g/g, mg/mg
- digitization_policy: visual estimates require verification
- common_failure_modes: hydrogel size photo used as exact swelling

### leakage_or_retention_assay

- aliases: cell leakage, retention, release assay
- outputs_allowed: retention_fraction, leakage_fraction, cell_retention, microalgae_retention
- outputs_forbidden: OD600 unless measured; compressive_strength
- typical_evidence_shapes: line_plot, bar_plot, table, fluorescence_image
- valid_units: %, cells/mL, a.u.
- digitization_policy: visual plots require verification
- common_failure_modes: visible cells treated as retained fraction

### biomineralization_assay

- aliases: MICP assay, carbonate precipitation, calcium carbonate quantification
- outputs_allowed: calcium_carbonate_content, carbonate_fraction, carbonate_content, biomineralization_extent
- outputs_forbidden: CO2_capture_rate unless carbon capture assay; water_uptake
- typical_evidence_shapes: bar_plot, table, FTIR, XRD, SEM_image
- valid_units: %, wt%, mg, mg/g
- digitization_policy: visual bar values require verification
- common_failure_modes: precipitate image used as carbonate amount

### schematic_or_workflow

- aliases: schematic, workflow, mechanism diagram, graphical abstract
- outputs_allowed: supporting_observation only
- outputs_forbidden: all benchmark metrics
- typical_evidence_shapes: schematic, workflow_diagram
- valid_units: none
- digitization_policy: never digitize as benchmark metric
- common_failure_modes: turning labels into present metrics

## Layer 4: Metric Contract Layer

Purpose: define legal metric candidates. Every extracted metric must satisfy this layer plus panel target group and evidence policy.

Each metric contains: metric_id, canonical_name, aliases, application_task, assay, metric_category, expected_units, expected_value_types, allowed_evidence_shapes, allowed_evidence_sources, forbidden_evidence, required_context, extraction_source_policy, needs_digitization_policy, confidence_policy, verifier_rules, rejection_rules.

### Shared contract defaults

- allowed_evidence_sources: caption_exact, table_exact, text_exact, visual_estimate
- extraction_source_policy: exact text/table/caption values use *_exact; visual plot reads use visual_estimate
- needs_digitization_policy: visual_estimate from line_plot, bar_plot, box_plot, stress_strain_plot, or chromatogram must set needs_digitization_verification=true
- confidence_policy: lower confidence when value is approximate, visually estimated, or context is incomplete
- verifier_rules: metric_name must be in panel allowed_metrics; panel matched_target_group_ids must be present; row-level matched_target_group_id is optional provenance and must not be the only acceptance gate
- rejection_rules: reject metric_name_unknown, missing_target_context, schematic_context, no_value, missing_evidence, metric_not_allowed_by_panel_target

### Metric contracts

| metric_id | canonical_name | aliases | application_task | assay | metric_category | expected_units | expected_value_types | allowed_evidence_shapes | forbidden_evidence | required_context |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| water_uptake | water_uptake | water absorption, uptake of water | water_transport | water_uptake_assay | material_structure_metric | g, mg, %, g/g | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, caption_text, table | schematic, workflow_diagram, representative_photo | material and time/condition |
| water_loss | water_loss | mass loss, water decrease | water_transport | evaporation_or_retention_assay | material_structure_metric | g, mg, % | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, caption_text, table | schematic, photo_only | material and time |
| evaporation_loss | evaporation_loss | evaporation, evaporative loss | water_transport | evaporation_or_retention_assay | material_structure_metric | g, mg, %, h | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, caption_text, table | schematic arrows | material and time |
| water_retention | water_retention | retention, retained water | water_transport | evaporation_or_retention_assay, swelling_assay | material_structure_metric | %, g, g/g | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, caption_text, table | schematic, qualitative wetness photo | material and time |
| biomass_accumulation | biomass_accumulation | biomass increase, accumulated biomass | microbial_growth_support | biomass_assay, chlorophyll_proxy_assay | biological_activity_metric | ug/mg, mg/g, mg, ug, a.u. | exact_numeric, approximate_numeric, trend | bar_plot, line_plot, caption_text, table | schematic, representative_photo, qualitative microscopy without quantitative axis | organism/material and time/condition |
| biomass_content | biomass_content | biomass amount, cell biomass | microbial_growth_support | biomass_assay | biological_activity_metric | mg, ug, mg/g, ug/mg, % | exact_numeric, approximate_numeric, trend | bar_plot, line_plot, table, caption_text | schematic, microscopy only | organism/material |
| biomass_density | biomass_density | cell density, biomass density | microbial_growth_support | biomass_assay, microscopy_quantification | biological_activity_metric | cells/mL, cells/cm2, mg/mL, a.u. | exact_numeric, approximate_numeric, trend | bar_plot, line_plot, table, caption_text | schematic, qualitative microscopy | organism/material and area/volume |
| fluorescence_intensity | fluorescence_intensity | fluorescence signal, GFP intensity | microbial_growth_support | fluorescence_microscopy | biological_activity_metric | a.u., %, normalized intensity | exact_numeric, approximate_numeric, trend | fluorescence_image, bar_plot, line_plot, caption_text, table | schematic, qualitative image without quantification unless allowed categorical | fluorophore/reporter and condition |
| microbial_colonization | microbial_colonization | colonization, cell coverage | microbial_growth_support | fluorescence_microscopy, SEM_or_morphology | biological_activity_metric | %, cells/mm2, categorical | exact_numeric, approximate_numeric, categorical, trend | microscopy_image, fluorescence_image, bar_plot, caption_text | schematic | organism/material and evidence of quantification or allowed categorical |
| OD600 | OD600 | optical density, OD 600 | microbial_growth_support | OD600_growth_assay | biological_activity_metric | OD600, a.u. | exact_numeric, approximate_numeric, trend | line_plot, table, caption_text | microscopy_image, schematic | organism and time |
| CFU | CFU | colony forming units, CFU_per_mL | microbial_growth_support | CFU_assay | biological_activity_metric | CFU, CFU/mL, log CFU/mL | exact_numeric, approximate_numeric, trend | bar_plot, table, caption_text | plate photo without count, schematic | organism and dilution/time |
| MPN | MPN | most probable number, MPN_per_mL | microbial_growth_support | MPN_assay | biological_activity_metric | MPN/mL, cells/mL | exact_numeric, approximate_numeric, trend | table, bar_plot, caption_text | schematic | organism and sample |
| compressive_strength | compressive_strength | compressive stress, strength | mechanical_reinforcement | compression_test, stress_strain_test | mechanical_rheological_metric | Pa, kPa, MPa | exact_numeric, approximate_numeric, trend | stress_strain_plot, bar_plot, box_plot, table, caption_text | SEM_image, representative_photo, water plot | material and strain/condition |
| compressive_modulus | compressive_modulus | modulus, elastic modulus in compression | mechanical_reinforcement | compression_test, stress_strain_test | mechanical_rheological_metric | Pa, kPa, MPa | exact_numeric, approximate_numeric, trend | stress_strain_plot, bar_plot, table, caption_text | SEM_image, schematic | material and test mode |
| peak_load | peak_load | maximum load, peak force | mechanical_reinforcement | compression_test | mechanical_rheological_metric | N, mN | exact_numeric, approximate_numeric, trend | stress_strain_plot, bar_plot, table, caption_text | schematic, morphology image | material and load test |
| calcium_carbonate_content | calcium_carbonate_content | CaCO3 content, carbonate amount | biomineralization_or_biocementation | biomineralization_assay | material_structure_metric | %, wt%, mg, mg/g | exact_numeric, approximate_numeric, trend | bar_plot, table, caption_text, FTIR, XRD | SEM_image only, schematic | material and assay method |
| carbonate_fraction | carbonate_fraction | carbonate percentage, mineral fraction | biomineralization_or_biocementation | biomineralization_assay | material_structure_metric | %, wt% | exact_numeric, approximate_numeric, trend | bar_plot, table, caption_text | SEM_image only, schematic | material and quantification method |
| CO2_capture_rate | CO2_capture_rate | CO2 fixation rate, capture rate, CO2 removal rate | carbon_capture | CO2_capture_assay | gas_energy_carbon_metric | umol/m2/s, mmol/h, mg/h, %/h, ppm/h, ppm h^-1 | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic CO2 arrows, organism presence | material/organism and condition |
| CO2_concentration | CO2_concentration | CO2 concentration, remaining CO2 | carbon_capture | CO2_capture_assay | gas_energy_carbon_metric | ppm, ppb, % | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic CO2 arrows, organism presence | gas concentration, material/organism and condition |
| CO2_uptake | CO2_uptake | CO2 absorption, CO2 fixed | carbon_capture | CO2_capture_assay | gas_energy_carbon_metric | mmol, mg, mg/g, % | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | material/organism and time |
| carbon_capture_efficiency | carbon_capture_efficiency | capture efficiency, sequestration efficiency | carbon_capture | CO2_capture_assay | gas_energy_carbon_metric | %, fraction | exact_numeric, approximate_numeric, trend | bar_plot, line_plot, table, caption_text | schematic | comparison target and condition |
| chlorophyll_proxy | chlorophyll_proxy | chlorophyll, chlorophyll fluorescence | carbon_capture, microalgae_activity_or_retention | chlorophyll_proxy_assay, fluorescence_microscopy | biological_activity_metric | a.u., ug/mL, mg/g, % | exact_numeric, approximate_numeric, trend | fluorescence_image, bar_plot, line_plot, table, caption_text | green photo, schematic | organism and photosynthetic proxy |
| gas_response | gas_response | response signal, sensor response | gas_sensing | gas_sensing_response_assay | gas_energy_carbon_metric | a.u., %, ppm response | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | pathway schematic, gas icon | gas and material/strain |
| reporter_signal | reporter_signal | reporter output, fluorescence reporter | gas_sensing | gas_sensing_response_assay, fluorescence_reporter_assay | gas_energy_carbon_metric | a.u., %, normalized intensity | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, fluorescence_image, table, caption_text | genetic circuit schematic | reporter and gas/condition |
| response_intensity | response_intensity | signal intensity, response amplitude | gas_sensing | gas_sensing_response_assay | gas_energy_carbon_metric | a.u., %, normalized intensity | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | gas/condition |
| response_time | response_time | time to response, response duration | gas_sensing | gas_sensing_response_assay | gas_energy_carbon_metric | s, min, h | exact_numeric, approximate_numeric | line_plot, table, caption_text | schematic | gas/condition |
| product_concentration | product_concentration | product titer, metabolite concentration | gas_sensing | gas_sensing_response_assay, chromatogram | product_yield_metric | mg/L, uM, mM, ppm | exact_numeric, approximate_numeric, trend | chromatogram, bar_plot, table, caption_text | pathway schematic | product and condition |
| isoamyl_acetate_concentration | isoamyl_acetate_concentration | isoamyl acetate, IA concentration | gas_sensing | chromatogram, gas_sensing_response_assay | product_yield_metric | mg/L, uM, mM, ppm | exact_numeric, approximate_numeric, trend | chromatogram, bar_plot, table, caption_text | pathway schematic | product and gas/strain |
| isoamyl_alcohol_concentration | isoamyl_alcohol_concentration | isoamyl alcohol, precursor concentration | gas_sensing | chromatogram, gas_sensing_response_assay | product_yield_metric | mg/L, uM, mM, ppm | exact_numeric, approximate_numeric, trend | chromatogram, bar_plot, table, caption_text | pathway schematic | product and condition |
| metabolite_concentration | metabolite_concentration | metabolite level, metabolite titer | general_bio_chemistry | metabolite_assay, chromatogram, colorimetric_assay | product_yield_metric | M, mM, uM, nM, mol/L, mmol/L, mg/L, ug/mL, mg/mL, g/L, ppm, ppb | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, chromatogram, table, caption_text | pathway schematic | named metabolite and condition |
| lactate_concentration | lactate_concentration | lactic acid concentration, lactate titer | general_bio_chemistry | metabolite_assay | product_yield_metric | mM, uM, mg/L, g/L | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | metabolite and condition |
| glucose_concentration | glucose_concentration | glucose level, glucose titer | general_bio_chemistry | substrate_consumption_assay | product_yield_metric | mM, uM, mg/L, g/L | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | substrate and time/condition |
| acetate_concentration | acetate_concentration | acetic acid concentration, acetate titer | general_bio_chemistry | metabolite_assay | product_yield_metric | mM, uM, mg/L, g/L | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | metabolite and condition |
| protein_concentration | protein_concentration | protein amount, total protein | general_bio_chemistry | protein_assay | biological_activity_metric | mg/mL, mg/L, ug/mL, uM, mg/g | exact_numeric, approximate_numeric, trend | bar_plot, line_plot, table, caption_text | schematic | protein/sample and assay |
| enzyme_activity | enzyme_activity | catalytic activity, activity units | general_bio_chemistry | enzyme_activity_assay | biological_activity_metric | U, IU, U/mL, U/mg, IU/mL, kat, nkat, umol/min | exact_numeric, approximate_numeric, trend | bar_plot, line_plot, table, caption_text | schematic | enzyme and assay condition |
| specific_enzyme_activity | specific_enzyme_activity | specific activity | general_bio_chemistry | enzyme_activity_assay | biological_activity_metric | U/mg, IU/mg, U/mL, kat/kg | exact_numeric, approximate_numeric, trend | bar_plot, line_plot, table, caption_text | schematic | enzyme and protein normalization |
| cell_density | cell_density | cell concentration, cell count | microbial_growth_support | cell_count_assay, microscopy_quantification | biological_activity_metric | cells/mL, cells/L, cells/cm2, cells/mm2, OD600, a.u. | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, microscopy_image, table, caption_text | schematic | organism/sample and time |
| cell_viability | cell_viability | viability, live cell percentage | antibacterial_or_biological_activity | viability_assay | biological_activity_metric | %, fraction, a.u. | exact_numeric, approximate_numeric, trend | bar_plot, line_plot, table, caption_text | schematic | organism/cell line and condition |
| absorbance | absorbance | absorbance signal, optical absorbance | general_bio_chemistry | absorbance_assay | biological_activity_metric | absorbance, OD600, a.u. | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | microscopy_image | wavelength/context |
| reaction_rate | reaction_rate | reaction velocity, conversion rate | general_bio_chemistry | reaction_assay | chemical_environment_metric | 1/s, 1/min, 1/h, mM/min, mg/L/h, umol/min | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | reaction and condition |
| porosity | porosity | pore fraction, void fraction | material_characterization | porosity_assay, image_quantification | material_structure_metric | %, fraction | exact_numeric, approximate_numeric, trend | SEM_image, microscopy_image, bar_plot, table, caption_text | schematic | material and method |
| pore_size | pore_size | pore diameter, pore radius | material_characterization | microscopy_quantification | material_structure_metric | nm, um, mm | exact_numeric, approximate_numeric, trend | SEM_image, microscopy_image, histogram, table, caption_text | schematic | material and imaging method |
| particle_size | particle_size | particle diameter, size distribution | material_characterization | particle_size_assay | material_structure_metric | nm, um, mm | exact_numeric, approximate_numeric, trend | microscopy_image, histogram, table, caption_text | schematic | material and method |
| surface_area | surface_area | BET surface area, specific surface area | material_characterization | BET_assay | material_structure_metric | m2/g, cm2/g | exact_numeric, approximate_numeric, trend | table, bar_plot, caption_text | schematic | material and assay |
| zeta_potential | zeta_potential | zeta, surface potential | material_characterization | zeta_potential_assay | material_structure_metric | mV, V | exact_numeric, approximate_numeric, trend | table, bar_plot, caption_text | schematic | material and medium |
| contact_angle | contact_angle | water contact angle, wettability angle | material_characterization | contact_angle_assay | material_structure_metric | degree, deg | exact_numeric, approximate_numeric, trend | representative_photo, bar_plot, table, caption_text | schematic | material and liquid |
| tensile_strength | tensile_strength | tensile stress, ultimate tensile strength | mechanical_reinforcement | tensile_test, stress_strain_test | mechanical_rheological_metric | Pa, kPa, MPa, GPa | exact_numeric, approximate_numeric, trend | stress_strain_plot, bar_plot, table, caption_text | morphology image only | material and test mode |
| young_modulus | young_modulus | Young's modulus, elastic modulus | mechanical_reinforcement | tensile_test, stress_strain_test | mechanical_rheological_metric | Pa, kPa, MPa, GPa | exact_numeric, approximate_numeric, trend | stress_strain_plot, bar_plot, table, caption_text | morphology image only | material and test mode |
| elongation_at_break | elongation_at_break | strain at break, breaking elongation | mechanical_reinforcement | tensile_test | mechanical_rheological_metric | %, strain, mm/mm | exact_numeric, approximate_numeric, trend | stress_strain_plot, bar_plot, table, caption_text | morphology image only | material and test mode |
| pH | pH | acidity, pH value | chemical_environment | pH_measurement | chemical_environment_metric | pH | exact_numeric, approximate_numeric, trend | table, line_plot, bar_plot, caption_text | schematic | sample and condition |
| temperature | temperature | assay temperature, incubation temperature | general_assay_condition | temperature_measurement | treatment_condition_metric | degC, C, K | exact_numeric, approximate_numeric, trend | table, line_plot, caption_text | schematic | assay/sample condition |
| dissolved_oxygen | dissolved_oxygen | DO, oxygen concentration | environmental_chemistry | oxygen_probe_assay | chemical_environment_metric | mg/L, %, ppm | exact_numeric, approximate_numeric, trend | line_plot, table, caption_text | schematic | sample and condition |
| conductivity | conductivity | electrical conductivity | chemical_environment | conductivity_assay | chemical_environment_metric | S/m, mS/cm, uS/cm | exact_numeric, approximate_numeric, trend | table, line_plot, bar_plot, caption_text | schematic | sample and condition |
| salinity | salinity | salt concentration | chemical_environment | salinity_assay | chemical_environment_metric | ppt, %, g/L, mg/L | exact_numeric, approximate_numeric, trend | table, line_plot, caption_text | schematic | sample and condition |
| turbidity | turbidity | turbidity signal | chemical_environment | turbidity_assay | chemical_environment_metric | NTU, a.u., OD600 | exact_numeric, approximate_numeric, trend | table, line_plot, bar_plot, caption_text | microscopy_image | sample and condition |
| removal_efficiency | removal_efficiency | removal percentage, clearance efficiency | environmental_treatment | removal_assay | degradation_treatment_metric | %, fraction | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | analyte and treatment condition |
| adsorption_capacity | adsorption_capacity | adsorption amount, sorption capacity | environmental_treatment | adsorption_assay | material_structure_metric | mg/g, g/g, mmol/g, ug/mg | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | adsorbent and analyte |
| adsorption_rate | adsorption_rate | sorption rate, uptake rate | environmental_treatment | adsorption_assay | degradation_treatment_metric | mg/g/h, mg/L/h, 1/h, mmol/g/h | exact_numeric, approximate_numeric, trend | line_plot, bar_plot, table, caption_text | schematic | adsorbent/analyte and time |

## Layer 5: Evidence Policy Layer

Purpose: decide which evidence can extract metrics, which only supports observations, and which must reject.

Each evidence type contains: evidence_type, role, can_extract_benchmark_metric, can_extract_supporting_observation, needs_digitization, allowed_metric_types, forbidden_metric_types, common_failure_modes, verifier_policy.

### Evidence policies

- schematic: role=context; can_extract_benchmark_metric=false; can_extract_supporting_observation=true; needs_digitization=false; allowed_metric_types=none; forbidden_metric_types=all benchmark metrics; common_failure_modes=turning labels into present metrics; verifier_policy=reject schematic_context metrics.
- workflow_diagram: role=context; can_extract_benchmark_metric=false; can_extract_supporting_observation=true; needs_digitization=false; allowed_metric_types=none; forbidden_metric_types=all benchmark metrics; common_failure_modes=workflow steps as performance values; verifier_policy=reject.
- representative_photo: role=supporting_observation; can_extract_benchmark_metric=false unless exact value in caption/table/text; can_extract_supporting_observation=true; needs_digitization=false; allowed_metric_types=qualitative observations only when contract allows; forbidden_metric_types=numeric performance metrics; common_failure_modes=photo appearance as exact value; verifier_policy=reject if no exact evidence.
- microscopy_image: role=supporting_or_metric_if_quantified; can_extract_benchmark_metric=true only with quantitative axis, quantification, or explicitly allowed categorical metric; can_extract_supporting_observation=true; needs_digitization=false for qualitative, true for plotted quantification; allowed_metric_types=microbial_colonization, fluorescence_intensity, morphology when allowed; forbidden_metric_types=compressive_strength, water_uptake, CO2_capture_rate; common_failure_modes=visible cells as cell_density; verifier_policy=must match metric contract.
- fluorescence_image: role=supporting_or_metric_if_quantified; can_extract_benchmark_metric=true only when quantified or target group allows categorical reporter/colonization; can_extract_supporting_observation=true; needs_digitization=false unless plot-derived; allowed_metric_types=fluorescence_intensity, chlorophyll_proxy, reporter_signal, microbial_colonization; forbidden_metric_types=OD600, CFU, water_uptake; common_failure_modes=green signal as present metric; verifier_policy=reject if panel not target matched.
- SEM_image: role=supporting_observation; can_extract_benchmark_metric=false unless contract allows quantified morphology/porosity; can_extract_supporting_observation=true; needs_digitization=false; allowed_metric_types=porosity/morphology observations, precipitate presence; forbidden_metric_types=compressive_strength, water_uptake, CO2_capture_rate; common_failure_modes=structure image as mechanical metric; verifier_policy=reject benchmark metrics not explicitly allowed.
- line_plot: role=metric_evidence; can_extract_benchmark_metric=true; can_extract_supporting_observation=true; needs_digitization=true unless exact value in caption/table/text; allowed_metric_types=numeric/trend metrics in matched group; forbidden_metric_types=schematic labels; common_failure_modes=axis label as value; verifier_policy=visual estimates require needs_digitization_verification=true.
- bar_plot: role=metric_evidence; can_extract_benchmark_metric=true; can_extract_supporting_observation=true; needs_digitization=true unless exact value in caption/table/text; allowed_metric_types=numeric/trend metrics in matched group; forbidden_metric_types=unsupported categorical present metrics; common_failure_modes=unreadable bars as exact values; verifier_policy=visual estimates require needs_digitization_verification=true.
- box_plot: role=metric_evidence; can_extract_benchmark_metric=true; can_extract_supporting_observation=true; needs_digitization=true unless exact value in caption/table/text; allowed_metric_types=numeric metrics; forbidden_metric_types=qualitative observations as benchmark; common_failure_modes=whisker/median confusion; verifier_policy=mark visual_estimate.
- stress_strain_plot: role=metric_evidence; can_extract_benchmark_metric=true; can_extract_supporting_observation=true; needs_digitization=true unless exact value in caption/table/text; allowed_metric_types=compressive_strength, compressive_modulus, peak_load; forbidden_metric_types=water_transport and growth metrics; common_failure_modes=axis max as sample value; verifier_policy=must match mechanical target group.
- chromatogram: role=metric_evidence; can_extract_benchmark_metric=true; can_extract_supporting_observation=true; needs_digitization=true unless exact value in caption/table/text; allowed_metric_types=product_concentration, isoamyl_acetate_concentration, isoamyl_alcohol_concentration; forbidden_metric_types=gas_response if no response metric; common_failure_modes=peak label as concentration; verifier_policy=must have product/context.
- caption_text: role=text_evidence; can_extract_benchmark_metric=true only if value and unit are explicitly stated or it names plotted metric for visual estimate; can_extract_supporting_observation=true; needs_digitization=false for exact stated values; allowed_metric_types=contract metrics; forbidden_metric_types=guessed values; common_failure_modes=caption description as value; verifier_policy=exact metric only with value and unit.
- paragraph_text: role=text_evidence; can_extract_benchmark_metric=true only with explicit value/unit/context; can_extract_supporting_observation=true; needs_digitization=false; allowed_metric_types=contract metrics; forbidden_metric_types=unsupported claims; common_failure_modes=abstract claims as measured rows; verifier_policy=require evidence ids.
- table: role=metric_evidence; can_extract_benchmark_metric=true; can_extract_supporting_observation=true; needs_digitization=false; allowed_metric_types=contract metrics; forbidden_metric_types=none outside target groups; common_failure_modes=wrong column or unit; verifier_policy=prefer table_exact.

## Layer 6: Runtime Reasoning Policy

Purpose: LLM runtime rulebook.

Rule 1: Only extract metrics inside PaperBenchmarkPlan.target_metric_groups.

Rule 2: Panel must match target group before metric extraction.

Rule 3: Observation never becomes benchmark metric unless metric contract explicitly allows qualitative/categorical benchmark metric.

Rule 4: Schematic/context/methods panels never produce accepted metric_rows.

Rule 5: Metric must satisfy Metric Contract: metric_name, value_type, unit, evidence, target_group.

Rule 6: Evidence must satisfy Evidence Policy.

Rule 7: Numeric visual estimate must set extraction_source=visual_estimate and needs_digitization_verification=true.

Rule 8: If evidence is insufficient, output unsupported_claims, not guessed metric. Reject instead of guess.

Rule 9: metric_name_unknown cannot be accepted.

Rule 10: Rejected metrics must keep reason.

## Layer 7: Knowledge Evolution Policy

Purpose: define how web search, external ontologies, and new papers update the ontology outside runtime.

Runtime pipeline:

- must not call web search;
- must not call Tavily even when `TAVILY_API_KEY` exists;
- must only read stable BENCHMARK_ONTOLOGY_V1.

Offline Ontology Builder:

- may use web search through Tavily using `.env` `TAVILY_API_KEY`;
- may use OBI, EFO, OBO, domain ontologies, and literature references;
- may use project corpus evidence and rejected audit rows;
- must generate proposed ontology updates;
- must pass automated multi-evidence, multi-constraint, repeated-occurrence checks before entering runtime overlay.

Policies:

- unknown_metric_policy: reject from accepted runtime CSV, record rejected metric and reason, queue as ontology gap candidate.
- unknown_assay_policy: do not infer assay legality at runtime; record unknown assay context for offline proposal.
- unknown_material_policy: preserve material text as context but do not create new metric permissions.
- evidence_gap_policy: if value, unit, target group, or evidence is missing, output unsupported_claims or rejected rows.
- source_search_policy: offline only; Tavily, literature, and ontology references create notes, not runtime mutations.
- automated_review_policy: proposed patches require metric-name dimension, unit dimension, current-panel evidence, and repeated support before runtime overlay.
- overlay_policy: automatically discovered contracts are written to runtime_ontology_overlay.json; this base ontology remains stable until a versioned patch is generated.
- versioning_policy: accepted updates create a new ontology version and runtime tests must assert that version.

Future tool design: `tools/ontology_builder.py`

- scan audit rejected metrics;
- detect unknown metric, assay, and material terms;
- collect candidate source notes from Tavily/web/literature/OBO/EFO/OBI;
- generate a proposed ontology patch or runtime overlay;
- never directly edit this base ontology during extraction runtime.

## Runtime Target Group Guidance

Each group contains: group_id, application_task, assay, metric_category, canonical_metrics, metric_aliases, expected_units, expected_value_types, valid_evidence_shapes, valid_evidence_sources, exclusion_rules, digitization_policy.

### water_transport.water_uptake

- application_task: water_transport
- assay: water_uptake_assay / evaporation_or_retention_assay
- metric_category: material_structure_metric
- canonical_metrics: water_uptake, water_loss, evaporation_loss, water_retention
- metric_aliases: uptake of water -> water_uptake; water loss -> water_loss; evaporation -> evaporation_loss; retained water -> water_retention
- expected_units: g, mg, %, g/g, mg/g, h, min
- expected_value_types: exact_numeric, approximate_numeric, trend
- valid_evidence_shapes: line_plot, bar_plot, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject schematic arrows; reject wet/dry appearance; reject generic "water transport" labels
- digitization_policy: plot values require extraction_source=visual_estimate and needs_digitization_verification=true unless exact value appears in caption/table/text

### microbial_growth_support.growth_activity

- application_task: microbial_growth_support
- assay: OD600_growth_assay / CFU_assay / MPN_assay / fluorescence_microscopy
- metric_category: biological_activity_metric
- canonical_metrics: OD600, CFU, MPN, fluorescence_intensity, microbial_colonization, cell_density, growth_rate
- metric_aliases: optical density -> OD600; colony forming unit -> CFU; most probable number -> MPN; fluorescence signal -> fluorescence_intensity; colonization -> microbial_colonization
- expected_units: OD600, CFU/mL, MPN/mL, cells/mL, cells/mm2, a.u., %, 1/h
- expected_value_types: exact_numeric, approximate_numeric, categorical, trend
- valid_evidence_shapes: line_plot, bar_plot, fluorescence_image, microscopy_image, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject qualitative fluorescence as metric unless allowed by contract; reject organism presence alone
- digitization_policy: plotted values require visual_estimate verification

### microbial_growth_support.biomass_accumulation

- application_task: microbial_growth_support
- assay: biomass_assay / chlorophyll_proxy_assay / fluorescence_microscopy
- metric_category: biological_activity_metric
- canonical_metrics: biomass_accumulation, biomass_content, biomass_density, chlorophyll_proxy
- metric_aliases: accumulated biomass -> biomass_accumulation; biomass amount -> biomass_content; cell biomass density -> biomass_density; chlorophyll signal -> chlorophyll_proxy
- expected_units: ug/mg, mg/g, mg, ug, a.u., %, cells/mL
- expected_value_types: exact_numeric, approximate_numeric, trend
- valid_evidence_shapes: bar_plot, line_plot, fluorescence_image, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject schematic organism labels; reject qualitative microscopy without quantitative axis; reject "biomass present" unless explicitly allowed categorical metric
- digitization_policy: visual plot values require needs_digitization_verification=true

### mechanical_reinforcement.compressive_test

- application_task: mechanical_reinforcement
- assay: compression_test / stress_strain_test / biomineralization_assay
- metric_category: mechanical_rheological_metric
- canonical_metrics: compressive_strength, compressive_modulus, peak_load, stress_at_failure, calcium_carbonate_content, carbonate_fraction
- metric_aliases: compressive stress -> compressive_strength; modulus -> compressive_modulus; peak force -> peak_load; CaCO3 content -> calcium_carbonate_content; carbonate fraction -> carbonate_fraction
- expected_units: Pa, kPa, MPa, N, mN, %, wt%
- expected_value_types: exact_numeric, approximate_numeric, trend
- valid_evidence_shapes: stress_strain_plot, bar_plot, box_plot, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject SEM morphology as compressive metric; reject water or growth plots; do not rename peak_load as strength
- digitization_policy: stress-strain, box, and bar plot values require verification

### carbon_capture.co2_capture

- application_task: carbon_capture
- assay: CO2_capture_assay / chlorophyll_proxy_assay / biomineralization_assay
- metric_category: gas_energy_carbon_metric
- canonical_metrics: CO2_capture_rate, CO2_concentration, CO2_uptake, carbon_capture_efficiency, biomass_proxy, chlorophyll_proxy, carbonate_content
- metric_aliases: CO2 fixation -> CO2_capture_rate; CO2 removal rate -> CO2_capture_rate; CO2 concentration -> CO2_concentration; CO2 uptake -> CO2_uptake; capture efficiency -> carbon_capture_efficiency; chlorophyll signal -> chlorophyll_proxy; carbonate amount -> carbonate_content
- expected_units: umol/m2/s, mmol/h, mg/h, %/h, ppm/h, ppm h^-1, ppm, mmol, mg, mg/g, %, a.u., wt%
- expected_value_types: exact_numeric, approximate_numeric, trend
- valid_evidence_shapes: line_plot, bar_plot, fluorescence_image, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject conceptual CO2 arrows; reject cyanobacteria presence without capture/proxy measurement
- digitization_policy: plot-derived values require verification unless exact text/table/caption values exist

### gas_sensing.response

- application_task: gas_sensing
- assay: gas_sensing_response_assay / fluorescence_reporter_assay / chromatogram
- metric_category: gas_energy_carbon_metric / product_yield_metric
- canonical_metrics: gas_response, reporter_signal, response_intensity, response_time, product_concentration, isoamyl_acetate_concentration, isoamyl_alcohol_concentration
- metric_aliases: response signal -> gas_response; reporter output -> reporter_signal; signal amplitude -> response_intensity; response duration -> response_time; isoamyl acetate -> isoamyl_acetate_concentration; isoamyl alcohol -> isoamyl_alcohol_concentration
- expected_units: a.u., %, s, min, h, ppm, uM, mM, mg/L
- expected_value_types: exact_numeric, approximate_numeric, categorical, trend
- valid_evidence_shapes: line_plot, bar_plot, chromatogram, fluorescence_image, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject pathway labels, gas icons, and generic gas_sensing_signal unless explicitly listed as allowed metric
- digitization_policy: visual response curves and chromatograms require verification when not exact

### hydrogel_swelling_retention

- application_task: hydrogel_swelling_retention
- assay: swelling_assay / evaporation_or_retention_assay
- metric_category: material_structure_metric
- canonical_metrics: swelling_ratio, swelling_degree, water_retention, water_loss
- metric_aliases: swelling -> swelling_ratio; retention -> water_retention; deswelling -> water_loss
- expected_units: %, g/g, mg/mg, g, mg
- expected_value_types: exact_numeric, approximate_numeric, trend
- valid_evidence_shapes: line_plot, bar_plot, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject hydrogel photo size changes without measurement
- digitization_policy: plot-derived values require verification

### microalgae_activity_or_retention

- application_task: microalgae_activity_or_retention
- assay: chlorophyll_proxy_assay / leakage_or_retention_assay / fluorescence_microscopy
- metric_category: biological_activity_metric
- canonical_metrics: chlorophyll_proxy, biomass_accumulation, microalgae_retention, leakage_fraction, retention_fraction, fluorescence_intensity
- metric_aliases: chlorophyll fluorescence -> chlorophyll_proxy; retained algae -> microalgae_retention; leaked cells -> leakage_fraction
- expected_units: a.u., %, cells/mL, mg/g, ug/mL
- expected_value_types: exact_numeric, approximate_numeric, categorical, trend
- valid_evidence_shapes: line_plot, bar_plot, fluorescence_image, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject green material photo as chlorophyll_proxy; reject algae icon
- digitization_policy: plot-derived values require verification

### biomineralization_or_biocementation

- application_task: biomineralization_or_biocementation
- assay: biomineralization_assay / FTIR / XRD / SEM_or_morphology
- metric_category: material_structure_metric
- canonical_metrics: calcium_carbonate_content, carbonate_fraction, carbonate_content, biomineralization_extent
- metric_aliases: CaCO3 -> calcium_carbonate_content; carbonate percentage -> carbonate_fraction; mineral amount -> carbonate_content
- expected_units: %, wt%, mg, mg/g, categorical
- expected_value_types: exact_numeric, approximate_numeric, categorical, trend
- valid_evidence_shapes: bar_plot, line_plot, FTIR_spectrum, XRD_pattern, SEM_image, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject precipitate image without contract-allowed qualitative metric or quantitative value
- digitization_policy: spectra peak positions are observations; numeric bars require verification

### antibacterial_or_biological_activity

- application_task: antibacterial_or_biological_activity
- assay: antibacterial_assay / viability_assay / degradation_assay
- metric_category: biological_activity_metric / degradation_treatment_metric
- canonical_metrics: inhibition_zone, survival_ratio, viability_percent, degradation_efficiency, activity_retention
- metric_aliases: antibacterial effect -> inhibition_zone; viability -> viability_percent; degradation -> degradation_efficiency
- expected_units: mm, %, CFU/mL, a.u., mg/L
- expected_value_types: exact_numeric, approximate_numeric, categorical, trend
- valid_evidence_shapes: bar_plot, line_plot, representative_photo, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject antibacterial label without assay result
- digitization_policy: plotted values require verification; zone photos require exact stated diameter or explicit quantification

### environmental_treatment.removal_adsorption

- application_task: environmental_treatment
- assay: removal_assay / adsorption_assay / degradation_assay
- metric_category: degradation_treatment_metric / material_structure_metric
- canonical_metrics: removal_efficiency, adsorption_capacity, adsorption_rate, degradation_efficiency
- metric_aliases: removal percentage -> removal_efficiency; removal efficiency -> removal_efficiency; clearance efficiency -> removal_efficiency; adsorption amount -> adsorption_capacity; sorption capacity -> adsorption_capacity; uptake rate -> adsorption_rate; degradation percentage -> degradation_efficiency
- expected_units: %, fraction, mg/g, g/g, mmol/g, ug/mg, mg/g/h, mg/L/h, 1/h, mmol/g/h
- expected_value_types: exact_numeric, approximate_numeric, trend
- valid_evidence_shapes: line_plot, bar_plot, scatter_plot, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject pollutant schematic without measured removal, adsorption, or degradation value; reject concentration axis values as product_concentration unless the y-axis is explicitly product concentration
- digitization_policy: plot-derived removal or adsorption values require verification unless exact text/table/caption values exist

### mechanical_rheology

- application_task: mechanical_reinforcement
- assay: rheology
- metric_category: mechanical_rheological_metric
- canonical_metrics: storage_modulus, loss_modulus, viscosity, yield_stress
- metric_aliases: G' -> storage_modulus; G'' -> loss_modulus; viscous modulus -> loss_modulus
- expected_units: Pa, kPa, mPa.s, Pa.s
- expected_value_types: exact_numeric, approximate_numeric, trend
- valid_evidence_shapes: line_plot, bar_plot, caption_text, paragraph_text, table
- valid_evidence_sources: visual_plot, caption, text, table
- exclusion_rules: reject compression strength claims from rheology-only panels
- digitization_policy: plot-derived values require verification

## Prompt Usage Instruction

PaperTaskPlanner outputs a Paper Benchmark Plan, not a summary. It must use Runtime Target Group Guidance to create `target_metric_groups`; those groups are hard constraints.

PanelSemanticClassifier outputs Panel Target Match, not generic classification. It must return `matched_target_group_ids`, `allowed_metrics`, `evidence_role`, and `exclusion_reason`. Schematic/context/methods panels must set `skip_metric_extraction`.

MetricExtractor executes Metric Contract and Runtime Reasoning Policy. It must only output `allowed_metrics`. If `allowed_metrics=[]`, output `metrics=[]`. It must not output `metric_name_unknown` or schematic-label present metrics.

MetricVerifier remains the hard acceptance gate. It rejects missing panel target context, unknown metric names, metrics outside panel allowed_metrics, schematic/context metrics, empty values, and missing evidence. It must not discard an otherwise scoped metric only because row-level `matched_target_group_id` is empty.

## Version Notes

This is V1. It upgrades V0 from vocabulary plus guidance into a runtime knowledge model. It intentionally does not attempt exhaustive domain expansion; unknowns are rejected at runtime and routed to offline ontology evolution.
