"""Demo dispatch protocol: incident category -> required capabilities.

This is a SYNTHETIC protocol for the hackathon, not an adopted agency protocol
and not a statement of any standard's minimums. `standardRefs` on the capability
definitions name the standards a real agency would map to; nothing here assigns
numeric ratings to them. A real deployment replaces this table with versioned,
approved policy data (the contract's Policy entity, tier "future").

`quantity` is the number of distinct *units* that must provide the capability.

Every incident that transports a patient also requires a BLS-or-higher crew: a unit whose crew
holds no valid, verified EMS credential must not be admitted merely because it owns an ambulance.
"""

from dataclasses import dataclass

from .config import stable_id
from .errors import ApiError
from .models import (
    CapabilityDefinition,
    CapabilityRequirement,
    ErrorCode,
    IncidentCategory as C,
    RequirementOrigin,
)

POLICY_NAME = "Demo dispatch protocol"
POLICY_VERSION = "0.1.0-demo"
POLICY_ID = stable_id(f"policy:dispatch_protocol:{POLICY_VERSION}")


@dataclass(frozen=True)
class Rule:
    capability_id: str
    quantity: int = 1
    mandatory: bool = True


_FIRE = "CAP-FIRE-SUPPRESSION"
_ALS, _BLS, _XPORT = "CAP-ALS-PARAMEDIC", "CAP-BLS-EMT", "CAP-AMBULANCE-TRANSPORT"

PROTOCOL: dict[C, list[Rule]] = {
    C.structure_fire: [Rule(_FIRE, 2), Rule("CAP-AERIAL-LADDER"), Rule(_ALS, mandatory=False)],
    C.vehicle_fire: [Rule(_FIRE)],
    C.wildland_fire: [Rule(_FIRE, 2)],
    C.medical_cardiac: [Rule(_ALS), Rule(_BLS), Rule("CAP-CARDIAC-MONITOR"), Rule(_XPORT)],
    C.medical_trauma: [Rule(_ALS), Rule(_BLS), Rule(_XPORT)],
    C.medical_other: [Rule(_BLS), Rule(_XPORT)],
    C.traffic_collision: [Rule(_BLS), Rule(_XPORT), Rule("CAP-RESCUE-TOOLS", mandatory=False)],
    C.technical_rescue: [Rule("CAP-TECH-RESCUE"), Rule("CAP-RESCUE-TOOLS"), Rule(_ALS, mandatory=False)],
    C.hazmat: [Rule("CAP-HAZMAT-TECH"), Rule("CAP-HAZMAT-DETECTION"), Rule(_FIRE, mandatory=False)],
    C.assault: [Rule("CAP-PATROL-VEHICLE", 2)],
    C.burglary: [Rule("CAP-PATROL-VEHICLE")],
    C.domestic_disturbance: [Rule("CAP-PATROL-VEHICLE", 2)],
    C.welfare_check: [Rule("CAP-PATROL-VEHICLE")],
    C.other: [],  # nothing is inferred for an unclassified incident
}


def requirements_for(category: C, definitions: list[CapabilityDefinition]) -> list[CapabilityRequirement]:
    by_id = {d.capabilityId: d for d in definitions}
    out = []
    for rule in PROTOCOL[category]:
        definition = by_id.get(rule.capability_id)
        if definition is None:
            # Protocol and capability registry disagree: fail loudly rather than drop a requirement.
            raise ApiError(
                ErrorCode.INTERNAL_ERROR,
                f"Protocol references capability {rule.capability_id} missing from the capability registry.",
            )
        out.append(CapabilityRequirement(
            capabilityId=definition.capabilityId,
            label=definition.label,
            kind=definition.kind,
            quantity=rule.quantity,
            mandatory=rule.mandatory,
            origin=RequirementOrigin.protocol_rule,
            standardRef=(definition.standardRefs or [None])[0],
        ))
    return out
