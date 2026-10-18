"""Readable names for the types in models_generated.py.

datamodel-codegen numbers colliding enum names (Status, Status2, ...), and the
numbering shifts whenever the contract changes. Import generated types only
through this module so a regeneration needs fixes in one place.
"""

from .models_generated import (
    Apparatus,
    AuditEvent,
    AuthMode,
    Candidate,
    Capability,
    CapabilityCheck,
    CapabilityDefinition,
    CapabilityRequirement,
    Classification,
    ClassificationCreate,
    Code as ErrorCode,
    Component,
    Credential,
    DataOrigin,
    DemoResetResult,
    Error,
    EquipmentItem,
    EtaMethod,
    Facility,
    GeoPoint,
    Health,
    Incident,
    IncidentCategory,
    IncidentCreate,
    IncidentUpdate,
    Inputs,
    Location,
    Mode as ClassificationMode,
    Package,
    Personnel,
    Provenance,
    RankingFactor,
    Recommendation,
    RecommendationCreate,
    RecommendationReview,
    RequirementSet,
    ReviewCreate,
    Role,
    Unit,
    UnitCapabilities,
    UnitStatusEvent,
    UnitStatusEventCreate,
    User,
    VerificationStatus,
)
from .models_generated import Decision as ReviewDecision
from .models_generated import Discipline as UnitDiscipline
from .models_generated import Outcome as AuditOutcome
from .models_generated import PreviousStatus
from .models_generated import Type3 as EventType
from .models_generated import Kind as CapabilityKind
from .models_generated import Origin as RequirementOrigin
from .models_generated import Priority as IncidentPriority
from .models_generated import Priority1 as ConfirmedPriority
from .models_generated import Readiness as ApparatusReadiness
from .models_generated import Readiness1 as EquipmentReadiness
from .models_generated import Scope as RecommendationScope
from .models_generated import Source as ClassificationSource
from .models_generated import State as CapabilityState
from .models_generated import State2 as CredentialState
from .models_generated import Status as HealthStatus
from .models_generated import Status2 as IncidentStatus
from .models_generated import Status4 as ClassificationStatus
from .models_generated import Status5 as RecommendationStatus
from .models_generated import Status6 as UnitStatus
from .models_generated import Stage as RecommendationStage
from .models_generated import SuggestedPriority as SuggestedPriority
from .models_generated import Type1 as FacilityType
from .models_generated import EmploymentStatus

__all__ = [name for name in dir() if not name.startswith("_")]
