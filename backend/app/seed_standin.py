"""TEMPORARY stand-in seed so the backend runs and is testable before the DB/seed track lands.

Replace by a `Store` backed by the real seed. Whatever replaces it must keep the
capability IDs and credential-definition IDs used by `protocol.py` (listed in
CAPABILITIES below), or the protocol table must change with it.

Everything here is fictional and synthetic: fictional county, station names,
call signs and crew labels (no real people).
"""

from datetime import datetime, timezone

from .config import stable_id
from .models import (
    Apparatus,
    ApparatusReadiness,
    CapabilityDefinition,
    CapabilityKind,
    Credential,
    CredentialState,
    DataOrigin,
    EmploymentStatus,
    EquipmentItem,
    EquipmentReadiness,
    Facility,
    FacilityType,
    GeoPoint,
    Personnel,
    Provenance,
    UnitDiscipline,
    UnitStatus,
    Unit,
    VerificationStatus,
)
from .store import _Seed

SEED_VERSION = "standin-0.1.0"
SEED = 20260417
_STAMP = datetime(2026, 10, 1, tzinfo=timezone.utc)
SOURCE = "dispatchz-backend-standin-seed"


def _prov(verified: bool = False) -> Provenance:
    return Provenance(
        dataOrigin=DataOrigin.synthetic,
        sourceSystem=SOURCE,
        sourceTimestamp=_STAMP,
        verificationStatus=VerificationStatus.verified if verified else VerificationStatus.unverified,
    )


K = CapabilityKind
# (id, label, kind, standard refs, required credential definitions)
CAPABILITIES = [
    ("CAP-FIRE-SUPPRESSION", "Fire suppression (pumper)", K.apparatus, ["FEMA NIMS Resource Typing Library: Engine"], None),
    ("CAP-AERIAL-LADDER", "Aerial ladder device", K.apparatus, ["FEMA NIMS Resource Typing Library: Aerial apparatus"], None),
    ("CAP-AMBULANCE-TRANSPORT", "Ambulance transport", K.apparatus, ["FEMA NIMS Resource Typing Library: Ambulance"], None),
    ("CAP-PATROL-VEHICLE", "Patrol vehicle", K.apparatus, [], None),
    ("CAP-CARDIAC-MONITOR", "Cardiac monitor/defibrillator", K.equipment, [], None),
    ("CAP-RESCUE-TOOLS", "Hydraulic rescue tools", K.equipment, [], None),
    ("CAP-HAZMAT-DETECTION", "Hazmat detection/monitoring kit", K.equipment, [], None),
    ("CAP-ALS-PARAMEDIC", "ALS-qualified paramedic on crew", K.personnel_qualification,
     ["National EMS Scope of Practice Model"], ["CRED-NREMT-PARAMEDIC"]),
    ("CAP-BLS-EMT", "BLS-qualified EMT (or higher) on crew", K.personnel_qualification,
     ["National EMS Scope of Practice Model"], ["CRED-NREMT-EMT", "CRED-NREMT-PARAMEDIC"]),
    ("CAP-HAZMAT-TECH", "Hazardous materials technician on crew", K.personnel_qualification,
     ["NFPA 1072"], ["CRED-NFPA1072-HAZMAT-TECH"]),
    ("CAP-TECH-RESCUE", "Technical rescue technician on crew", K.personnel_qualification,
     ["NFPA 1006"], ["CRED-NFPA1006-RESCUE-TECH"]),
]

AGENCY_FIRE_EMS = stable_id("agency:fire-ems")
AGENCY_SHERIFF = stable_id("agency:sheriff")
# agency id -> (name, code); the Postgres seed needs a row for every agency the resources reference.
AGENCIES = {
    AGENCY_FIRE_EMS: ("County Fire and EMS (synthetic)", "FIRE-EMS"),
    AGENCY_SHERIFF: ("County Sheriff (synthetic)", "SHERIFF"),
}

# station key -> (name, type, agency, lat, lon)
STATIONS = {
    "S1": ("Station 1 Central", FacilityType.fire_station, AGENCY_FIRE_EMS, 39.100, -95.700),
    "S2": ("Station 2 North", FacilityType.fire_station, AGENCY_FIRE_EMS, 39.150, -95.690),
    "S3": ("Station 3 East", FacilityType.fire_station, AGENCY_FIRE_EMS, 39.095, -95.630),
    "S4": ("Station 4 West", FacilityType.fire_station, AGENCY_FIRE_EMS, 39.080, -95.760),
    "SO": ("Sheriff Headquarters", FacilityType.police_station, AGENCY_SHERIFF, 39.105, -95.705),
    "H1": ("County General Hospital", FacilityType.hospital, AGENCY_FIRE_EMS, 39.102, -95.695),
}

# call sign -> (station, discipline, unit type, status, apparatus readiness, crew roles,
#               equipment [(name, [capability ids], readiness)])
# crew role keys: FF firefighter, PM paramedic, EMT, HZ hazmat tech, TR tech rescue, DEP deputy
R = EquipmentReadiness
UNITS = {
    "E1": ("S1", UnitDiscipline.fire, "Engine Type 1", UnitStatus.available, ["FF", "FF", "FF"],
           [("Pump 1500 gpm", ["CAP-FIRE-SUPPRESSION"], R.ready)]),
    "E2": ("S2", UnitDiscipline.fire, "Engine Type 1", UnitStatus.available, ["PM", "FF", "FF"],
           [("Pump 1500 gpm", ["CAP-FIRE-SUPPRESSION"], R.ready), ("Cardiac monitor", ["CAP-CARDIAC-MONITOR"], R.ready)]),
    "E3": ("S3", UnitDiscipline.fire, "Engine Type 1", UnitStatus.available, ["FF", "FF", "FF"],
           [("Pump 1500 gpm", ["CAP-FIRE-SUPPRESSION"], R.ready)]),
    "E4": ("S4", UnitDiscipline.fire, "Engine Type 1", UnitStatus.available, ["FF", "FF", "TR_EXPIRED"],
           [("Pump 1500 gpm", ["CAP-FIRE-SUPPRESSION"], R.ready), ("Hydraulic rescue tools", ["CAP-RESCUE-TOOLS"], R.ready)]),
    "L1": ("S1", UnitDiscipline.fire, "Ladder / Aerial", UnitStatus.available, ["FF", "FF", "FF"],
           [("Aerial device 75 ft", ["CAP-AERIAL-LADDER"], R.ready)]),
    "HR4": ("S4", UnitDiscipline.fire, "Hazmat / Technical Rescue", UnitStatus.available, ["HZ", "TR", "FF"],
           [("Detection and monitoring kit", ["CAP-HAZMAT-DETECTION"], R.ready),
            ("Hydraulic rescue tools", ["CAP-RESCUE-TOOLS"], R.ready)]),
    "M1": ("S1", UnitDiscipline.ems, "ALS Ambulance", UnitStatus.available, ["PM", "EMT"],
           [("Ambulance transport package", ["CAP-AMBULANCE-TRANSPORT"], R.ready),
            ("Cardiac monitor", ["CAP-CARDIAC-MONITOR"], R.ready)]),
    "M2": ("S2", UnitDiscipline.ems, "ALS Ambulance", UnitStatus.dispatched_simulated, ["PM", "EMT"],
           [("Ambulance transport package", ["CAP-AMBULANCE-TRANSPORT"], R.ready),
            ("Cardiac monitor", ["CAP-CARDIAC-MONITOR"], R.ready)]),
    "M3": ("S3", UnitDiscipline.ems, "BLS Ambulance", UnitStatus.available, ["EMT", "EMT"],
           [("Ambulance transport package", ["CAP-AMBULANCE-TRANSPORT"], R.ready)]),
    "M4": ("S4", UnitDiscipline.ems, "BLS Ambulance", UnitStatus.available, ["EMT_EXPIRED", "EMT_UNVERIFIED"],
           [("Ambulance transport package", ["CAP-AMBULANCE-TRANSPORT"], R.ready)]),
    "M5": ("S3", UnitDiscipline.ems, "ALS Ambulance", UnitStatus.available, ["PM", "EMT"],
           [("Ambulance transport package", ["CAP-AMBULANCE-TRANSPORT"], R.ready),
            ("Cardiac monitor", ["CAP-CARDIAC-MONITOR"], R.maintenance)]),
    "P1": ("SO", UnitDiscipline.police, "Patrol", UnitStatus.available, ["DEP"],
           [("Patrol vehicle", ["CAP-PATROL-VEHICLE"], R.ready)]),
    "P2": ("SO", UnitDiscipline.police, "Patrol", UnitStatus.available, ["DEP"],
           [("Patrol vehicle", ["CAP-PATROL-VEHICLE"], R.ready)]),
}

# crew role -> (display role, [credential definition ids granted])
ROLES = {
    "FF": ("Firefighter", []),
    "PM": ("Firefighter/Paramedic", ["CRED-NREMT-PARAMEDIC"]),
    "EMT": ("EMT", ["CRED-NREMT-EMT"]),
    "HZ": ("Hazmat Technician", ["CRED-NFPA1072-HAZMAT-TECH"]),
    "TR": ("Technical Rescue Technician", ["CRED-NFPA1006-RESCUE-TECH"]),
    "DEP": ("Deputy", []),
    "TR_EXPIRED": ("Technical Rescue Technician", ["CRED-NFPA1006-RESCUE-TECH"]),
    "EMT_EXPIRED": ("EMT", ["CRED-NREMT-EMT"]),
    "EMT_UNVERIFIED": ("EMT", ["CRED-NREMT-EMT"]),
}
_VALID_UNTIL = datetime(2028, 6, 30, tzinfo=timezone.utc)
_EXPIRED_ON = datetime(2026, 6, 30, tzinfo=timezone.utc)
_ISSUED = datetime(2024, 6, 30, tzinfo=timezone.utc)

_MIN_CREW = {"fire": 3, "ems": 2, "police": 1}


def build_seed() -> _Seed:
    facilities = [
        Facility(
            facilityId=stable_id(f"facility:{key}"), agencyId=agency, name=name, type=ftype,
            location=GeoPoint(lat=lat, lon=lon), provenance=_prov(),
        )
        for key, (name, ftype, agency, lat, lon) in STATIONS.items()
    ]
    capability_definitions = [
        CapabilityDefinition(
            capabilityId=cid, label=label, kind=kind, standardRefs=refs or None,
            requiresCredentialDefinitionIds=creds,
        )
        for cid, label, kind, refs, creds in CAPABILITIES
    ]

    units, personnel, credentials, apparatus = [], [], [], []
    for index, (sign, (station, disc, utype, status, crew, equipment)) in enumerate(UNITS.items()):
        _, _, agency, lat, lon = STATIONS[station]
        crew_ids = []
        for n, role_key in enumerate(crew, start=1):
            pid = stable_id(f"personnel:{sign}:{n}")
            role, grants = ROLES[role_key]
            crew_ids.append(pid)
            personnel.append(Personnel(
                personnelId=pid, agencyId=agency, displayName=f"{sign} Crew Member {n} (synthetic)", role=role,
                stationId=stable_id(f"facility:{station}"), employmentStatus=EmploymentStatus.active, provenance=_prov(),
            ))
            for cred_def in grants:
                state, expires, verified = CredentialState.valid, _VALID_UNTIL, True
                if role_key.endswith("_EXPIRED"):
                    state, expires = CredentialState.expired, _EXPIRED_ON
                if role_key.endswith("_UNVERIFIED"):
                    state, verified = CredentialState.unverified, False
                credentials.append(Credential(
                    credentialId=stable_id(f"credential:{sign}:{n}:{cred_def}"), personnelId=pid,
                    credentialDefinitionId=cred_def, state=state, issuedAt=_ISSUED, expiresAt=expires,
                    verifiedAt=_STAMP if verified else None, provenance=_prov(verified),
                ))

        appid = stable_id(f"apparatus:{sign}")
        has_down_equipment = any(r is not EquipmentReadiness.ready for _, _, r in equipment)
        apparatus.append(Apparatus(
            apparatusId=appid, agencyId=agency, identifier=f"{sign} apparatus", type=utype,
            readiness=ApparatusReadiness.limited if has_down_equipment else ApparatusReadiness.ready,
            equipment=[
                EquipmentItem(equipmentId=stable_id(f"equipment:{sign}:{i}"), name=name, capabilityIds=caps, readiness=r)
                for i, (name, caps, r) in enumerate(equipment)
            ],
            provenance=_prov(),
        ))
        units.append(Unit(
            unitId=stable_id(f"unit:{sign}"), agencyId=agency, callSign=sign, discipline=disc, unitType=utype,
            status=status, stationId=stable_id(f"facility:{station}"), apparatusId=appid,
            location=GeoPoint(lat=lat + 0.002 * (index % 3), lon=lon + 0.002 * (index % 2)),
            crewPersonnelIds=crew_ids, minimumCrew=_MIN_CREW[disc.value], version=1, provenance=_prov(),
        ))

    return _Seed(
        version=SEED_VERSION, seed=SEED, units=units, personnel=personnel, credentials=credentials,
        apparatus=apparatus, facilities=facilities, capability_definitions=capability_definitions,
    )
