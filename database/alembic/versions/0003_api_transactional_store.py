"""Tables the API writes to at run time: incidents, classifications, recommendations, reviews, status events, audit.

Each row keeps the columns the API looks rows up or orders by, and the complete contract
model in `payload`, so a response is rebuilt exactly as it was stored. The 0001 tables of the
same names (incident, review, audit, ...) are left as they are: their constraints (a unique
NOT NULL idempotency key on review, a string reviewer id, a different incident status and
priority vocabulary) cannot hold the contract's shapes without losing data.

`seq` preserves insertion order, which the in-memory store returned and the API's default
sort relies on.

Revision ID: 0003_api_transactional_store
Revises: 0002_api_resource_model
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_api_transactional_store"
down_revision = "0002_api_resource_model"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _seq() -> sa.Column:
    return sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False, unique=True)


def _payload() -> sa.Column:
    return sa.Column("payload", postgresql.JSONB(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "api_meta",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )

    op.execute("CREATE SEQUENCE api_incident_number_seq")

    op.create_table(
        "api_incident",
        _id(), _seq(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("number", sa.String(50), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reported_category", sa.String(80)),
        sa.Column("mock_scenario_id", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _payload(),
    )
    op.create_index("ix_api_incident_status", "api_incident", ["status"])

    op.create_table(
        "api_classification",
        _id(), _seq(),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("api_incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _payload(),
    )
    op.create_index("ix_api_classification_incident", "api_classification", ["incident_id"])

    op.create_table(
        "api_recommendation",
        _id(), _seq(),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("api_incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _payload(),
    )
    op.create_index("ix_api_recommendation_incident", "api_recommendation", ["incident_id"])

    op.create_table(
        "api_review",
        _id(), _seq(),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("api_recommendation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _payload(),
    )
    op.create_index("ix_api_review_recommendation", "api_review", ["recommendation_id"])

    op.create_table(
        "api_unit_status_event",
        _id(), _seq(),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("unit.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        _payload(),
    )
    op.create_index("ix_api_unit_status_event_unit", "api_unit_status_event", ["unit_id", "recorded_at"])

    op.create_table(
        "api_audit_event",
        _id(), _seq(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        _payload(),
    )
    op.create_index("ix_api_audit_event_tenant", "api_audit_event", ["tenant_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("api_audit_event")
    op.drop_table("api_unit_status_event")
    op.drop_table("api_review")
    op.drop_table("api_recommendation")
    op.drop_table("api_classification")
    op.drop_table("api_incident")
    op.execute("DROP SEQUENCE api_incident_number_seq")
    op.drop_table("api_meta")
