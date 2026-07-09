from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class UnitDimension:
    name: str
    units: tuple[str, ...]
    metric_terms: tuple[str, ...]
    value_types: tuple[str, ...] = ("exact_numeric", "approximate_numeric", "trend")


_DIMENSIONS: tuple[UnitDimension, ...] = (
    UnitDimension("concentration", ("M", "mM", "uM", "nM", "mol/L", "mmol/L", "umol/L", "mg/L", "ug/mL", "mg/mL", "g/L", "ppm", "ppb"), ("concentration", "titer", "metabolite", "product", "substrate", "lactate", "glucose", "acetate", "ethanol", "nitrate", "phosphate", "ammonia")),
    UnitDimension("rate", ("1/s", "1/min", "1/h", "s^-1", "min^-1", "h^-1", "%/h", "%/min", "mg/L/h", "mg/L/min", "mmol/h", "mmol/L/h", "ppm/h", "ppm h^-1", "umol/m2/s", "g/m2/day"), ("rate", "flux", "removal rate", "fixation rate", "growth rate", "production rate", "uptake rate")),
    UnitDimension("time", ("ms", "s", "sec", "min", "h", "hr", "day", "d"), ("time", "duration", "lag", "half life", "response time", "retention time")),
    UnitDimension("cell_concentration", ("cells/mL", "cells/L", "cells/cm2", "cells/mm2", "CFU", "CFU/mL", "log CFU/mL", "MPN/mL"), ("cell density", "cell count", "CFU", "MPN", "colonization", "viable count")),
    UnitDimension("signal", ("a.u.", "AU", "RFU", "RLU", "OD600", "absorbance", "normalized intensity", "%", "fold", "ratio"), ("signal", "intensity", "fluorescence", "absorbance", "optical density", "OD600", "reporter", "response")),
    UnitDimension("fraction", ("%", "wt%", "mol%", "vol%", "fraction", "ratio", "fold"), ("fraction", "percentage", "efficiency", "yield", "viability", "survival", "retention", "removal", "degradation", "conversion", "coverage", "content")),
    UnitDimension("mass", ("pg", "ng", "ug", "mg", "g", "kg"), ("mass", "amount", "weight", "biomass")),
    UnitDimension("normalized_mass", ("ug/mg", "mg/g", "g/g", "mg/mg", "mg/cm2", "ug/cm2"), ("specific", "normalized", "content", "capacity", "loading")),
    UnitDimension("enzyme_activity", ("U", "IU", "U/mL", "U/mg", "IU/mL", "kat", "nkat", "umol/min"), ("enzyme activity", "specific activity", "catalytic activity", "activity")),
    UnitDimension("stress", ("Pa", "kPa", "MPa", "GPa"), ("strength", "stress", "modulus", "stiffness", "elastic")),
    UnitDimension("force", ("N", "mN", "uN"), ("force", "load", "peak load")),
    UnitDimension("viscosity", ("Pa.s", "mPa.s", "cP", "m2/s"), ("viscosity", "viscous")),
    UnitDimension("length", ("nm", "um", "mm", "cm", "m", "inch"), ("length", "diameter", "radius", "size", "pore size", "particle size", "zone", "thickness")),
    UnitDimension("area", ("nm2", "um2", "mm2", "cm2", "m2"), ("area", "surface area", "coverage")),
    UnitDimension("volume", ("uL", "mL", "L", "cm3", "mm3"), ("volume", "swelling volume")),
    UnitDimension("surface_area", ("m2/g", "cm2/g"), ("BET", "surface area", "specific surface area")),
    UnitDimension("temperature", ("degC", "C", "K"), ("temperature", "thermal")),
    UnitDimension("acidity", ("pH",), ("pH", "acidity")),
    UnitDimension("electrical", ("V", "mV", "A", "mA", "uA", "Ohm", "kOhm", "S/m", "mS/cm"), ("voltage", "current", "resistance", "conductivity", "electrical")),
    UnitDimension("pressure", ("Pa", "kPa", "MPa", "bar", "atm", "psi"), ("pressure",)),
)


def unit_dimensions() -> tuple[UnitDimension, ...]:
    return _DIMENSIONS


def dimension_for_unit(unit: Any) -> str:
    actual = _normalize_unit(unit)
    if not actual:
        return ""
    for dimension in _DIMENSIONS:
        if any(actual == _normalize_unit(item) for item in dimension.units):
            return dimension.name
    return ""


def expected_units_for_dimension(dimension_name: str) -> tuple[str, ...]:
    for dimension in _DIMENSIONS:
        if dimension.name == dimension_name:
            return dimension.units
    return ()


def expected_value_types_for_dimension(dimension_name: str) -> tuple[str, ...]:
    for dimension in _DIMENSIONS:
        if dimension.name == dimension_name:
            return dimension.value_types
    return ()


def infer_metric_dimension(metric_name: str, *, axis_label: str = "", evidence_text: str = "") -> str:
    text = _normalize_text(" ".join([metric_name, axis_label, evidence_text]))
    if not text:
        return ""
    rules = (
        ("concentration", "concentration"), ("titer", "concentration"),
        ("rate", "rate"), ("time", "time"), ("duration", "time"),
        ("density", "cell_concentration"), ("count", "cell_concentration"),
        ("intensity", "signal"), ("signal", "signal"), ("response", "signal"),
        ("efficiency", "fraction"), ("ratio", "fraction"), ("fraction", "fraction"), ("percent", "fraction"), ("content", "fraction"),
        ("activity", "enzyme_activity"),
        ("strength", "stress"), ("modulus", "stress"), ("stress", "stress"),
        ("load", "force"), ("force", "force"), ("viscosity", "viscosity"),
        ("size", "length"), ("diameter", "length"), ("thickness", "length"),
        ("area", "area"), ("volume", "volume"), ("temperature", "temperature"),
        ("ph", "acidity"), ("conductivity", "electrical"), ("current", "electrical"), ("voltage", "electrical"),
        ("pressure", "pressure"),
    )
    tokens = set(text.split())
    compact = text.replace(" ", "_")
    for term, dimension in rules:
        if term in tokens or compact.endswith(f"_{term}") or term in compact:
            return dimension
    for dimension in _DIMENSIONS:
        if any(_normalize_text(term) in text for term in dimension.metric_terms):
            return dimension.name
    return ""


def metric_unit_dimensions_compatible(metric_name: str, unit: str, *, axis_label: str = "", evidence_text: str = "") -> bool:
    unit_dimension = dimension_for_unit(unit)
    metric_dimension = infer_metric_dimension(metric_name, axis_label=axis_label, evidence_text=evidence_text)
    return bool(unit_dimension and metric_dimension and unit_dimension == metric_dimension)


def _normalize_text(value: Any) -> str:
    return " ".join(re.sub(r"[^a-zA-Z0-9%]+", " ", str(value or "").lower()).split())


def _normalize_unit(value: Any) -> str:
    text = str(value or "").strip().lower()
    for src, dst in {" ": "", "*": "", "·": "", "^": "", "per": "/"}.items():
        text = text.replace(src, dst)
    return text
