"""Columns and tables the API's resource model needs on top of 0001.

Additive: every row that satisfies 0001 stays valid. Adds the crew, apparatus and
equipment structure the eligibility engine reads, the contract's discipline,
facility type and capability kind, and a provenance document on each resource.
Widens the unit and credential status checks to also accept the contract's
enumerations; the 0001 values stay valid and are mapped when the API reads them.

Revision ID: 0002_api_resource_model
Revises: 0001_initial_schema
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_api_resource_model"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

EMPTY_OBJECT = sa.text("'{}'::jsonb")
EMPTY_ARRAY = sa.text("'[]'::jsonb")

UNIT_STATUSES_0001 = "('available', 'assigned', 'en_route', 'on_scene', 'out_of_service', 'offline')"
UNIT_STATUSES = (
    "('available', 'assigned', 'en_route', 'on_scene', 'out_of_service', 'offline', "
    "'dispatched_simulated', 'en_route_simulated', 'on_scene_simulated', 'returning', 'off_duty')"
)
CREDENTIAL_STATUSES_0001 = "('active', 'expired', 'suspended', 'revoked')"
CREDENTIAL_STATUSES = "('active', 'expired', 'suspended', 'revoked', 'valid', 'unverified')"


def _provenance() -> sa.Column:
    return sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default=EMPTY_OBJECT)


def upgrade() -> None:
    # Capability definitions are keyed by the protocol's CAP-* identifiers, held in capability.code.
    op.add_column("capability", sa.Column("kind", sa.String(40), nullable=False, server_default="apparatus"))
    op.add_column("capability", sa.Column("standard_refs", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY))
    op.add_column(
        "capability",
        sa.Column("required_credential_definition_ids", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
    )
    op.create_check_constraint(
        "ck_capability_kind", "capability",
        "kind IN ('personnel_qualification', 'apparatus', 'equipment', 'crew_size', 'authority')",
    )

    op.add_column("facility", sa.Column("facility_type", sa.String(40), nullable=False, server_default="fire_station"))
    op.add_column("facility", sa.Column("service_area_id", postgresql.UUID(as_uuid=True)))
    op.add_column("facility", _provenance())
    op.create_check_constraint(
        "ck_facility_type", "facility",
        "facility_type IN ('fire_station', 'police_station', 'ems_station', 'hospital', 'eoc')",
    )

    op.add_column("personnel", sa.Column("employment_status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("personnel", _provenance())
    op.create_check_constraint(
        "ck_personnel_employment_status", "personnel", "employment_status IN ('active', 'leave', 'inactive')"
    )

    op.drop_constraint("ck_credential_status", "credential", type_="check")
    op.create_check_constraint("ck_credential_status", "credential", f"status IN {CREDENTIAL_STATUSES}")
    op.add_column("credential", sa.Column("verified_at", sa.DateTime(timezone=True)))
    op.add_column("credential", sa.Column("verified_by", postgresql.UUID(as_uuid=True)))
    op.add_column("credential", sa.Column("version", sa.Integer()))
    op.add_column("credential", _provenance())
    # credential.code already holds the definition id (for example CRED-NREMT-PARAMEDIC).
    op.create_index("ix_credential_personnel", "credential", ["personnel_id"])

    op.create_table(
        "apparatus",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agency.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("identifier", sa.String(80), nullable=False),
        sa.Column("apparatus_type", sa.String(80), nullable=False),
        sa.Column("nims_type_ref", sa.String(160)),
        sa.Column("readiness", sa.String(30), nullable=False, server_default="ready"),
        _provenance(),
        sa.CheckConstraint("readiness IN ('ready', 'limited', 'maintenance', 'out_of_service')", name="ck_apparatus_readiness"),
        sa.UniqueConstraint("agency_id", "identifier", name="uq_apparatus_agency_identifier"),
    )

    op.create_table(
        "equipment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("apparatus_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("apparatus.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("capability_ids", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
        sa.Column("readiness", sa.String(30), nullable=False, server_default="ready"),
        sa.Column("last_inspected_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("readiness IN ('ready', 'limited', 'maintenance', 'missing')", name="ck_equipment_readiness"),
    )
    op.create_index("ix_equipment_apparatus", "equipment", ["apparatus_id", "position"])

    op.drop_constraint("ck_unit_status", "unit", type_="check")
    op.create_check_constraint("ck_unit_status", "unit", f"status IN {UNIT_STATUSES}")
    # The contract allows a unit with no known position.
    op.alter_column("unit", "latitude", nullable=True)
    op.alter_column("unit", "longitude", nullable=True)
    op.add_column("unit", sa.Column("discipline", sa.String(20), nullable=False, server_default="fire"))
    op.add_column("unit", sa.Column("minimum_crew", sa.Integer()))
    op.add_column("unit", sa.Column("apparatus_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("apparatus.id", ondelete="SET NULL")))
    op.add_column("unit", _provenance())
    op.create_check_constraint("ck_unit_discipline", "unit", "discipline IN ('fire', 'police', 'ems')")
    op.create_check_constraint("ck_unit_minimum_crew", "unit", "minimum_crew IS NULL OR minimum_crew >= 1")

    op.create_table(
        "unit_crew",
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("unit.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("personnel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("personnel.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_unit_crew_personnel", "unit_crew", ["personnel_id"])


def downgrade() -> None:
    op.drop_index("ix_unit_crew_personnel", table_name="unit_crew")
    op.drop_table("unit_crew")

    op.drop_constraint("ck_unit_minimum_crew", "unit", type_="check")
    op.drop_constraint("ck_unit_discipline", "unit", type_="check")
    op.execute("UPDATE unit SET latitude = 0 WHERE latitude IS NULL")
    op.execute("UPDATE unit SET longitude = 0 WHERE longitude IS NULL")
    op.alter_column("unit", "latitude", nullable=False)
    op.alter_column("unit", "longitude", nullable=False)
    op.drop_column("unit", "provenance")
    op.drop_column("unit", "apparatus_id")
    op.drop_column("unit", "minimum_crew")
    op.drop_column("unit", "discipline")
    op.drop_constraint("ck_unit_status", "unit", type_="check")
    op.execute("UPDATE unit SET status = 'assigned' WHERE status IN ('dispatched_simulated', 'returning')")
    op.execute("UPDATE unit SET status = 'en_route' WHERE status = 'en_route_simulated'")
    op.execute("UPDATE unit SET status = 'on_scene' WHERE status = 'on_scene_simulated'")
    op.execute("UPDATE unit SET status = 'offline' WHERE status = 'off_duty'")
    op.create_check_constraint("ck_unit_status", "unit", f"status IN {UNIT_STATUSES_0001}")

    op.drop_index("ix_equipment_apparatus", table_name="equipment")
    op.drop_table("equipment")
    op.drop_table("apparatus")

    op.drop_index("ix_credential_personnel", table_name="credential")
    op.drop_column("credential", "provenance")
    op.drop_column("credential", "version")
    op.drop_column("credential", "verified_by")
    op.drop_column("credential", "verified_at")
    op.drop_constraint("ck_credential_status", "credential", type_="check")
    op.execute("UPDATE credential SET status = 'active' WHERE status IN ('valid', 'unverified')")
    op.create_check_constraint("ck_credential_status", "credential", f"status IN {CREDENTIAL_STATUSES_0001}")

    op.drop_constraint("ck_personnel_employment_status", "personnel", type_="check")
    op.drop_column("personnel", "provenance")
    op.drop_column("personnel", "employment_status")

    op.drop_constraint("ck_facility_type", "facility", type_="check")
    op.drop_column("facility", "provenance")
    op.drop_column("facility", "service_area_id")
    op.drop_column("facility", "facility_type")

    op.drop_constraint("ck_capability_kind", "capability", type_="check")
    op.drop_column("capability", "required_credential_definition_ids")
    op.drop_column("capability", "standard_refs")
    op.drop_column("capability", "kind")
