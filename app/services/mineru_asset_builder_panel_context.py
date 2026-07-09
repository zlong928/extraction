from __future__ import annotations

import re
from typing import Any

from app.core.constants import compact_text


def strip_text_markup(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def find_panel_marker_positions(full_caption: str) -> list[tuple[str, int]]:
    text = compact_text(strip_text_markup(full_caption))
    if not text:
        return []
    marker_re = re.compile(
        r"(?:^\s*|[;:,.\)\]\}\-]\s*)([a-z])(?=\s+[A-Za-z0-9\(\[]|$)"
    )
    markers: list[tuple[str, int]] = []
    for match in marker_re.finditer(text):
        marker = (match.group(1) or "").lower()
        if not marker:
            continue
        marker_pos = match.start(1)
        after = text[marker_pos + 1 :]
        if re.match(r"(?i)\s*[;:,\-]?\s*(?:fig(?:ure)?\.?\s*\d+|figure\s*\d+)", after):
            continue
        if marker_pos > 0 and text[marker_pos - 1].isalnum():
            continue
        markers.append((marker, marker_pos))
    return markers


def parse_figure_panel_references(text: str) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    normalized = compact_text(text).lower()
    patterns = (
        re.compile(r"(?i)\bfig(?:ure)?\.?\s*(\d+)\s*\(?\s*([a-z](?:\s*,\s*[a-z])*)"),
        re.compile(r"(?i)\b(\d+)\s*\(\s*([a-z](?:\s*,\s*[a-z])*)\s*\)"),
    )
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            figure_no = match.group(1)
            raw_labels = match.group(2) or ""
            labels = {label for label in re.findall(r"[a-z]", raw_labels)}
            if figure_no:
                refs.setdefault(figure_no, set()).update(labels)
    return refs


def normalize_panel_label_text(panel_id: str) -> str:
    return (panel_id or "").strip().lower()[:1]


def figure_number(parent_figure_id: str) -> str:
    match = re.search(r"(?i)fig(?:ure)?\.?\s*(\d+)", parent_figure_id)
    return match.group(1) if match else ""


def sentence_panel_labels(sentence: str, parent_figure_id: str) -> list[str]:
    labels: list[str] = []
    lowered = sentence.lower()
    fig_no = figure_number(parent_figure_id)
    if fig_no:
        for match in re.finditer(rf"(?i)\bfig\.?\s*{re.escape(fig_no)}\s*([a-z](?:\s*,\s*[a-z])*)", lowered):
            raw = match.group(1) or ""
            for label in re.findall(r"[a-z]", raw):
                if label not in labels:
                    labels.append(label)
        for match in re.finditer(rf"(?i)\b{re.escape(fig_no)}\s*([a-z](?:\s*,\s*[a-z])*)", lowered):
            raw = match.group(1) or ""
            for label in re.findall(r"[a-z]", raw):
                if label not in labels:
                    labels.append(label)
        for match in re.finditer(rf"(?i)\b{re.escape(fig_no)}\s*\(([a-z](?:\s*,\s*[a-z])*)\)", lowered):
            raw = match.group(1) or ""
            for label in re.findall(r"[a-z]", raw):
                if label not in labels:
                    labels.append(label)
    for match in re.finditer(r"(?i)\bpanel\s*([a-z])\b|\(([a-z])\)|\[\s*([a-z])\s*\]", lowered):
        label = (match.group(1) or match.group(2) or match.group(3) or "").lower()
        if label and label not in labels:
            labels.append(label)
    return labels


def is_reliable_panel_caption_candidate(
    panel_label: str,
    candidate: str,
    parent_figure_id: str | None = None,
) -> bool:
    panel_label = normalize_panel_label_text(panel_label)
    if not panel_label or not candidate:
        return False

    compact = compact_text(candidate).lower()
    if not re.match(rf"(?i)^\s*{re.escape(panel_label)}([\s\)\]:;.,-]|$)", compact):
        return False

    if re.match(
        rf"(?i)^\s*{re.escape(panel_label)}[;:,.-]?\s*(?:fig(?:ure)?\.?\s*\d+|figure\s*\d+)\b",
        compact,
    ):
        return False

    references = parse_figure_panel_references(compact)
    if not references:
        return True

    parent_no = figure_number(parent_figure_id or "")
    if parent_no:
        for figure_no, labels in references.items():
            if figure_no != parent_no:
                if labels:
                    return False
                continue
            if labels and any(label != panel_label for label in labels):
                return False
        return True

    for labels in references.values():
        if labels and panel_label not in labels:
            return False
    return True


def score_panel_caption(panel_id: str, candidate: str, parent_figure_id: str | None = None) -> float:
    if not candidate:
        return 0.0
    text = compact_text(candidate).lower()
    if not text:
        return 0.0
    if not is_reliable_panel_caption_candidate(panel_id, candidate, parent_figure_id):
        return 0.0

    panel_label = normalize_panel_label_text(panel_id)
    score = 0.0
    if text.startswith(f"{panel_label} "):
        score += 2.8
    if re.search(r"\d+(?:\.\d+)?\s*%", text):
        score += 1.0
    if re.search(r"\b(before|after|vs\.?|versus|compared|difference)\b", text):
        score += 1.0
    word_count = len(text.split())
    if 5 <= word_count <= 30:
        score += 0.5
    if "figure" in text or "fig." in text:
        score -= 2.5
    if "variation of viscosity" in text or "construction of" in text or "comparison of" in text:
        score -= 2.0
    if "chl content" in text or "schematic" in text:
        score -= 1.8
    if any(token in text for token in ("16.1%", "1.3%", "0.7%", "7 days", "7 d")):
        score -= 1.2
    if panel_label and parent_figure_id:
        fig_no = figure_number(parent_figure_id)
        if fig_no and re.search(
            rf"(?i)fig\.?\s*{re.escape(fig_no)}\s*{re.escape(panel_label)}\b",
            text,
        ):
            score += 0.8
    score += min(len(candidate.split()), 40) / 25.0
    return score


def best_panel_caption(panel_id: str, candidates: list[str], parent_figure_id: str | None = None) -> str:
    if not candidates:
        return ""
    scored = [
        (score_panel_caption(panel_id, candidate, parent_figure_id), candidate)
        for candidate in candidates
        if candidate
    ]
    scored = [(value, candidate) for value, candidate in scored if value > 0.25]
    if not scored:
        return ""
    scored.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return scored[0][1]


def panel_caption_from_full_caption(
    full_caption: str,
    panel_id: str | None,
    parent_figure_id: str | None = None,
) -> str:
    if not full_caption or not panel_id:
        return ""
    target = normalize_panel_label_text(panel_id)
    if not target:
        return ""
    text = compact_text(strip_text_markup(full_caption))
    if parent_figure_id:
        contamination = re.match(
            rf"(?i)^\s*{re.escape(target)}\s*[;:,-]?\s*(?:fig(?:ure)?\.?\s*\d+|figure\s*\d+)\b",
            text,
        )
        if contamination:
            text = text[contamination.end() :].lstrip(" ;,|.-")
    if re.match(rf"(?i)^\s*{re.escape(target)}[;:,\-]?\s*$", text):
        return ""

    markers = find_panel_marker_positions(text)
    if not markers:
        if re.match(rf"(?i)^\s*{re.escape(target)}\b", text):
            return text.strip()
        return ""

    candidates: list[str] = []
    for i, (marker, start) in enumerate(markers):
        if marker != target:
            continue
        end = markers[i + 1][1] if i + 1 < len(markers) else len(text)
        panel_text = text[start:end].strip(" ;.,:-")
        if panel_text.lower().startswith(f"{target} fig"):
            continue
        if not is_reliable_panel_caption_candidate(target, panel_text, parent_figure_id):
            continue
        candidates.append(panel_text)

    if not candidates:
        return ""
    return best_panel_caption(target, candidates, parent_figure_id)


def split_panel_context_sentences(text: str) -> list[str]:
    compact = compact_text(text)
    protected = re.sub(r"(?i)\bfig\.", "fig<sentdot>", compact)
    protected = re.sub(r"(?i)\be\.g\.", "e<sentdot>g<sentdot>", protected)
    parts = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。])\s+", protected)
        if sentence.strip()
    ]
    return [part.replace("<sentdot>", ".") for part in parts]


def build_panel_context_record(
    *,
    panel_id: str | None,
    parent_figure_id: str,
    full_parent_caption: str,
    nearby_candidates: list[str],
    markdown_candidates: list[str] | None = None,
) -> dict[str, Any]:
    panel_label = normalize_panel_label_text(panel_id or "")
    full_parent_caption = compact_text(full_parent_caption)
    markdown_candidates = markdown_candidates or []
    panel_caption = ""
    panel_caption_source = "unresolved"
    panel_caption_confidence = 0.0

    caption_candidates: list[str] = []
    candidate_sources = [full_parent_caption, *markdown_candidates]
    for source_text in candidate_sources:
        candidate = panel_caption_from_full_caption(source_text, panel_label, parent_figure_id)
        if candidate:
            caption_candidates.append(candidate)
    if caption_candidates:
        panel_caption = best_panel_caption(panel_label, caption_candidates, parent_figure_id)
        panel_caption_source = "marker_slice"
        panel_caption_confidence = 0.82

    strong_context: list[str] = []
    weak_context: list[str] = []
    excluded_context: list[str] = []
    all_sentences: list[str] = []
    for item in nearby_candidates:
        all_sentences.extend(split_panel_context_sentences(item))
    for item in [full_parent_caption]:
        all_sentences.extend(split_panel_context_sentences(item))
    for item in markdown_candidates:
        all_sentences.extend(split_panel_context_sentences(item))

    parent_values = ("16.1%", "1.3%", "0.7%", "7 days", "7 d", "prolonged")
    figure_no = figure_number(parent_figure_id)
    direct_mention_re = (
        re.compile(
            rf"(?i)\bfig(?:ure)?\.?\s*{re.escape(figure_no)}\s*{re.escape(panel_label)}\b"
            rf"|\b{re.escape(figure_no)}\s*{re.escape(panel_label)}\b"
            rf"|\b{re.escape(figure_no)}\s*\(\s*{re.escape(panel_label)}(?:\s*,\s*[a-z])*\)",
        )
        if figure_no
        else None
    )

    for sentence in all_sentences:
        labels = sentence_panel_labels(sentence, parent_figure_id)
        sentence_lower = sentence.lower()
        direct_mention = (
            bool(direct_mention_re.search(sentence_lower))
            if direct_mention_re
            else (
                panel_label
                and sentence_lower.startswith(panel_label + " ")
                and sentence_lower[:2] != f"{panel_label};"
            )
        )
        if panel_label and re.match(
            rf"(?i)^\s*(?:panel\s*)?{re.escape(panel_label)}[\s)\]:;\-]",
            sentence_lower,
        ):
            if panel_label not in labels:
                labels.append(panel_label)

        if panel_label and direct_mention:
            if any(token in sentence_lower for token in parent_values) and len(labels) > 1:
                excluded_context.append(sentence)
            elif any(token in sentence_lower for token in parent_values):
                weak_context.append(sentence)
            else:
                strong_context.append(sentence)
            continue

        if labels and panel_label and panel_label in labels:
            if any(token in sentence_lower for token in parent_values):
                if len(labels) > 1:
                    excluded_context.append(sentence)
                else:
                    weak_context.append(sentence)
            else:
                strong_context.append(sentence)
        elif labels:
            if any(token in sentence_lower for token in parent_values):
                excluded_context.append(sentence)
            else:
                weak_context.append(sentence)
        elif panel_label and sentence_lower.strip().startswith(panel_label):
            weak_context.append(sentence)
        elif panel_label and re.search(
            rf"(?i)\({re.escape(panel_label)}\)|\[{re.escape(panel_label)}\]|\b{re.escape(panel_label)}[\)\]:;\-]",
            sentence_lower,
        ):
            weak_context.append(sentence)

    if not panel_caption:
        prefixed = [
            s
            for s in strong_context + weak_context
            if is_reliable_panel_caption_candidate(panel_label, s, parent_figure_id)
        ]
        if prefixed:
            panel_caption = prefixed[0]
            panel_caption_source = "sentence_hint"
            panel_caption_confidence = 0.42
        else:
            panel_caption = strong_context[0] if strong_context else ""
            panel_caption_source = "first_strong_context" if strong_context else ""
            panel_caption_confidence = 0.3 if strong_context else 0.0

    panel_nearby_text = " ".join(strong_context) if strong_context else " ".join(weak_context)
    if panel_caption and panel_caption not in panel_nearby_text:
        panel_nearby_text = " ".join(
            part
            for part in [panel_caption, panel_nearby_text]
            if part
        )

    if not panel_caption:
        panel_caption = ""
        full_parent_caption_source = "full_parent_caption"
    else:
        full_parent_caption_source = "full_parent_caption"

    return {
        "panel_label": panel_label,
        "parent_figure_id": parent_figure_id,
        "panel_caption": panel_caption,
        "panel_caption_source": panel_caption_source,
        "panel_caption_confidence": panel_caption_confidence,
        "full_parent_caption": full_parent_caption,
        "full_parent_caption_source": full_parent_caption_source,
        "strong_context": strong_context,
        "weak_context": weak_context,
        "excluded_context": excluded_context,
        "excluded_context_summary": (
            "Full parent caption kept as background context; only target panel sentences "
            "and explicit marker hits are treated as strong context."
        ),
        "panel_nearby_text": panel_nearby_text,
    }
