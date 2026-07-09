from __future__ import annotations

from content_pipeline.contracts.evidence import EvidenceItem


_PRIORITY = {
    "primary_caption": 100,
    "same_panel": 90,
    "same_figure": 70,
    "adjacent_table": 65,
    "adjacent_formula": 65,
    "citation": 60,
    "same_section": 50,
    "background": 20,
    "sibling_panel": 10,
    "excluded": 0,
}


class EvidenceDeduplicator:
    """Remove repeated evidence before LLM input while preserving a report."""

    def dedupe(self, items: list[EvidenceItem]) -> tuple[list[EvidenceItem], list[dict]]:
        best_by_key: dict[tuple[str, str, str], EvidenceItem] = {}
        report: list[dict] = []
        for item in items:
            key = self._key(item)
            existing = best_by_key.get(key)
            if existing is None:
                best_by_key[key] = item
                continue
            keep, remove = self._prefer(existing, item)
            best_by_key[key] = keep
            report.append({
                "kept_evidence_id": keep.evidence_id,
                "removed_evidence_id": remove.evidence_id,
                "reason": "duplicate_text_or_block_with_weaker_provenance",
                "text_hash": keep.text_hash,
            })
        return list(best_by_key.values()), report

    def _key(self, item: EvidenceItem) -> tuple[str, str, str]:
        return (item.block_id or "", item.text_hash or item.text.lower(), item.scope)

    def _prefer(self, left: EvidenceItem, right: EvidenceItem) -> tuple[EvidenceItem, EvidenceItem]:
        left_score = _PRIORITY.get(left.relation, 0) + left.confidence
        right_score = _PRIORITY.get(right.relation, 0) + right.confidence
        return (right, left) if right_score > left_score else (left, right)
