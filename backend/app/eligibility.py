"""Stage 1 (hard eligibility) and Stage 2 (prioritization) of the decision process.

Pure functions over a `ResourceSnapshot`: no I/O, no clock reads, no model calls
except the injected ETA estimator. Order of operations is the safety argument:

1. Hard eligibility decides who may be considered at all. A unit is eligible only if it can
   deploy (status, staffing, apparatus), provides at least one mandatory capability, and has
   no mandatory capability it nominally carries that is currently impaired. Nothing downstream
   (ETA, coverage, ML) can turn an ineligible unit into a ranked one.
2. Only eligible units get a rank, ordered by ETA, then coverage impact, then call sign.
3. The package is a greedy pick over the ranked eligible units; unmet mandatory
   requirements are reported, never papered over.

A facility owning equipment is not a deployable resource: capabilities come from
the unit's current status, staffing, qualified active crew and ready equipment.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from . import multi_incident
from .inference import EtaEstimator, EtaEstimate
from .models import (
    ApparatusReadiness,
    Candidate,
    Capability,
    CapabilityCheck,
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRequirement,
    CapabilityState,
    Credential,
    CredentialState,
    EmploymentStatus,
    EquipmentReadiness,
    Facility,
    GeoPoint,
    Package,
    Personnel,
    RankingFactor,
    Unit,
    UnitStatus,
    Apparatus,
)
from .store import ResourceSnapshot

S = CapabilityState


@dataclass(frozen=True)
class UnitView:
    unit: Unit
    crew: list[tuple[Personnel, list[Credential]]]
    apparatus: Apparatus | None
    station: Facility | None

    @property
    def active_crew(self) -> list[tuple[Personnel, list[Credential]]]:
        return [(p, c) for p, c in self.crew if p.employmentStatus == EmploymentStatus.active]


@dataclass(frozen=True)
class RawCheck:
    state: CapabilityState
    evidence: str
    expires_at: datetime | None = None


def build_views(snap: ResourceSnapshot) -> dict[UUID, UnitView]:
    people = {p.personnelId: p for p in snap.personnel}
    creds: dict[UUID, list[Credential]] = {}
    for c in snap.credentials:
        creds.setdefault(c.personnelId, []).append(c)
    apparatus = {a.apparatusId: a for a in snap.apparatus}
    stations = {f.facilityId: f for f in snap.facilities}
    return {
        u.unitId: UnitView(
            unit=u,
            crew=[(people[pid], creds.get(pid, [])) for pid in (u.crewPersonnelIds or []) if pid in people],
            apparatus=apparatus.get(u.apparatusId) if u.apparatusId else None,
            station=stations.get(u.stationId) if u.stationId else None,
        )
        for u in snap.units
    }


# --- capability evaluation ---------------------------------------------------

def _credential_ok(c: Credential, now: datetime) -> bool:
    return c.state == CredentialState.valid and (c.expiresAt is None or c.expiresAt > now)


def _check_qualification(defn: CapabilityDefinition, view: UnitView, now: datetime) -> RawCheck:
    wanted = set(defn.requiresCredentialDefinitionIds or [])
    if not wanted:
        return RawCheck(S.unavailable, "Capability defines no required credential; cannot be verified.")
    active = view.active_crew
    held = [(p, c) for p, creds in active for c in creds if c.credentialDefinitionId in wanted]
    valid = [(p, c) for p, c in held if _credential_ok(c, now)]
    label = "/".join(sorted(wanted))
    if valid:
        holders = len({p.personnelId for p, _ in valid})
        expiries = [c.expiresAt for _, c in valid]
        return RawCheck(
            S.available,
            f"{holders} of {len(active)} active crew hold a valid {label}.",
            None if None in expiries else max(expiries),
        )
    unverified = [c for _, c in held if c.state == CredentialState.unverified]
    if unverified:
        return RawCheck(S.unverified, f"{label} is held but not verified; unverified credentials are not accepted.")
    expired = [c for _, c in held if c.state == CredentialState.expired or (c.expiresAt and c.expiresAt <= now)]
    if expired:
        latest = max(c.expiresAt for c in expired if c.expiresAt) if any(c.expiresAt for c in expired) else None
        return RawCheck(S.expired, f"{label} expired" + (f" on {latest.date()}." if latest else "."), latest)
    if held:
        return RawCheck(S.unavailable, f"{label} is not in a usable state (revoked).")
    return RawCheck(S.missing, f"No active crew member holds {label}.")


def _check_equipment(defn: CapabilityDefinition, view: UnitView) -> RawCheck:
    app = view.apparatus
    items = [i for i in (app.equipment or []) if defn.capabilityId in (i.capabilityIds or [])] if app else []
    if not items:
        return RawCheck(S.missing, "No assigned apparatus carries this capability.")
    if app.readiness in (ApparatusReadiness.maintenance, ApparatusReadiness.out_of_service):
        return RawCheck(S.unavailable, f"Apparatus {app.identifier} is {app.readiness.value}.")
    ready = [i for i in items if i.readiness == EquipmentReadiness.ready]
    if not ready:
        worst = items[0]
        # 'limited' is deliberately not deployable: a mandatory capability must be fully ready.
        return RawCheck(S.unavailable, f"{worst.name} readiness is '{worst.readiness.value}'.")
    return RawCheck(S.available, f"{ready[0].name} is ready.")


def check_capability(defn: CapabilityDefinition, view: UnitView, now: datetime) -> RawCheck:
    if defn.kind == CapabilityKind.personnel_qualification:
        return _check_qualification(defn, view, now)
    if defn.kind in (CapabilityKind.apparatus, CapabilityKind.equipment):
        return _check_equipment(defn, view)
    if defn.kind == CapabilityKind.crew_size:
        n, need = len(view.active_crew), view.unit.minimumCrew or 1
        return RawCheck(S.available if n >= need else S.unavailable, f"{n} active crew, {need} required.")
    return RawCheck(S.unavailable, "Authority checks are not implemented in this build.")  # fail closed


def gate_reasons(view: UnitView) -> list[str]:
    """Unit-level conditions that make a unit undeployable regardless of capability."""
    unit, reasons = view.unit, []
    if unit.status != UnitStatus.available:
        reasons.append(f"Unit status is {unit.status.value}.")
    app = view.apparatus
    if app and app.readiness in (ApparatusReadiness.maintenance, ApparatusReadiness.out_of_service):
        reasons.append(f"Apparatus {app.identifier} is {app.readiness.value}.")
    need, have = unit.minimumCrew, len(view.active_crew)
    if need and have < need:
        reasons.append(f"Staffed {have} of {need} minimum crew.")
    return reasons


def unit_capabilities(view: UnitView, defs: list[CapabilityDefinition], now: datetime) -> list[Capability]:
    """Per-unit capability status for the API: raw checks, downgraded when the unit cannot deploy."""
    gate = gate_reasons(view)
    out = []
    for d in defs:
        raw = check_capability(d, view, now)
        state, evidence = raw.state, raw.evidence
        if gate and state == S.available:
            state, evidence = S.unavailable, f"{evidence} Unit cannot deploy: {' '.join(gate)}"
        out.append(Capability(
            capabilityId=d.capabilityId, state=state, evidence=evidence, expiresAt=raw.expires_at,
            provenance=view.unit.provenance,
        ))
    return out


# --- stage 1 + 2 --------------------------------------------------------------

@dataclass(eq=False)
class _Evaluated:
    view: UnitView
    raw: dict[str, RawCheck]
    gate: list[str]
    eta: EtaEstimate | None = None
    eligible: bool = False
    reasons: list[str] = field(default_factory=list)
    coverage: str | None = None
    leaves_station_empty: bool = False

    def provides(self, capability_id: str) -> bool:
        return self.raw[capability_id].state == S.available


@dataclass
class Evaluation:
    candidates: list[Candidate]
    package: Package | None
    unmet: list[str]
    eta_models: set[str]
    units_evaluated: int
    # Kept for the multi-incident allocator: the hard-eligible units (ranked) and this incident's mandatory requirements.
    eligible_units: list[_Evaluated] = field(default_factory=list)
    mandatory: list[CapabilityRequirement] = field(default_factory=list)


def _origin(view: UnitView) -> GeoPoint | None:
    return view.unit.location or (view.station.location if view.station else None)


def _coverage(views: dict[UUID, UnitView], ev: _Evaluated) -> tuple[str | None, bool]:
    unit = ev.view.unit
    if unit.stationId is None or ev.view.station is None:
        return None, False
    others = [
        v for v in views.values()
        if v.unit.unitId != unit.unitId and v.unit.stationId == unit.stationId
        and v.unit.unitType == unit.unitType and v.unit.status == UnitStatus.available
    ]
    name, kind = ev.view.station.name, unit.unitType or "unit"
    if not others:
        return f"Leaves {name} with no other available {kind} (by unit status).", True
    return f"{name} keeps {len(others)} other available {kind} (by unit status).", False


def evaluate(
    snap: ResourceSnapshot,
    requirements: list[CapabilityRequirement],
    destination: GeoPoint,
    estimator: EtaEstimator,
    now: datetime,
    *,
    excluded: set[UUID] = frozenset(),
    max_candidates: int = 10,
) -> Evaluation:
    views = build_views(snap)
    defs = {d.capabilityId: d for d in snap.capability_definitions}
    mandatory = [r for r in requirements if r.mandatory]

    evaluated: list[_Evaluated] = []
    for view in views.values():
        raw = {r.capabilityId: check_capability(defs[r.capabilityId], view, now) for r in requirements}
        ev = _Evaluated(view=view, raw=raw, gate=gate_reasons(view))
        if view.unit.unitId in excluded:
            ev.gate.append("Excluded by the dispatcher for this recommendation.")
        origin = _origin(view)
        if origin is None:
            ev.gate.append("Unit location unknown; arrival time cannot be estimated.")
        else:
            ev.eta = estimator.estimate(origin, destination, at=now)
        contributes = any(ev.provides(r.capabilityId) for r in mandatory)
        # A mandatory capability the unit nominally carries (anything but 'missing') yet cannot
        # field right now - expired or unverified credential, equipment down - blocks the whole
        # unit. 'missing' only means it is not built for that role and simply does not contribute.
        impaired = [r for r in mandatory if raw[r.capabilityId].state not in (S.available, S.missing)]
        ev.eligible = not ev.gate and contributes and not impaired
        if not ev.eligible:
            ev.reasons = list(ev.gate)
            for r in impaired:
                ev.reasons.append(f"{r.capabilityId}: {raw[r.capabilityId].state.value}. {raw[r.capabilityId].evidence}")
            if not ev.reasons:
                ev.reasons.append("Does not provide any mandatory capability for this incident.")
        ev.coverage, ev.leaves_station_empty = _coverage(views, ev)
        evaluated.append(ev)

    eligible = sorted(
        (e for e in evaluated if e.eligible),
        key=lambda e: (e.eta.seconds, e.leaves_station_empty, e.view.unit.callSign),
    )
    ineligible = sorted(
        (e for e in evaluated if not e.eligible),
        # Near misses first: units that carry mandatory capabilities but are blocked are what a dispatcher asks about.
        key=lambda e: (-sum(e.raw[r.capabilityId].state != S.missing for r in mandatory), e.view.unit.callSign),
    )
    package, unmet = _build_package(eligible, mandatory)

    shown = (eligible + ineligible)[:max_candidates]
    rank_of = {id(e): i for i, e in enumerate(eligible, start=1)}
    candidates = [_to_candidate(e, rank_of.get(id(e)), requirements, mandatory) for e in shown]
    return Evaluation(
        candidates=candidates, package=package, unmet=unmet,
        eta_models={e.eta.model_version for e in evaluated if e.eta}, units_evaluated=len(evaluated),
        eligible_units=eligible, mandatory=mandatory,
    )


def to_problem(incident_id: UUID, label: str, evaluation: Evaluation) -> multi_incident.Problem:
    """What the joint allocator may see of one incident: its mandatory needs and ONLY the units that passed hard eligibility."""
    needs = tuple(multi_incident.Need(r.capabilityId, r.quantity or 1) for r in evaluation.mandatory)
    wanted = {n.capability_id for n in needs}
    providers = tuple(
        multi_incident.Provider(
            e.view.unit.unitId, e.view.unit.callSign, e.eta.seconds,
            frozenset(c for c in wanted if e.provides(c)),
        )
        for e in evaluation.eligible_units
    )
    return multi_incident.Problem(incident_id, label, needs, providers)


def package_for(chosen: list[_Evaluated], mandatory: list[CapabilityRequirement], header: str) -> tuple[Package | None, list[str]]:
    """A package for units that were picked elsewhere (the joint plan): what they cover and what is still unmet."""
    assigned: dict[str, list[str]] = {}
    unmet: list[str] = []
    for r in mandatory:
        need = r.quantity or 1
        providers = [e for e in chosen if e.provides(r.capabilityId)]
        for e in providers:
            caps = assigned.setdefault(e.view.unit.callSign, [])
            if r.capabilityId not in caps:
                caps.append(r.capabilityId)
        if len(providers) < need:
            unmet.append(f"{r.capabilityId}: {len(providers)} of {need} required unit(s) allocated.")
    if not chosen:
        return None, unmet
    parts = "; ".join(f"{sign} covers {', '.join(caps)}" for sign, caps in assigned.items())
    text = f"{header} {parts}."
    if unmet:
        text += " NOT SATISFIED: " + " ".join(unmet)
    return Package(
        packageId=uuid4(), unitIds=[e.view.unit.unitId for e in chosen], satisfiesAllMandatory=not unmet,
        worstEtaSeconds=max(e.eta.seconds for e in chosen), explanation=text,
    ), unmet


def _to_candidate(e: _Evaluated, rank: int | None, requirements, mandatory) -> Candidate:
    unit = e.view.unit
    checks = []
    for r in requirements:
        raw = e.raw[r.capabilityId]
        state, evidence = raw.state, raw.evidence
        if e.gate and state == S.available:
            state, evidence = S.unavailable, f"{evidence} Unit cannot deploy."
        checks.append(CapabilityCheck(
            capabilityId=r.capabilityId, state=state, evidence=evidence, expiresAt=raw.expires_at, mandatory=r.mandatory,
        ))
    factors = None
    if e.eligible:
        met = [r.capabilityId for r in mandatory if e.provides(r.capabilityId)]
        gaps = [f"{r.capabilityId} ({e.raw[r.capabilityId].state.value})" for r in mandatory if not e.provides(r.capabilityId)]
        factors = [
            RankingFactor(factor="eta", value=f"{e.eta.seconds} s", note=f"Method: {e.eta.method.value} ({e.eta.model_version})."),
            RankingFactor(
                factor="mandatory_requirements_provided", value=f"{len(met)} of {len(mandatory)}",
                note=("Not provided: " + ", ".join(gaps)) if gaps else None,
            ),
            RankingFactor(factor="coverage_impact", value="leaves station empty" if e.leaves_station_empty else "station retains cover"),
        ]
    return Candidate(
        unitId=unit.unitId, callSign=unit.callSign, eligible=e.eligible, rank=rank,
        etaSeconds=e.eta.seconds if e.eta else None, etaMethod=e.eta.method if e.eta else None,
        checks=checks, ineligibleReasons=e.reasons or None, rankingFactors=factors,
        coverageImpact=e.coverage, dataFreshness=unit.provenance,
    )


def _build_package(eligible: list[_Evaluated], mandatory: list[CapabilityRequirement]) -> tuple[Package | None, list[str]]:
    """Greedy cover of the mandatory requirements using ranked eligible units.

    Scarcest requirement first, so a rare capability is not starved by a common
    one. Not optimal (set cover is NP-hard; the exact joint allocation across
    incidents is multi_incident.py) but always honest: whatever cannot be filled is listed."""
    providers = {r.capabilityId: [e for e in eligible if e.provides(r.capabilityId)] for r in mandatory}
    chosen: list[_Evaluated] = []
    assigned: dict[str, list[str]] = {}
    unmet: list[str] = []
    for r in sorted(mandatory, key=lambda r: (len(providers[r.capabilityId]), r.capabilityId)):
        need = r.quantity or 1
        have = sum(e.provides(r.capabilityId) for e in chosen)
        for e in providers[r.capabilityId]:
            if have >= need:
                break
            if e not in chosen:
                chosen.append(e)
                have += 1
        for e in chosen:
            if e.provides(r.capabilityId):
                assigned.setdefault(e.view.unit.callSign, [])
                if r.capabilityId not in assigned[e.view.unit.callSign]:
                    assigned[e.view.unit.callSign].append(r.capabilityId)
        if have < need:
            unmet.append(f"{r.capabilityId}: {have} of {need} required unit(s) available.")
    if not chosen:
        return None, unmet
    parts = "; ".join(f"{sign} covers {', '.join(caps)}" for sign, caps in assigned.items())
    text = f"Greedy cover by ETA, scarcest requirement first. {parts}."
    if unmet:
        text += " NOT SATISFIED: " + " ".join(unmet)
    return Package(
        packageId=uuid4(), unitIds=[e.view.unit.unitId for e in chosen], satisfiesAllMandatory=not unmet,
        worstEtaSeconds=max(e.eta.seconds for e in chosen), explanation=text,
    ), unmet
