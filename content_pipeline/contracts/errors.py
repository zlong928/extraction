from __future__ import annotations


class ExtractionPipelineError(Exception):
    """Base error for the content-graph extraction pipeline."""


class ExtractionInputError(ExtractionPipelineError):
    """Input files or required content are missing or invalid."""


class ExtractionSchemaError(ExtractionPipelineError):
    """A phase output failed local schema or contract validation."""


class ExtractionPhaseError(ExtractionPipelineError):
    """An LLM or deterministic phase failed."""


class ExtractionRoutingError(ExtractionPipelineError):
    """Evidence shape to extractor routing failed."""
