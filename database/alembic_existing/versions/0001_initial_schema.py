"""Initial ResponseMatch MVP schema.

Draft migration for the ResponseMatch MVP described in the project plan.
Uses PostgreSQL-compatible SQLAlchemy/Alembic types and intentionally does
not require PostGIS. Review column names/enum values against the team's
OpenAPI contract before applying to a shared database.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Organizational / reference data
    op.create_table(
        "agency",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "facility",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agency.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("address", sa.String(300)),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_facility_latitude"),
        sa.CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_facility_longitude"),
        sa.UniqueConstraint("agency_id", "code", name="uq_facility_agency_code"),
    )

    op.create_table(
        "capability",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("category", sa.String(80)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "personnel",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agency.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("facility.id", ondelete="SET NULL")),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("role", sa.String(80), nullable=False, server_default="responder"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("personnel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("personnel.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.CheckConstraint("status IN ('active', 'expired', 'suspended', 'revoked')", name="ck_credential_status"),
    )

    op.create_table(
        "unit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agency.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("facility.id", ondelete="SET NULL")),
        sa.Column("unit_number", sa.String(40), nullable=False),
        sa.Column("unit_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="available"),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_unit_latitude"),
        sa.CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_unit_longitude"),
        sa.CheckConstraint("status IN ('available', 'assigned', 'en_route', 'on_scene', 'out_of_service', 'offline')", name="ck_unit_status"),
        sa.UniqueConstraint("agency_id", "unit_number", name="uq_unit_agency_number"),
    )

    op.create_table(
        "unit_capability",
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("unit.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("capability_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("capability.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("state", sa.String(30), nullable=False, server_default="available"),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint("state IN ('available', 'unavailable', 'unknown')", name="ck_unit_capability_state"),
    )

    op.create_table(
        "incident",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agency.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("incident_number", sa.String(50), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="intake"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="unassigned"),
        sa.Column("priority_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("category_suggested", sa.String(100)),
        sa.Column("category_confirmed", sa.String(100)),
        sa.Column("classification_confidence", sa.Float()),
        sa.Column("classification_model_version", sa.String(100)),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("address", sa.String(300)),
        sa.Column("mock_scenario_id", sa.String(60)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_incident_latitude"),
        sa.CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_incident_longitude"),
        sa.CheckConstraint("priority IN ('P1', 'P2', 'P3', 'P4', 'unassigned')", name="ck_incident_priority"),
        sa.CheckConstraint("status IN ('intake', 'pending_dispatch', 'recommendation_ready', 'dispatched_simulated', 'on_scene_simulated', 'resolved', 'cancelled')", name="ck_incident_status"),
        sa.UniqueConstraint("agency_id", "incident_number", name="uq_incident_agency_number"),
    )

    op.create_table(
        "incident_capability_requirement",
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incident.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("capability_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("capability.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(30), nullable=False, server_default="rules"),
    )

    op.create_table(
        "incident_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.String(120)),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "recommendation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(30), nullable=False, server_default="current"),
        sa.Column("algorithm_version", sa.String(100)),
        sa.Column("input_incident_version", sa.Integer(), nullable=False),
        sa.Column("reasoning_summary", sa.Text()),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("status IN ('current', 'stale', 'reviewed', 'superseded')", name="ck_recommendation_status"),
    )

    op.create_table(
        "recommendation_candidate",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recommendation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("unit.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("ineligibility_reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("distance_km", sa.Float()),
        sa.Column("eta_seconds", sa.Integer()),
        sa.Column("eta_method", sa.String(40)),
        sa.Column("coverage_impact", sa.Float()),
        sa.Column("score", sa.Float()),
        sa.UniqueConstraint("recommendation_id", "unit_id", name="uq_recommendation_candidate_unit"),
        sa.UniqueConstraint("recommendation_id", "rank", name="uq_recommendation_candidate_rank"),
        sa.CheckConstraint("rank > 0", name="ck_recommendation_candidate_rank"),
        sa.CheckConstraint("distance_km IS NULL OR distance_km >= 0", name="ck_candidate_distance"),
        sa.CheckConstraint("eta_seconds IS NULL OR eta_seconds >= 0", name="ck_candidate_eta"),
    )

    op.create_table(
        "review",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recommendation.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("selected_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recommendation_candidate.id", ondelete="RESTRICT")),
        sa.Column("reviewer_id", sa.String(120), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("simulated_assignment", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("decision IN ('accepted', 'rejected', 'modified')", name="ck_review_decision"),
    )

    op.create_table(
        "unit_status_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("unit.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("previous_status", sa.String(40)),
        sa.Column("new_status", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(120)),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", sa.String(120)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80)),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Common lookup indexes
    op.create_index("ix_unit_agency_status", "unit", ["agency_id", "status"])
    op.create_index("ix_incident_agency_status_created", "incident", ["agency_id", "status", "created_at"])
    op.create_index("ix_incident_event_incident_created", "incident_event", ["incident_id", "created_at"])
    op.create_index("ix_recommendation_incident_generated", "recommendation", ["incident_id", "generated_at"])
    op.create_index("ix_candidate_recommendation_rank", "recommendation_candidate", ["recommendation_id", "rank"])
    op.create_index("ix_unit_status_event_unit_created", "unit_status_event", ["unit_id", "created_at"])
    op.create_index("ix_audit_entity_created", "audit", ["entity_type", "entity_id", "created_at"])


def downgrade() -> None:
    # Drop dependents before the tables they reference.
    op.drop_index("ix_audit_entity_created", table_name="audit")
    op.drop_index("ix_unit_status_event_unit_created", table_name="unit_status_event")
    op.drop_index("ix_candidate_recommendation_rank", table_name="recommendation_candidate")
    op.drop_index("ix_recommendation_incident_generated", table_name="recommendation")
    op.drop_index("ix_incident_event_incident_created", table_name="incident_event")
    op.drop_index("ix_incident_agency_status_created", table_name="incident")
    op.drop_index("ix_unit_agency_status", table_name="unit")

    op.drop_table("audit")
    op.drop_table("unit_status_event")
    op.drop_table("review")
    op.drop_table("recommendation_candidate")
    op.drop_table("recommendation")
    op.drop_table("incident_event")
    op.drop_table("incident_capability_requirement")
    op.drop_table("incident")
    op.drop_table("unit_capability")
    op.drop_table("unit")
    op.drop_table("credential")
    op.drop_table("personnel")
    op.drop_table("capability")
    op.drop_table("facility")
    op.drop_table("agency")

