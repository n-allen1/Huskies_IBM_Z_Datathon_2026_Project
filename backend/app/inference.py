"""Seams for the AI modules, with deterministic defaults that need no model.

The AI track plugs in a PyTorch `Classifier` and an XGBoost-backed `EtaEstimator`
(both must fall back to the defaults here on failure). Neither can affect
eligibility: the classifier only produces a *suggestion* a human must confirm,
and the ETA estimator only orders units that already passed hard eligibility.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from .models import ClassificationSource, EtaMethod, GeoPoint, IncidentCategory, SuggestedPriority


class InferenceUnavailable(Exception):
    """Raised by a model-backed component that cannot produce a result right now."""


@dataclass(frozen=True)
class ClassificationResult:
    category: IncidentCategory
    source: ClassificationSource
    model_version: str
    confidence: float | None = None  # None = no calibrated probability available
    suggested_priority: SuggestedPriority | None = None
    limitations: list[str] = field(default_factory=list)
    top_similarity: float | None = None  # raw model similarity of the winning category, for evaluation
    margin: float | None = None  # winning similarity minus the runner-up's, for evaluation


class Classifier(Protocol):
    def classify(self, text: str) -> ClassificationResult: ...


@dataclass(frozen=True)
class EtaEstimate:
    seconds: int
    method: EtaMethod
    model_version: str


class EtaEstimator(Protocol):
    def estimate(self, origin: GeoPoint, destination: GeoPoint, *, at: datetime) -> EtaEstimate: ...


# --- defaults ---------------------------------------------------------------

_C = IncidentCategory
# Ordered: earlier categories win ties. Word-boundary regexes keep "fall" from matching "waterfall".
_KEYWORDS: list[tuple[IncidentCategory, list[str]]] = [
    (_C.medical_cardiac, ["chest pain", "cardiac", "heart attack", "not breathing", "no pulse", "unresponsive"]),
    (_C.vehicle_fire, ["car fire", "vehicle fire", "truck fire", "engine fire"]),
    (_C.wildland_fire, ["brush fire", "grass fire", "wildfire", "wildland"]),
    (_C.structure_fire, ["structure fire", "house fire", "building fire", "apartment fire", "smoke showing", "flames", "fire alarm"]),
    (_C.hazmat, ["hazmat", "chemical", "gas leak", "spill", "fumes", "toxic", "ammonia", "chlorine"]),
    (_C.technical_rescue, ["trapped", "confined space", "trench", "rope rescue", "collapse", "cliff"]),
    (_C.traffic_collision, ["crash", "collision", "rollover", "hit and run", "car accident"]),
    (_C.medical_trauma, ["stabbed", "gunshot", "bleeding", "fell from", "fractur", "head injury"]),
    (_C.assault, ["assault", "fight", "attacked"]),
    (_C.burglary, ["burglary", "break-in", "break in", "broke into", "intruder"]),
    (_C.domestic_disturbance, ["domestic", "yelling", "argument with"]),
    (_C.welfare_check, ["welfare check", "check on", "well-being"]),
    (_C.medical_other, ["sick", "unconscious", "dizzy", "injured", "seizure", "overdose"]),
]


class KeywordClassifier:
    """Rule-based fallback. Reports no confidence and no priority: it has no calibrated
    probability to offer, and priority policy belongs to the approved protocol, not this table."""

    VERSION = "keyword-rules-0.1.0"

    def describe(self) -> tuple[str, str]:
        return "ok", f"keyword rules {self.VERSION}"

    def classify(self, text: str) -> ClassificationResult:
        lowered = text.lower()
        best, best_hits = _C.other, 0
        for category, words in _KEYWORDS:
            hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}", lowered))
            if hits > best_hits:
                best, best_hits = category, hits
        notes = ["Keyword match on the narrative; no calibrated probability or priority is provided."]
        if best is _C.other:
            notes.append("No keyword matched; dispatcher must choose a category.")
        return ClassificationResult(
            category=best, source=ClassificationSource.rule_based, model_version=self.VERSION, limitations=notes,
        )


class HaversineEta:
    """Straight-line distance at an assumed average speed. Always available; the contract's
    straight_line_fallback. A real road-routing engine or XGBoost model replaces it via EtaEstimator."""

    VERSION = "haversine-v1"

    def describe(self) -> tuple[str, str]:
        return "ok", f"straight-line distance at {self.speed_kmh:g} km/h"

    def __init__(self, speed_kmh: float = 40.0):
        self.speed_kmh = speed_kmh

    def estimate(self, origin: GeoPoint, destination: GeoPoint, *, at: datetime) -> EtaEstimate:
        km = haversine_km(origin, destination)
        return EtaEstimate(
            seconds=round(km / self.speed_kmh * 3600), method=EtaMethod.straight_line_fallback, model_version=self.VERSION,
        )


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))
