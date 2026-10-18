"""Reference data (agencies' facilities, people, units, apparatus, capability definitions) in PostgreSQL.

`write_seed` stores a `_Seed` in the tables from migrations 0001/0002 and `read_resources`
rebuilds the contract models from them, so the stand-in seed round-trips exactly. Values
the 0001 schema used before the contract's enumerations existed are mapped on read.
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from .config import stable_id
from .models import (
    Apparatus,
    CapabilityDefinition,
    Credential,
    EquipmentItem,
    Facility,
    GeoPoint,
    Personnel,
    Provenance,
    Unit,
)
from .seed_standin import AGENCIES
from .store import _Seed

# 0001 values -> the contract's.
LEGACY_UNIT_STATUS = {
    "assigned": "dispatched_simulated",
    "en_route": "en_route_simulated",
    "on_scene": "on_scene_simulated",
    "offline": "off_duty",
}
LEGACY_CREDENTIAL_STATE = {"active": "valid", "suspended": "revoked"}

# Children before parents is not required: TRUNCATE ... CASCADE follows the foreign keys.
_RESET_TABLES = "agency, capability, audit"


@dataclass(frozen=True)
class Resources:
    units: list[Unit]
    personnel: list[Personnel]
    credentials: list[Credential]
    apparatus: list[Apparatus]
    facilities: list[Facility]
    capability_definitions: list[CapabilityDefinition]


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _prov(p: Provenance) -> str:
    return _json(p.model_dump(mode="json", exclude_none=True))


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.upper()).strip("-")[:40]


def write_seed(conn: Connection, seed: _Seed) -> dict[str, int]:
    """Replace every table's contents with `seed`. The caller owns the transaction."""
    conn.execute(text(f"TRUNCATE {_RESET_TABLES} RESTART IDENTITY CASCADE"))

    for agency_id, (name, code) in AGENCIES.items():
        conn.execute(
            text("INSERT INTO agency (id, name, code) VALUES (:id, :name, :code)"),
            {"id": agency_id, "name": name, "code": code},
        )

    for f in seed.facilities:
        conn.execute(
            text(
                "INSERT INTO facility (id, agency_id, name, code, latitude, longitude, facility_type, service_area_id, provenance)"
                " VALUES (:id, :agency, :name, :code, :lat, :lon, :type, :area, CAST(:prov AS jsonb))"
            ),
            {
                "id": f.facilityId, "agency": f.agencyId, "name": f.name, "code": _slug(f.name), "lat": f.location.lat,
                "lon": f.location.lon, "type": f.type.value, "area": f.serviceAreaId, "prov": _prov(f.provenance),
            },
        )

    for d in seed.capability_definitions:
        conn.execute(
            text(
                "INSERT INTO capability (id, code, name, description, category, kind, standard_refs, required_credential_definition_ids)"
                " VALUES (:id, :code, :name, :description, :kind, :kind, CAST(:refs AS jsonb), CAST(:creds AS jsonb))"
            ),
            {
                "id": stable_id(f"capability:{d.capabilityId}"), "code": d.capabilityId, "name": d.label,
                "description": d.description, "kind": d.kind.value, "refs": _json(d.standardRefs or []),
                "creds": _json(d.requiresCredentialDefinitionIds or []),
            },
        )

    for a in seed.apparatus:
        conn.execute(
            text(
                "INSERT INTO apparatus (id, agency_id, identifier, apparatus_type, nims_type_ref, readiness, provenance)"
                " VALUES (:id, :agency, :identifier, :type, :nims, :readiness, CAST(:prov AS jsonb))"
            ),
            {
                "id": a.apparatusId, "agency": a.agencyId, "identifier": a.identifier, "type": a.type,
                "nims": a.nimsTypeRef, "readiness": a.readiness.value, "prov": _prov(a.provenance),
            },
        )
        for position, item in enumerate(a.equipment or []):
            conn.execute(
                text(
                    "INSERT INTO equipment (id, apparatus_id, position, name, capability_ids, readiness, last_inspected_at)"
                    " VALUES (:id, :apparatus, :position, :name, CAST(:caps AS jsonb), :readiness, :inspected)"
                ),
                {
                    "id": item.equipmentId, "apparatus": a.apparatusId, "position": position, "name": item.name,
                    "caps": _json(item.capabilityIds or []), "readiness": item.readiness.value,
                    "inspected": item.lastInspectedAt,
                },
            )

    for p in seed.personnel:
        conn.execute(
            text(
                "INSERT INTO personnel (id, agency_id, facility_id, display_name, role, employment_status, provenance)"
                " VALUES (:id, :agency, :station, :name, :role, :employment, CAST(:prov AS jsonb))"
            ),
            {
                "id": p.personnelId, "agency": p.agencyId, "station": p.stationId, "name": p.displayName,
                "role": p.role, "employment": p.employmentStatus.value, "prov": _prov(p.provenance),
            },
        )

    for c in seed.credentials:
        conn.execute(
            text(
                "INSERT INTO credential (id, personnel_id, code, name, issued_at, expires_at, status, verified_at, verified_by, version, provenance)"
                " VALUES (:id, :personnel, :code, :name, :issued, :expires, :state, :verified_at, :verified_by, :version, CAST(:prov AS jsonb))"
            ),
            {
                "id": c.credentialId, "personnel": c.personnelId, "code": c.credentialDefinitionId,
                "name": c.label or c.credentialDefinitionId, "issued": c.issuedAt, "expires": c.expiresAt,
                "state": c.state.value, "verified_at": c.verifiedAt, "verified_by": c.verifiedBy,
                "version": c.version, "prov": _prov(c.provenance),
            },
        )

    for u in seed.units:
        conn.execute(
            text(
                "INSERT INTO unit (id, agency_id, facility_id, unit_number, unit_type, status, latitude, longitude, version,"
                " discipline, minimum_crew, apparatus_id, provenance)"
                " VALUES (:id, :agency, :station, :sign, :type, :status, :lat, :lon, :version, :discipline, :min_crew, :apparatus,"
                " CAST(:prov AS jsonb))"
            ),
            {
                "id": u.unitId, "agency": u.agencyId, "station": u.stationId, "sign": u.callSign,
                "type": u.unitType or u.discipline.value, "status": u.status.value,
                "lat": u.location.lat if u.location else None, "lon": u.location.lon if u.location else None,
                "version": u.version or 1, "discipline": u.discipline.value, "min_crew": u.minimumCrew,
                "apparatus": u.apparatusId, "prov": _prov(u.provenance),
            },
        )
        for position, pid in enumerate(u.crewPersonnelIds or []):
            conn.execute(
                text("INSERT INTO unit_crew (unit_id, personnel_id, position) VALUES (:unit, :personnel, :position)"),
                {"unit": u.unitId, "personnel": pid, "position": position},
            )

    return {
        "units": len(seed.units), "personnel": len(seed.personnel), "credentials": len(seed.credentials),
        "apparatus": len(seed.apparatus), "facilities": len(seed.facilities),
        "capabilityDefinitions": len(seed.capability_definitions),
    }


def _rows(conn: Connection, sql: str, **params: Any):
    return conn.execute(text(sql), params).mappings().all()


def read_facilities(conn: Connection) -> list[Facility]:
    return [
        Facility(
            facilityId=r["id"], agencyId=r["agency_id"], name=r["name"], type=r["facility_type"],
            location=GeoPoint(lat=r["latitude"], lon=r["longitude"]), serviceAreaId=r["service_area_id"],
            provenance=Provenance.model_validate(r["provenance"]),
        )
        for r in _rows(conn, "SELECT * FROM facility ORDER BY name")
    ]


def read_capability_definitions(conn: Connection) -> list[CapabilityDefinition]:
    return [
        CapabilityDefinition(
            capabilityId=r["code"], label=r["name"], description=r["description"], kind=r["kind"],
            standardRefs=r["standard_refs"] or None,
            requiresCredentialDefinitionIds=r["required_credential_definition_ids"] or None,
        )
        for r in _rows(conn, "SELECT * FROM capability ORDER BY code")
    ]


def read_units(conn: Connection, unit_id: UUID | None = None) -> list[Unit]:
    where = "AND id = :id" if unit_id else ""
    crews: dict[Any, list[Any]] = {}
    for r in _rows(
        conn, "SELECT unit_id, personnel_id FROM unit_crew" + (" WHERE unit_id = :id" if unit_id else "") + " ORDER BY unit_id, position",
        id=unit_id,
    ):
        crews.setdefault(r["unit_id"], []).append(r["personnel_id"])
    return [
        Unit(
            unitId=r["id"], agencyId=r["agency_id"], callSign=r["unit_number"], discipline=r["discipline"],
            unitType=r["unit_type"], status=LEGACY_UNIT_STATUS.get(r["status"], r["status"]),
            stationId=r["facility_id"], apparatusId=r["apparatus_id"],
            location=GeoPoint(lat=r["latitude"], lon=r["longitude"]) if r["latitude"] is not None else None,
            crewPersonnelIds=crews.get(r["id"], []), minimumCrew=r["minimum_crew"], version=r["version"],
            provenance=Provenance.model_validate(r["provenance"]),
        )
        for r in _rows(conn, f"SELECT * FROM unit WHERE is_active {where} ORDER BY unit_number", id=unit_id)
    ]


def save_unit_row(conn: Connection, unit: Unit, expected_version: int) -> bool:
    """Update the fields that change at run time iff the stored version (NULL counts as 1) matches. False if not."""
    result = conn.execute(
        text(
            "UPDATE unit SET status = :status, latitude = :lat, longitude = :lon, version = :version,"
            " minimum_crew = :min_crew, last_status_at = now(), provenance = CAST(:prov AS jsonb)"
            " WHERE id = :id AND COALESCE(version, 1) = :expected"
        ),
        {
            "id": unit.unitId, "status": unit.status.value, "lat": unit.location.lat if unit.location else None,
            "lon": unit.location.lon if unit.location else None, "version": unit.version or 1,
            "min_crew": unit.minimumCrew, "prov": _prov(unit.provenance), "expected": expected_version,
        },
    )
    return result.rowcount == 1


def read_resources(conn: Connection) -> Resources:
    equipment: dict[Any, list[EquipmentItem]] = {}
    for r in _rows(conn, "SELECT * FROM equipment ORDER BY apparatus_id, position"):
        equipment.setdefault(r["apparatus_id"], []).append(
            EquipmentItem(
                equipmentId=r["id"], name=r["name"], capabilityIds=r["capability_ids"], readiness=r["readiness"],
                lastInspectedAt=r["last_inspected_at"],
            )
        )
    apparatus = [
        Apparatus(
            apparatusId=r["id"], agencyId=r["agency_id"], identifier=r["identifier"], type=r["apparatus_type"],
            nimsTypeRef=r["nims_type_ref"], readiness=r["readiness"], equipment=equipment.get(r["id"], []),
            provenance=Provenance.model_validate(r["provenance"]),
        )
        for r in _rows(conn, "SELECT * FROM apparatus ORDER BY identifier")
    ]

    personnel = [
        Personnel(
            personnelId=r["id"], agencyId=r["agency_id"], displayName=r["display_name"], role=r["role"],
            stationId=r["facility_id"], employmentStatus=r["employment_status"],
            provenance=Provenance.model_validate(r["provenance"]),
        )
        for r in _rows(conn, "SELECT * FROM personnel ORDER BY display_name")
    ]

    credentials = [
        Credential(
            credentialId=r["id"], personnelId=r["personnel_id"], credentialDefinitionId=r["code"],
            label=r["name"] if r["name"] != r["code"] else None,
            state=LEGACY_CREDENTIAL_STATE.get(r["status"], r["status"]), issuedAt=r["issued_at"],
            expiresAt=r["expires_at"], verifiedBy=r["verified_by"], verifiedAt=r["verified_at"], version=r["version"],
            provenance=Provenance.model_validate(r["provenance"]),
        )
        for r in _rows(conn, "SELECT * FROM credential ORDER BY personnel_id, code")
    ]

    return Resources(
        read_units(conn), personnel, credentials, apparatus, read_facilities(conn), read_capability_definitions(conn)
    )
