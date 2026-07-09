from __future__ import annotations

from content_pipeline.mineru.panel_marker_detector import detect_panel_markers


def _markers(text: str) -> list[str]:
    return [item.marker for item in detect_panel_markers(text)]


def test_panel_marker_detector_rejects_scientific_symbols() -> None:
    assert _markers("p < 0.05 indicates significance") == []
    assert _markers("p value was reported") == []
    assert _markers("p-value was reported") == []
    assert _markers("n = 3 samples were tested") == []
    assert _markers("x axis and y axis labels are visible") == []
    assert _markers("y axis shows fluorescence intensity") == []
    assert _markers("z stack projection of confocal image") == []
    assert _markers("t test revealed significance") == []
    assert _markers("r = 0.95 correlation") == []
    assert _markers("p = 0.01 indicates significance") == []
    assert _markers("p ≥ 0.05 threshold") == []
    assert _markers("n number of samples = 3") == []


def test_panel_marker_detector_accepts_explicit_markers() -> None:
    assert _markers("(a) microscopy image; (b) fluorescence image") == ["a", "b"]
    assert _markers("a) before treatment b) after treatment") == ["a", "b"]


def test_panel_marker_detector_rejected_has_reason() -> None:
    from content_pipeline.mineru.panel_marker_detector import PanelMarkerDetector
    d = PanelMarkerDetector()

    def _top_reason(text: str, marker_char: str = "p") -> str | None:
        import re as _re
        for m in _re.finditer(rf"\b{marker_char}\b", text):
            item = d._candidate(text, m.group(), m.start(), m.end(), "test", 0.5)
            if item.rejection_reason:
                return item.rejection_reason
        return None

    r1 = _top_reason("p < 0.05 indicates significance")
    assert r1 and "statistical" in r1

    r2 = _top_reason("n = 3 samples were tested", "n")
    assert r2 and "statistical" in r2

    r3 = _top_reason("x axis label", "x")
    assert r3 and "statistical" in r3


def test_panel_marker_accepts_letter_period() -> None:
    assert _markers("a. microscopy image; b. fluorescence image") == ["a", "b"]


def test_panel_marker_accepts_letter_colon() -> None:
    assert _markers("a: microscopy image; b: fluorescence image") == ["a", "b"]
