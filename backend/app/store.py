"""Persistence seam between the services/rules and whatever holds the data.

`Store` is the only thing the DB/seed track needs to implement (Postgres, or the
SQLite fallback) to replace `InMemoryStore`; routes, services and the eligibility
engine depend on nothing else. Methods are synchronous (handlers run in
FastAPI's threadpool) and return contract models from models_generated.
Filtering, sorting and paging happen above this seam on full lists, which is
fine for demo-scale data; a DB implementation may add pushdown later.
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol
from uuid import UUID

from .models import (
    Apparatus,
    AuditEvent,
    CapabilityDefinition,
    Classification,
    Credential,
    Facility,
    Incident,
    IncidentCategory,
    Personnel,
    Recommendation,
    RecommendationReview,
    Unit,
    UnitStatusEvent,
)


class VersionConflict(Exception):
    """Raised by save_incident when the stored version is not the expected one."""


@dataclass(frozen=True)
class ResourceSnapshot:
    """Everything the eligibility engine reads, captured at one instant."""

    taken_at: datetime
    units: list[Unit]
    personnel: list[Personnel]
    credentials: list[Credential]
    apparatus: list[Apparatus]
    facilities: list[Facility]
    capability_definitions: list[CapabilityDefinition]


@dataclass(frozen=True)
class IncidentExtra:
    """Intake data that has no home on the contract's Incident shape."""

    reported_category: IncidentCategory | None = None
    mock_scenario_id: str | None = None


class Store(Protocol):
    seed_version: str
    seed: int | None

    def ping(self) -> None:
        """Raise if the backing store is unreachable."""

    def reset(self) -> dict[str, int]:
        """Restore the deterministic seed (demo only). Returns per-entity counts."""

    def snapshot(self) -> ResourceSnapshot: ...
    def get_unit(self, unit_id: UUID) -> Unit | None: ...
    def list_units(self) -> list[Unit]: ...
    def save_unit(self, unit: Unit, expected_version: int) -> Unit:
        """Replace the stored unit iff its current version (None counts as 1) == expected_version, else VersionConflict."""
    def add_status_event(self, event: UnitStatusEvent) -> UnitStatusEvent: ...
    def list_facilities(self) -> list[Facility]: ...
    def list_capability_definitions(self) -> list[CapabilityDefinition]: ...

    def next_incident_number(self, year: int) -> str: ...
    def add_incident(self, incident: Incident, extra: IncidentExtra) -> Incident: ...
    def get_incident(self, incident_id: UUID) -> Incident | None: ...
    def get_incident_extra(self, incident_id: UUID) -> IncidentExtra: ...
    def list_incidents(self) -> list[Incident]: ...
    def save_incident(self, incident: Incident, expected_version: int) -> Incident:
        """Replace the stored incident iff its current version == expected_version, else VersionConflict."""

    def add_classification(self, c: Classification) -> Classification: ...
    def save_classification(self, c: Classification) -> Classification: ...
    def get_classification(self, classification_id: UUID) -> Classification | None: ...
    def list_classifications(self, incident_id: UUID) -> list[Classification]: ...

    def add_recommendation(self, r: Recommendation) -> Recommendation: ...
    def save_recommendation(self, r: Recommendation) -> Recommendation: ...
    def get_recommendation(self, recommendation_id: UUID) -> Recommendation | None: ...
    def list_recommendations(self, incident_id: UUID) -> list[Recommendation]: ...
    def list_all_recommendations(self) -> list[Recommendation]: ...

    def add_review(self, review: RecommendationReview) -> RecommendationReview: ...
    def list_reviews(self, recommendation_id: UUID) -> list[RecommendationReview]: ...

    def add_audit_event(self, event: AuditEvent) -> AuditEvent: ...
    def list_audit_events(self) -> list[AuditEvent]: ...


@dataclass
class _Seed:
    """What a seed loader must provide (see seed_standin.build_seed)."""

    version: str
    seed: int | None
    units: list[Unit]
    personnel: list[Personnel]
    credentials: list[Credential]
    apparatus: list[Apparatus]
    facilities: list[Facility]
    capability_definitions: list[CapabilityDefinition]


class InMemoryStore:
    """Process-local store. Everything is lost on restart; `reset()` reloads the seed."""

    def __init__(self, load_seed: Callable[[], _Seed], clock: Callable[[], datetime]):
        self._load_seed = load_seed
        self._clock = clock
        self._lock = threading.RLock()
        self.reset()

    # -- lifecycle -----------------------------------------------------------
    def ping(self) -> None:
        return None

    def reset(self) -> dict[str, int]:
        with self._lock:
            s = self._load_seed()
            self.seed_version, self.seed = s.version, s.seed
            self._units = {u.unitId: u for u in s.units}
            self._personnel = list(s.personnel)
            self._credentials = list(s.credentials)
            self._apparatus = list(s.apparatus)
            self._facilities = list(s.facilities)
            self._capdefs = list(s.capability_definitions)
            self._incidents: dict[UUID, Incident] = {}
            self._extras: dict[UUID, IncidentExtra] = {}
            self._classifications: dict[UUID, Classification] = {}
            self._recommendations: dict[UUID, Recommendation] = {}
            self._reviews: list[RecommendationReview] = []
            self._status_events: list[UnitStatusEvent] = []
            self._audit: list[AuditEvent] = []
            self._incident_counter = 0
            return {
                "units": len(s.units),
                "personnel": len(s.personnel),
                "credentials": len(s.credentials),
                "apparatus": len(s.apparatus),
                "facilities": len(s.facilities),
                "capabilityDefinitions": len(s.capability_definitions),
            }

    # -- resources -----------------------------------------------------------
    def snapshot(self) -> ResourceSnapshot:
        with self._lock:
            return ResourceSnapshot(
                taken_at=self._clock(),
                units=list(self._units.values()),
                personnel=list(self._personnel),
                credentials=list(self._credentials),
                apparatus=list(self._apparatus),
                facilities=list(self._facilities),
                capability_definitions=list(self._capdefs),
            )

    def get_unit(self, unit_id: UUID) -> Unit | None:
        return self._units.get(unit_id)

    def save_unit(self, unit: Unit, expected_version: int) -> Unit:
        with self._lock:
            if (self._units[unit.unitId].version or 1) != expected_version:
                raise VersionConflict()
            self._units[unit.unitId] = unit
            return unit

    def add_status_event(self, event: UnitStatusEvent) -> UnitStatusEvent:
        with self._lock:
            self._status_events.append(event)
            return event

    def list_units(self) -> list[Unit]:
        with self._lock:
            return list(self._units.values())

    def list_facilities(self) -> list[Facility]:
        with self._lock:
            return list(self._facilities)

    def list_capability_definitions(self) -> list[CapabilityDefinition]:
        with self._lock:
            return list(self._capdefs)

    # -- incidents -----------------------------------------------------------
    def next_incident_number(self, year: int) -> str:
        with self._lock:
            self._incident_counter += 1
            return f"INC-{year}-{self._incident_counter:06d}"

    def add_incident(self, incident: Incident, extra: IncidentExtra) -> Incident:
        with self._lock:
            self._incidents[incident.incidentId] = incident
            self._extras[incident.incidentId] = extra
            return incident

    def get_incident(self, incident_id: UUID) -> Incident | None:
        return self._incidents.get(incident_id)

    def get_incident_extra(self, incident_id: UUID) -> IncidentExtra:
        return self._extras.get(incident_id, IncidentExtra())

    def list_incidents(self) -> list[Incident]:
        with self._lock:
            return list(self._incidents.values())

    def save_incident(self, incident: Incident, expected_version: int) -> Incident:
        with self._lock:
            current = self._incidents[incident.incidentId]
            if current.version != expected_version:
                raise VersionConflict()
            self._incidents[incident.incidentId] = incident
            return incident

    # -- classifications -----------------------------------------------------
    def add_classification(self, c: Classification) -> Classification:
        with self._lock:
            self._classifications[c.classificationId] = c
            return c

    save_classification = add_classification

    def get_classification(self, classification_id: UUID) -> Classification | None:
        return self._classifications.get(classification_id)

    def list_classifications(self, incident_id: UUID) -> list[Classification]:
        with self._lock:
            return [c for c in self._classifications.values() if c.incidentId == incident_id]

    # -- recommendations -----------------------------------------------------
    def add_recommendation(self, r: Recommendation) -> Recommendation:
        with self._lock:
            self._recommendations[r.recommendationId] = r
            return r

    save_recommendation = add_recommendation

    def get_recommendation(self, recommendation_id: UUID) -> Recommendation | None:
        return self._recommendations.get(recommendation_id)

    def list_recommendations(self, incident_id: UUID) -> list[Recommendation]:
        with self._lock:
            return [r for r in self._recommendations.values() if r.incidentId == incident_id]

    def list_all_recommendations(self) -> list[Recommendation]:
        with self._lock:
            return list(self._recommendations.values())

    # -- reviews and audit -----------------------------------------------------
    def add_review(self, review: RecommendationReview) -> RecommendationReview:
        with self._lock:
            self._reviews.append(review)
            return review

    def list_reviews(self, recommendation_id: UUID) -> list[RecommendationReview]:
        with self._lock:
            return [r for r in self._reviews if r.recommendationId == recommendation_id]

    def add_audit_event(self, event: AuditEvent) -> AuditEvent:
        with self._lock:
            self._audit.append(event)
            return event

    def list_audit_events(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._audit)
