from __future__ import annotations

from typing import Any

from content_pipeline.llm.phase_adapter import (
    PhaseAdapter,
    PhaseAdaptResult,
    clean_payload_keys,
    decode_json_from_model_text,
)

INFERRED_IMAGE_KIND_FIELDS = ("image_kind", "image_type", "type", "kind", "visual_kind")


class ImageObservationAdapter(PhaseAdapter):
    def adapt_payload(self, raw: Any) -> PhaseAdaptResult:
        repairs: list[dict[str, Any]] = []

        if isinstance(raw, str):
            decoded = decode_json_from_model_text(raw)
            if decoded is None:
                repairs.append({"path": "$", "repair": "json_decode_failed"})
                return PhaseAdaptResult(
                    payload=self.fallback_payload(["image_observation_json_decode_failed"]),
                    repairs=repairs,
                )
            repairs.append({"path": "$", "repair": "json_string_to_object"})
            raw = decoded

        if not isinstance(raw, dict):
            repairs.append({"path": "$", "repair": "payload_not_dict", "from": type(raw).__name__})
            return PhaseAdaptResult(
                payload=self.fallback_payload(["image_observation_payload_not_dict"]),
                repairs=repairs,
            )

        cleaned, key_repairs = clean_payload_keys(raw)
        repairs.extend(key_repairs)

        if not cleaned:
            repairs.append({"path": "$", "repair": "payload_empty"})
            return PhaseAdaptResult(
                payload=self.fallback_payload(["image_observation_payload_empty"]),
                repairs=repairs,
            )

        if "image_kind" not in cleaned:
            for candidate_key in INFERRED_IMAGE_KIND_FIELDS:
                candidate = cleaned.get(candidate_key)
                if isinstance(candidate, str) and candidate.strip():
                    cleaned["image_kind"] = candidate.strip()
                    repairs.append({"path": "image_kind", "repair": "infer_image_kind_from_type_field", "from": candidate_key})
                    break

        if "confidence" not in cleaned:
            cleaned["confidence"] = 0.0
            repairs.append({"path": "confidence", "repair": "fill_known_required", "to": 0.0})

        cleaned.setdefault("visual_fact_candidates", [])
        cleaned.setdefault("observations", [])
        cleaned.setdefault("warnings", [])

        if isinstance(cleaned.get("observations"), str):
            cleaned["observations"] = [cleaned["observations"]]
            repairs.append({"path": "observations", "repair": "scalar_to_array", "from": "str"})

        if isinstance(cleaned.get("visual_fact_candidates"), str):
            cleaned["visual_fact_candidates"] = [cleaned["visual_fact_candidates"]]
            repairs.append({"path": "visual_fact_candidates", "repair": "scalar_to_array", "from": "str"})

        return PhaseAdaptResult(payload=cleaned, repairs=repairs)

    def fallback_payload(self, warnings: list[str] | None = None) -> dict[str, Any]:
        """Fallback excludes image_kind so the caller resolves it from context."""
        return {
            "confidence": 0.0,
            "visual_fact_candidates": [],
            "observations": [],
            "warnings": warnings or ["image_observation_unrecoverable"],
        }
