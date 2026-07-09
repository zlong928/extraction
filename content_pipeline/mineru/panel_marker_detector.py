from __future__ import annotations

import re

from content_pipeline.contracts.blocks import PanelMarkerCandidate


_STRONG_MARKER_RE = re.compile(
    r"(?P<prefix>^|[;,.\n\r\t\s])(?:\s*\(?\s*(?P<marker>[a-zA-Z])\s*[\).:]|\s*<[^>]+>\s*(?P<html_marker>[a-zA-Z])\s*</[^>]+>)"
)
_WEAK_MARKER_RE = re.compile(r"(?:^|[;,.]\s+)(?P<marker>[a-z])(?=\s+[A-Za-z0-9\(\[])")
_STAT_CONTEXT_RE = re.compile(
    r"(?i)(?:\bp\s*(?:<|>|=|≤|≥)|\bp[-\s]?value\b|\bn\s*=|\bn\s+number\b|\bt\s+test\b|\br\s*=|\bx\s+axis\b|\by\s+axis\b|\bz\s+stack\b)"
)
_MATH_NEAR_RE = re.compile(r"[<>=≤≥±×/]|\d\s*(?:%|mg|g|ml|l|mm|µm|um|nm|h|min|s)\b", re.IGNORECASE)


class PanelMarkerDetector:
    """Detect panel markers while rejecting statistical/scientific symbols."""

    def detect(self, caption_text: str, figure_label: str | None = None) -> list[PanelMarkerCandidate]:
        if not caption_text:
            return []
        candidates: list[PanelMarkerCandidate] = []
        claimed_spans: set[tuple[int, int]] = set()
        for match in _STRONG_MARKER_RE.finditer(caption_text):
            marker = (match.group("marker") or match.group("html_marker") or "").lower()
            if not marker:
                continue
            start = match.start("marker") if match.group("marker") else match.start("html_marker")
            end = match.end("marker") if match.group("marker") else match.end("html_marker")
            candidate = self._candidate(caption_text, marker, start, end, "explicit_parenthesized", 0.92)
            if candidate.rejection_reason is None:
                candidates.append(candidate)
                claimed_spans.add((start, end))
        for match in _WEAK_MARKER_RE.finditer(caption_text):
            marker = match.group("marker").lower()
            start, end = match.start("marker"), match.end("marker")
            if (start, end) in claimed_spans:
                continue
            candidate = self._candidate(caption_text, marker, start, end, "weak_text_marker", 0.42)
            if candidate.rejection_reason is None:
                candidates.append(candidate)
        return _dedupe(candidates)

    def _candidate(self, text: str, marker: str, start: int, end: int, evidence_type: str, confidence: float) -> PanelMarkerCandidate:
        surrounding = text[max(0, start - 24): min(len(text), end + 24)]
        rejection = _reject_reason(text, start, end, surrounding, evidence_type)
        if rejection:
            return PanelMarkerCandidate(marker, start, end, 0.0, evidence_type, rejection, surrounding)
        return PanelMarkerCandidate(marker, start, end, confidence, evidence_type, None, surrounding)


def _reject_reason(text: str, start: int, end: int, surrounding: str, evidence_type: str) -> str | None:
    marker = text[start:end].lower()
    local_after = text[end: min(len(text), end + 16)]
    local_before = text[max(0, start - 8): start]
    original_marker = text[start:end]
    if evidence_type == "explicit_parenthesized" and original_marker.isupper() and re.match(r"\.\s+[a-z]", local_after):
        return "species_abbreviation"
    if marker in {"p", "n", "t", "r", "x", "y", "z"} and _STAT_CONTEXT_RE.search(surrounding):
        return "statistical_or_axis_symbol"
    if re.match(r"\s*(?:<|>|=|≤|≥)", local_after):
        return "math_comparator_after_marker"
    if _MATH_NEAR_RE.search(local_before + marker + local_after) and evidence_type == "weak_text_marker":
        return "math_or_unit_context"
    if start > 0 and text[start - 1].isalnum():
        return "inside_word"
    if end < len(text) and text[end:end + 1].isalnum():
        return "inside_word"
    return None


def _dedupe(candidates: list[PanelMarkerCandidate]) -> list[PanelMarkerCandidate]:
    seen: set[tuple[str, int]] = set()
    result: list[PanelMarkerCandidate] = []
    for item in sorted(candidates, key=lambda c: (c.start, -c.confidence)):
        key = (item.marker, item.start)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def detect_panel_markers(caption_text: str, figure_label: str | None = None) -> list[PanelMarkerCandidate]:
    return PanelMarkerDetector().detect(caption_text, figure_label=figure_label)
