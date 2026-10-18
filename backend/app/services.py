"""Use-case orchestration. Routes stay thin; every rule about incident lifecycle,
classification handling and recommendation generation lives here."""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from uuid import UUID, uuid4

from . import eligibility, multi_incident, protocol
from .config import Settings
from .errors import ApiError
from .events import EventBus
from .idempotency import IdempotencyCache
from .inference import Classifier, EtaEstimator, InferenceUnavailable
from .models import (
    AuditEvent,
    AuditOutcome,
    Classification,
    ClassificationCreate,
    ClassificationMode,
    ClassificationSource,
    ClassificationStatus,
    DataOrigin,
    ErrorCode,
    Incident,
    IncidentCategory,
    IncidentCreate,
    IncidentPriority,
    IncidentStatus,
    IncidentUpdate,
    Inputs,
    Provenance,
    Recommendation,
    PreviousStatus,
    RecommendationCreate,
    RecommendationReview,
    RecommendationScope,
    RecommendationStage,
    RecommendationStatus,
    RequirementSet,
    ReviewCreate,
    ReviewDecision,
    Role,
    UnitStatus,
    UnitStatusEvent,
    UnitStatusEventCreate,
    User,
    VerificationStatus,
)
from .store import IncidentExtra, Store, VersionConflict

IS = IncidentStatus
_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IS.intake: {IS.pending_dispatch, IS.cancelled},
    IS.pending_dispatch: {IS.recommendation_ready, IS.cancelled},
    IS.recommendation_ready: {IS.pending_dispatch, IS.dispatched_simulated, IS.cancelled},
    IS.dispatched_simulated: {IS.on_scene_simulated, IS.resolved, IS.cancelled},
    IS.on_scene_simulated: {IS.resolved},
    IS.resolved: set(),
    IS.cancelled: set(),
}
_TERMINAL = {IS.resolved, IS.cancelled}
_PRIORITY_CONFIRMERS = {Role.dispatcher, Role.supervisor}
RULES_VERSION = "eligibility-rules-0.1.0"
MULTI_INCIDENT_VERSION = "multi-incident-allocation-0.1.0"
# One joint solve per request: five incidents is far beyond the 13-unit demo fleet and keeps the exact solver instant.
MAX_JOINT_INCIDENTS = 5
# Who wins a contested unit. Not inferred by the system: it is the priority a person set on the incident.
_PRIORITY_RANK = {IncidentPriority.P1: 0, IncidentPriority.P2: 1, IncidentPriority.P3: 2, IncidentPriority.P4: 3}


@dataclass
class Services:
    store: Store
    settings: Settings
    classifier: Classifier
    estimator: EtaEstimator
    clock: Callable[[], datetime]
    idempotency: IdempotencyCache
    events: EventBus
    # Serializes the operations that change which units are available (assignments, status events), so two
    # dispatchers cannot both claim the same unit.
    dispatch_lock: threading.RLock = field(default_factory=threading.RLock)

    # -- helpers -------------------------------------------------------------
    def get_incident(self, incident_id: UUID) -> Incident:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise ApiError(ErrorCode.NOT_FOUND, "Incident not found.")
        return incident

    def _publish(self, type_: str, entity_type: str, entity_id: UUID, version: int, **payload) -> None:
        self.events.publish(type_, entity_type, str(entity_id), version, payload)

    def _audit(self, user: User, action: str, entity_type: str, entity_id: UUID | str | None, **detail) -> None:
        self.store.add_audit_event(AuditEvent(
            auditId=uuid4(), occurredAt=self.clock(), actorId=user.userId, actorRoles=user.roles, action=action,
            entityType=entity_type, entityId=str(entity_id) if entity_id else None, outcome=AuditOutcome.success,
            tenantId=user.tenantId, detail=detail or None,
        ))

    def _save(self, incident: Incident, expected_version: int, **changes) -> Incident:
        updated = incident.model_copy(update={**changes, "version": incident.version + 1, "updatedAt": self.clock()})
        try:
            saved = self.store.save_incident(updated, expected_version)
        except VersionConflict:
            raise ApiError(ErrorCode.CONFLICT, "The incident was modified by someone else; reload and retry.")
        self._publish(
            "incident.updated", "incident", saved.incidentId, saved.version, number=saved.number, status=saved.status.value,
            priority=saved.priority.value,
        )
        return saved

    def _invalidate(self, rec: Recommendation, reason: str) -> Recommendation:
        updated = self.store.save_recommendation(
            rec.model_copy(update={"status": RecommendationStatus.invalidated, "invalidatedReason": reason})
        )
        self._publish("recommendation.invalidated", "recommendation", rec.recommendationId, 1, incidentId=str(rec.incidentId), reason=reason)
        return updated

    def _invalidate_pending(self, incident_id: UUID, reason: str) -> list[Recommendation]:
        """Anything a dispatcher could still act on is stale once its inputs change."""
        return [
            self._invalidate(rec, reason) for rec in self.store.list_recommendations(incident_id)
            if rec.status == RecommendationStatus.pending_review
        ]

    def _invalidate_for_units(self, unit_ids: set[UUID], reason: str, *, except_id: UUID | None = None) -> list[Recommendation]:
        """Pending recommendations whose recommended package uses any of these units are no longer actionable."""
        stale = []
        for rec in self.store.list_all_recommendations():
            if rec.status != RecommendationStatus.pending_review or rec.recommendationId == except_id:
                continue
            if any(unit_ids & set(p.unitIds) for p in rec.packages or []):
                stale.append(self._invalidate(rec, reason))
        return stale

    def _confirmed_category(self, incident: Incident) -> tuple[IncidentCategory | None, bool, UUID | None]:
        """(category, human_confirmed, classification id). ML suggestions never drive requirements."""
        if incident.confirmedCategory:
            basis = [
                c for c in self.store.list_classifications(incident.incidentId)
                if c.status == ClassificationStatus.confirmed and c.category == incident.confirmedCategory
            ]
            basis.sort(key=lambda c: c.createdAt)
            return incident.confirmedCategory, True, basis[-1].classificationId if basis else None
        reported = self.store.get_incident_extra(incident.incidentId).reported_category
        return reported, False, None

    # -- incidents -----------------------------------------------------------
    def create_incident(self, user: User, body: IncidentCreate) -> Incident:
        now = self.clock()
        scenario = body.mockScenarioId
        incident = Incident(
            incidentId=uuid4(),
            tenantId=user.tenantId,
            number=self.store.next_incident_number(now.year),
            status=IS.intake,
            priority=IncidentPriority(body.priority.value) if body.priority else IncidentPriority.unassigned,
            priorityConfirmed=False,
            narrative=body.narrative,
            location=body.location,
            callbackNumber=body.callbackNumber,
            version=1,
            createdAt=now,
            updatedAt=now,
            createdBy=user.userId,
            provenance=Provenance(
                dataOrigin=DataOrigin.simulated if scenario else DataOrigin.user_entered,
                sourceSystem=f"dispatchz-scenario-{scenario}" if scenario else "dispatchz-api",
                sourceTimestamp=now,
                verificationStatus=VerificationStatus.unverified,
            ),
        )
        saved = self.store.add_incident(
            incident, IncidentExtra(reported_category=body.reportedCategory, mock_scenario_id=scenario)
        )
        self._publish(
            "incident.created", "incident", saved.incidentId, saved.version, number=saved.number,
            status=saved.status.value, priority=saved.priority.value,
        )
        self._audit(user, "incident.create", "incident", saved.incidentId, number=saved.number, scenario=scenario)
        return saved

    def update_incident(self, user: User, incident_id: UUID, body: IncidentUpdate) -> Incident:
        current = self.get_incident(incident_id)
        if current.version != body.version:
            raise ApiError(ErrorCode.CONFLICT, f"Incident is at version {current.version}, not {body.version}.")
        if current.status in _TERMINAL:
            raise ApiError(ErrorCode.INVALID_STATE_TRANSITION, f"Incident is {current.status.value} and cannot be modified.")

        changes: dict = {}
        invalidate: str | None = None

        if body.status is not None and body.status != current.status:
            if body.status not in _TRANSITIONS[current.status]:
                raise ApiError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"Cannot move an incident from {current.status.value} to {body.status.value}.",
                )
            changes["status"] = body.status
            if body.status in _TERMINAL:
                invalidate = f"Incident {body.status.value}."
        if body.narrative is not None:
            if not body.narrative.strip():
                raise ApiError(ErrorCode.VALIDATION_ERROR, "Invalid narrative.", [("narrative", "Must not be empty.")])
            changes["narrative"] = body.narrative
            invalidate = "Incident narrative changed."
        if body.location is not None:
            changes["location"] = body.location
            invalidate = "Incident location changed."
        if body.callbackNumber is not None:
            changes["callbackNumber"] = body.callbackNumber
        if body.confirmedCategory is not None and body.confirmedCategory != current.confirmedCategory:
            changes["confirmedCategory"] = body.confirmedCategory
            invalidate = "Incident category changed."

        # Priority: only dispatchers/supervisors may confirm; any change to a confirmed priority needs re-confirmation.
        new_priority = IncidentPriority(body.priority.value) if body.priority else current.priority
        if body.priority is not None and new_priority != current.priority:
            changes["priority"] = new_priority
            changes["priorityConfirmed"] = False
        if body.priorityConfirmed is not None:
            if body.priorityConfirmed:
                if not _PRIORITY_CONFIRMERS.intersection(user.roles):
                    raise ApiError(ErrorCode.FORBIDDEN, "Only dispatchers and supervisors may confirm priority.")
                if new_priority == IncidentPriority.unassigned:
                    raise ApiError(
                        ErrorCode.VALIDATION_ERROR, "Cannot confirm an unassigned priority.",
                        [("priorityConfirmed", "Set a priority first.")],
                    )
            changes["priorityConfirmed"] = body.priorityConfirmed

        if not changes:
            return current
        saved = self._save(current, body.version, **changes)
        if invalidate:
            self._invalidate_pending(incident_id, invalidate)
        self._audit(user, "incident.update", "incident", incident_id, fields=sorted(changes))
        return saved

    # -- classification ------------------------------------------------------
    def create_classification(self, user: User, incident_id: UUID, body: ClassificationCreate) -> Classification:
        incident = self.get_incident(incident_id)
        if incident.status in _TERMINAL:
            raise ApiError(ErrorCode.INVALID_STATE_TRANSITION, f"Incident is {incident.status.value}.")
        now = self.clock()

        if body.mode == ClassificationMode.infer:
            try:
                result = self.classifier.classify(incident.narrative)
            except InferenceUnavailable as exc:
                raise ApiError(ErrorCode.INFERENCE_UNAVAILABLE, f"Classification is unavailable ({exc}); classify manually.")
            for earlier in self.store.list_classifications(incident_id):
                if earlier.status == ClassificationStatus.suggested:
                    self.store.save_classification(earlier.model_copy(update={"status": ClassificationStatus.superseded}))
            suggestion = self.store.add_classification(Classification(
                classificationId=uuid4(), incidentId=incident_id, source=result.source, category=result.category,
                suggestedPriority=result.suggested_priority, confidence=result.confidence,
                modelVersion=result.model_version, policyVersionId=protocol.POLICY_ID,
                status=ClassificationStatus.suggested, createdAt=now, limitations=result.limitations or None,
            ))
            self._audit(
                user, "classification.infer", "incident", incident_id, category=result.category.value,
                source=result.source.value, modelVersion=result.model_version,
            )
            return suggestion

        inferred = None
        if body.inferredClassificationId is not None:
            inferred = self.store.get_classification(body.inferredClassificationId)
            if inferred is None or inferred.incidentId != incident_id:
                raise ApiError(ErrorCode.NOT_FOUND, "Inferred classification not found for this incident.")
            if inferred.status != ClassificationStatus.suggested:
                raise ApiError(ErrorCode.CONFLICT, f"That classification is already {inferred.status.value}.")

        if body.mode == ClassificationMode.reject:
            if inferred is None:
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR, "Nothing to reject.",
                    [("inferredClassificationId", "Required when mode is reject.")],
                )
            rejected = self.store.save_classification(inferred.model_copy(update={"status": ClassificationStatus.rejected}))
            self._audit(user, "classification.reject", "incident", incident_id, rejectedCategory=inferred.category.value)
            return rejected

        # confirm: the human's category wins over the suggestion when they differ.
        category = body.category or (inferred.category if inferred else None)
        if category is None:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR, "No category to confirm.",
                [("category", "Provide category or inferredClassificationId.")],
            )
        changes: dict = {"confirmedCategory": category}
        if body.priority is not None:
            if not _PRIORITY_CONFIRMERS.intersection(user.roles):
                raise ApiError(ErrorCode.FORBIDDEN, "Only dispatchers and supervisors may confirm priority.")
            changes.update(priority=IncidentPriority(body.priority.value), priorityConfirmed=True)
        if incident.status == IS.intake:
            changes["status"] = IS.pending_dispatch
        category_changed = incident.confirmedCategory != category

        confirmed = Classification(
            classificationId=uuid4(), incidentId=incident_id, source=ClassificationSource.dispatcher_confirmed,
            category=category, suggestedPriority=None, policyVersionId=protocol.POLICY_ID,
            status=ClassificationStatus.confirmed, confirmedBy=user.userId, createdAt=now,
        )
        self._save(incident, incident.version, **changes)
        if inferred is not None:
            same = inferred.category == category
            self.store.save_classification(inferred.model_copy(
                update={"status": ClassificationStatus.confirmed if same else ClassificationStatus.superseded}
            ))
        if category_changed:
            self._invalidate_pending(incident_id, "Incident category changed.")
        self._audit(
            user, "classification.confirm", "incident", incident_id, category=category.value,
            overridesSuggestion=bool(inferred is not None and inferred.category != category),
        )
        return self.store.add_classification(confirmed)

    # -- requirements --------------------------------------------------------
    def requirements(self, incident_id: UUID) -> RequirementSet:
        incident = self.get_incident(incident_id)
        category, _, basis = self._confirmed_category(incident)
        reqs = protocol.requirements_for(category, self.store.list_capability_definitions()) if category else []
        return RequirementSet(
            incidentId=incident_id, policyVersionId=protocol.POLICY_ID, generatedAt=self.clock(),
            requirements=reqs, basedOnClassificationId=basis,
        )

    # -- recommendations -----------------------------------------------------
    def create_recommendation(self, user: User, incident_id: UUID, body: RecommendationCreate) -> Recommendation:
        incident = self.get_incident(incident_id)
        if incident.status in _TERMINAL:
            raise ApiError(ErrorCode.INVALID_STATE_TRANSITION, f"Incident is {incident.status.value}.")
        joint = self._joint_members(incident, body)
        if body.policyVersionId is not None and body.policyVersionId != protocol.POLICY_ID:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR, "Unknown policy version.",
                [("policyVersionId", f"Only {protocol.POLICY_ID} is active.")],
            )

        snapshot = self.store.snapshot()
        max_candidates = body.maxCandidates or 10
        excluded = set(body.excludeUnitIds or [])
        category, confirmed, basis, evaluation = self._evaluate_incident(incident, snapshot, excluded, max_candidates, primary=True)

        package, stage, optimizer = evaluation.package, RecommendationStage.prioritization, None
        unmet, joint_limitations, joint_versions = evaluation.unmet, [], []
        if joint:
            others = [(i, self._evaluate_incident(i, snapshot, excluded, max_candidates, primary=False)[3]) for i in joint]
            package, unmet, optimizer, joint_limitations = self._joint_plan(incident, evaluation, others)
            stage, joint_versions = RecommendationStage.multi_incident_optimization, [MULTI_INCIDENT_VERSION]

        limitations = [
            "Decision support only: nothing is dispatched until a dispatcher reviews this recommendation.",
            "All unit, crew, credential and equipment data is synthetic.",
            "Coverage impact is judged from unit status only, not from crew qualifications.",
        ]
        if any(c.etaMethod and c.etaMethod.value == "straight_line_fallback" for c in evaluation.candidates):
            limitations.append(
                f"ETAs are straight-line distance at an assumed {self.settings.assumed_speed_kmh:g} km/h, not road routing."
            )
        if any(c.etaMethod and c.etaMethod.value == "xgboost" for c in evaluation.candidates):
            limitations.append(
                "ETAs are predicted by an XGBoost model trained on synthetic travel times (not live traffic or road routing); "
                "they only order units that already passed eligibility."
            )
        if not confirmed:
            limitations.append(
                f"Category '{category.value}' is the caller-reported value and has not been confirmed by a dispatcher."
            )
        limitations += joint_limitations
        limitations += [f"Unmet mandatory requirement - {u}" for u in unmet]
        if evaluation.units_evaluated > max_candidates:
            limitations.append(f"Showing {max_candidates} of {evaluation.units_evaluated} units.")

        now = self.clock()
        recommendation = Recommendation(
            recommendationId=uuid4(), incidentId=incident_id, status=RecommendationStatus.pending_review,
            stage=stage, candidates=evaluation.candidates,
            packages=[package] if package else None,
            inputs=Inputs(
                requirementSetGeneratedAt=now, unitSnapshotAt=snapshot.taken_at, classificationId=basis,
                modelVersions=[f"dispatch-protocol-{protocol.POLICY_VERSION}", RULES_VERSION, *joint_versions, *sorted(evaluation.eta_models)],
                optimizer=optimizer,
            ),
            policyVersionId=protocol.POLICY_ID, limitations=limitations, createdAt=now,
            expiresAt=now + timedelta(seconds=self.settings.recommendation_ttl_seconds),
            provenance=Provenance(
                dataOrigin=DataOrigin.simulated, sourceSystem=f"dispatchz-rules-engine-{RULES_VERSION}",
                sourceTimestamp=snapshot.taken_at, verificationStatus=VerificationStatus.unverified,
                stale=False, staleAfterSeconds=self.settings.recommendation_ttl_seconds,
            ),
            humanConfirmationRequired=True,
        )
        # A newer recommendation replaces older pending ones for this incident.
        self._invalidate_pending(incident_id, f"Superseded by recommendation {recommendation.recommendationId}.")
        self.store.add_recommendation(recommendation)
        eligible = [c for c in evaluation.candidates if c.eligible]
        self._publish(
            "recommendation.created", "recommendation", recommendation.recommendationId, 1, incidentId=str(incident_id),
            eligibleCandidates=len(eligible),
            satisfiesAllMandatory=bool(package and package.satisfiesAllMandatory),
        )
        self._audit(
            user, "recommendation.create", "recommendation", recommendation.recommendationId, incidentId=str(incident_id),
            eligibleCandidates=len(eligible), unmetMandatory=unmet, modelVersions=recommendation.inputs.modelVersions,
            scope="multi_incident" if joint else "single_incident", optimizer=optimizer,
            jointIncidentIds=[str(i.incidentId) for i in joint] or None,
        )
        if incident.status in (IS.intake, IS.pending_dispatch):
            self._save(incident, incident.version, status=IS.recommendation_ready)
        return recommendation

    def _joint_members(self, primary: Incident, body: RecommendationCreate) -> list[Incident]:
        """The other incidents of a multi_incident request, validated. Empty for an ordinary single-incident request."""
        extra = list(body.additionalIncidentIds or [])
        if body.scope != RecommendationScope.multi_incident:
            if extra:
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR, "additionalIncidentIds only applies to scope multi_incident.",
                    [("additionalIncidentIds", "Set scope to multi_incident, or remove this field.")],
                )
            return []
        if not extra:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR, "scope multi_incident needs at least one other incident to allocate units across.",
                [("additionalIncidentIds", "Provide the other incident ids.")],
            )
        if len(set(extra)) != len(extra) or primary.incidentId in extra:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR, "Each incident may appear only once.",
                [("additionalIncidentIds", "Contains a duplicate, or the incident being recommended for.")],
            )
        if len(extra) + 1 > MAX_JOINT_INCIDENTS:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR, f"At most {MAX_JOINT_INCIDENTS} incidents can be planned together.",
                [("additionalIncidentIds", f"Provide at most {MAX_JOINT_INCIDENTS - 1} ids.")],
            )
        members = [self.get_incident(i) for i in extra]  # 404 if one does not exist
        for member in members:
            if member.status in _TERMINAL:
                raise ApiError(ErrorCode.INVALID_STATE_TRANSITION, f"Incident {member.number} is {member.status.value}.")
        return members

    def _evaluate_incident(self, incident: Incident, snapshot, excluded: set[UUID], max_candidates: int, *, primary: bool):
        """Category -> protocol requirements -> hard eligibility and ranking for one incident."""
        who = "Incident" if primary else f"Incident {incident.number}"
        category, confirmed, basis = self._confirmed_category(incident)
        if category is None:
            raise ApiError(
                ErrorCode.CONFLICT, f"{who} has no category; confirm a classification before requesting a recommendation."
            )
        reqs = protocol.requirements_for(category, snapshot.capability_definitions)
        if not reqs:
            raise ApiError(
                ErrorCode.CONFLICT,
                f"The protocol defines no requirements for '{category.value}'; classify the incident." if primary
                else f"The protocol defines no requirements for incident {incident.number} ('{category.value}'); classify it.",
            )
        evaluation = eligibility.evaluate(
            snapshot, reqs, incident.location.point, self.estimator, snapshot.taken_at,
            excluded=excluded, max_candidates=max_candidates,
        )
        return category, confirmed, basis, evaluation

    def _joint_plan(self, primary: Incident, primary_eval, others):
        """Allocate units across the primary and the other incidents; return the primary's part of the plan.

        The same inputs give the same plan whichever incident asks, so asking again from another incident returns that
        incident's part of this same allocation. Nothing is stored or changed for the other incidents."""
        members = [(primary, primary_eval), *others]
        members.sort(key=lambda m: (_PRIORITY_RANK.get(m[0].priority, len(_PRIORITY_RANK)), m[0].createdAt, m[0].number))
        problems = [eligibility.to_problem(inc.incidentId, inc.number, ev) for inc, ev in members]
        plan = multi_incident.optimize(problems)

        part = {}
        for inc, ev in members:
            ids = set(plan.assignment[inc.incidentId])
            chosen = [e for e in ev.eligible_units if e.view.unit.unitId in ids]
            part[inc.incidentId] = (chosen, eligibility.package_for(chosen, ev.mandatory, "")[1])

        numbers = ", ".join(f"{inc.number} ({inc.priority.value})" for inc, _ in members)
        summary = " ".join(
            f"{inc.number}: {', '.join(e.view.unit.callSign for e in part[inc.incidentId][0]) or 'no units'}"
            f"{'' if not part[inc.incidentId][1] else ' (NOT fully staffed)'}."
            for inc, _ in members
        )
        header = f"Joint plan for {numbers}, highest priority first, by {plan.optimizer}. {summary} For this incident:"
        package, unmet = eligibility.package_for(part[primary.incidentId][0], primary_eval.mandatory, header)

        limitations = [
            f"Joint allocation across {len(members)} incidents ({numbers}); this recommendation is the part that applies to "
            f"{primary.number}. Request each other incident's recommendation with scope multi_incident and the same incident set "
            "to get its part of the same plan. Higher priority wins a contested unit, then the earlier incident.",
            *plan.notes,
        ]
        for inc, _ in members:
            if inc.priority == IncidentPriority.unassigned:
                limitations.append(f"{inc.number} has no priority; unassigned incidents rank below P4 when units are contested.")
            elif not inc.priorityConfirmed:
                limitations.append(f"{inc.number}'s priority {inc.priority.value} has not been confirmed by a dispatcher.")
        owner = {
            e.view.unit.unitId: inc
            for inc, _ in members if inc.incidentId != primary.incidentId
            for e in part[inc.incidentId][0]
        }
        for e in primary_eval.eligible_units:
            elsewhere = owner.get(e.view.unit.unitId)
            if elsewhere is not None:
                limitations.append(
                    f"{e.view.unit.callSign} is eligible for this incident but is allocated to {elsewhere.number} "
                    f"({elsewhere.priority.value}) in the joint plan."
                )
        for inc, _ in members:
            if inc.incidentId != primary.incidentId and part[inc.incidentId][1]:
                limitations.append(f"{inc.number} cannot be fully staffed in this plan: {' '.join(part[inc.incidentId][1])}")
        if package is None:
            limitations.append(
                f"No unit is allocated to {primary.number} in the joint plan. Review the plan, or request a single-incident recommendation."
            )
        return package, unmet, plan.optimizer, limitations

    def _expire_if_due(self, rec: Recommendation) -> Recommendation:
        if rec.status == RecommendationStatus.pending_review and rec.expiresAt and rec.expiresAt <= self.clock():
            return self.store.save_recommendation(rec.model_copy(update={"status": RecommendationStatus.expired}))
        return rec

    def get_recommendation(self, recommendation_id: UUID) -> Recommendation:
        rec = self.store.get_recommendation(recommendation_id)
        if rec is None:
            raise ApiError(ErrorCode.NOT_FOUND, "Recommendation not found.")
        return self._expire_if_due(rec)

    def list_recommendations(self, incident_id: UUID) -> list[Recommendation]:
        self.get_incident(incident_id)
        return [self._expire_if_due(r) for r in self.store.list_recommendations(incident_id)]

    # -- demo ------------------------------------------------------------------------
    def reset_demo(self, user: User) -> dict[str, int]:
        with self.dispatch_lock:
            counts = self.store.reset()
            self.idempotency.clear()
            self.events.reset()  # connected clients are told to refetch
            self._audit(user, "demo.reset", "system", None, seedVersion=self.store.seed_version)
        return counts

    # -- human review: the only way a unit gets (simulated) assigned ---------------
    def _review_selection(self, body: ReviewCreate, candidates: dict, recommended: list[UUID]) -> list[UUID]:
        chosen = list(dict.fromkeys(body.selectedUnitIds or []))
        if body.decision == ReviewDecision.reject:
            if chosen:
                raise ApiError(ErrorCode.VALIDATION_ERROR, "A rejection selects no units.", [("selectedUnitIds", "Must be empty when rejecting.")])
            return []
        if body.decision == ReviewDecision.modify and not chosen:
            raise ApiError(ErrorCode.VALIDATION_ERROR, "A modification must name the units to assign.", [("selectedUnitIds", "Required when decision is modify.")])
        if body.decision == ReviewDecision.accept:
            if chosen and set(chosen) != set(recommended):
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR, "Accepting takes the recommended units; use decision 'modify' to choose different ones.",
                    [("selectedUnitIds", "Differs from the recommended package.")],
                )
            chosen = chosen or recommended
            if not chosen:
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR, "There is nothing to accept: no package of eligible units was recommended.",
                    [("decision", "Use 'modify' to choose units manually, or 'reject'.")],
                )
        for i, unit_id in enumerate(chosen):
            if unit_id not in candidates:
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR, "A selected unit is not part of this recommendation.",
                    [(f"selectedUnitIds[{i}]", "Not evaluated here; request a new recommendation with a higher maxCandidates.")],
                )
        return chosen

    def create_review(self, user: User, recommendation_id: UUID, body: ReviewCreate) -> RecommendationReview:
        with self.dispatch_lock:
            rec = self.get_recommendation(recommendation_id)
            if rec.status in (RecommendationStatus.invalidated, RecommendationStatus.expired):
                why = rec.invalidatedReason or "it passed its expiry time"
                raise ApiError(ErrorCode.RECOMMENDATION_OUTDATED, f"This recommendation is {rec.status.value} ({why}); request a new one.")
            if rec.status != RecommendationStatus.pending_review:
                raise ApiError(ErrorCode.CONFLICT, f"This recommendation was already reviewed ({rec.status.value}).")
            if body.recommendationVersionSeenAt is not None and body.recommendationVersionSeenAt < rec.createdAt:
                raise ApiError(ErrorCode.RECOMMENDATION_OUTDATED, "Your view predates this recommendation; refresh and review again.")
            incident = self.get_incident(rec.incidentId)
            if incident.status in _TERMINAL:
                raise ApiError(ErrorCode.INVALID_STATE_TRANSITION, f"Incident is {incident.status.value}.")

            candidates = {c.unitId: c for c in rec.candidates}
            recommended = list(rec.packages[0].unitIds) if rec.packages else []
            selected = self._review_selection(body, candidates, recommended)

            # The world may have changed since the recommendation was made: check availability now.
            unavailable = []
            for i, unit_id in enumerate(selected):
                unit = self.store.get_unit(unit_id)
                if unit is None or unit.status != UnitStatus.available:
                    unavailable.append((f"selectedUnitIds[{i}]", f"{candidates[unit_id].callSign}: status={unit.status.value if unit else 'unknown'}"))
            if unavailable:
                raise ApiError(ErrorCode.UNIT_UNAVAILABLE, "A selected unit is no longer available.", unavailable)

            # A human may override the recommendation (never the model); it needs a reason and is recorded as an override.
            ineligible = [candidates[u] for u in selected if not candidates[u].eligible]
            is_override = bool(ineligible) or (bool(selected) and set(selected) != set(recommended))
            if is_override and not (body.overrideReason or "").strip():
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR, "An override reason is required when choosing ineligible or non-recommended units.",
                    [("overrideReason", "Required for this selection.")],
                )

            simulated = body.decision != ReviewDecision.reject
            call_signs = [candidates[u].callSign for u in selected]
            if simulated:
                for unit_id in selected:
                    unit = self.store.get_unit(unit_id)
                    version = unit.version or 1
                    try:
                        saved = self.store.save_unit(
                            unit.model_copy(update={"status": UnitStatus.dispatched_simulated, "version": version + 1}), version
                        )
                    except VersionConflict:
                        raise ApiError(ErrorCode.UNIT_UNAVAILABLE, "A selected unit changed while you were reviewing; refresh and try again.")
                    self._publish(
                        "unit.status_changed", "unit", unit_id, saved.version, callSign=saved.callSign,
                        previousStatus="available", status="dispatched_simulated", reason=f"Simulated assignment to {incident.number}",
                    )
                self._save(incident, incident.version, status=IS.dispatched_simulated)
                new_status = RecommendationStatus.accepted if body.decision == ReviewDecision.accept else RecommendationStatus.modified
            else:
                new_status = RecommendationStatus.rejected
                if incident.status == IS.recommendation_ready:
                    self._save(incident, incident.version, status=IS.pending_dispatch)
            self.store.save_recommendation(rec.model_copy(update={"status": new_status}))
            if simulated:
                self._invalidate_for_units(
                    set(selected), f"{', '.join(call_signs)} assigned to {incident.number}.", except_id=rec.recommendationId
                )

            review = self.store.add_review(RecommendationReview(
                reviewId=uuid4(), recommendationId=rec.recommendationId, reviewerId=user.userId, decision=body.decision,
                selectedUnitIds=selected or None, explanation=body.explanation, overrideReason=body.overrideReason,
                isOverride=is_override, simulatedAssignment=simulated, createdAt=self.clock(),
            ))
            self._audit(
                user, f"recommendation.review.{body.decision.value}", "recommendation", rec.recommendationId,
                incidentId=str(rec.incidentId), selectedUnits=call_signs, isOverride=is_override,
                overrideReason=body.overrideReason, simulatedAssignment=simulated,
                ineligibleSelected=[{"callSign": c.callSign, "reasons": c.ineligibleReasons} for c in ineligible],
            )
            return review

    # -- unit status (simulated) ---------------------------------------------------
    def record_unit_status(self, user: User, unit_id: UUID, body: UnitStatusEventCreate) -> UnitStatusEvent:
        with self.dispatch_lock:
            unit = self.store.get_unit(unit_id)
            if unit is None:
                raise ApiError(ErrorCode.NOT_FOUND, "Unit not found.")
            version = unit.version or 1
            if body.expectedUnitVersion is not None and body.expectedUnitVersion != version:
                raise ApiError(ErrorCode.CONFLICT, f"Unit {unit.callSign} is at version {version}, not {body.expectedUnitVersion}.")
            if body.status == unit.status:
                raise ApiError(ErrorCode.INVALID_STATE_TRANSITION, f"Unit {unit.callSign} is already {unit.status.value}.")

            previous = unit.status
            updates: dict = {"status": body.status, "version": version + 1}
            if body.location is not None:
                updates["location"] = body.location
            try:
                saved = self.store.save_unit(unit.model_copy(update=updates), version)
            except VersionConflict:
                raise ApiError(ErrorCode.CONFLICT, f"Unit {unit.callSign} was modified by someone else; reload and retry.")

            # Publish the cause before its consequences, so consumers see the status change first.
            self._publish(
                "unit.status_changed", "unit", unit_id, saved.version, callSign=saved.callSign,
                previousStatus=previous.value, status=body.status.value, reason=body.reason,
            )
            invalidated: list[Recommendation] = []
            if previous == UnitStatus.available and body.status != UnitStatus.available:
                reason = f"{unit.callSign} became {body.status.value}" + (f": {body.reason}" if body.reason else ".")
                invalidated = self._invalidate_for_units({unit_id}, reason)

            event = self.store.add_status_event(UnitStatusEvent(
                statusEventId=uuid4(), unitId=unit_id, status=body.status, previousStatus=PreviousStatus(previous.value),
                reason=body.reason, recordedBy=user.userId, effectiveAt=body.effectiveAt, recordedAt=self.clock(),
                invalidatedRecommendationIds=[r.recommendationId for r in invalidated] or None,
            ))
            self._audit(
                user, "unit.status.record", "unit", unit_id, callSign=saved.callSign, previousStatus=previous.value,
                status=body.status.value, reason=body.reason, invalidatedRecommendations=len(invalidated),
            )
            return event
