"""add_org_subscription_tier_and_features

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-23 08:45:00.000000

"""
import json
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None

ALL_ENTERPRISE_FEATURES = [
    "campaigns",
    "coupons",
    "custom_themes",
    "advanced_itineraries",
    "manual_bookings",
    "custom_domain",
    "backups",
    "refund_policy",
]


def upgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('subscription_tier', sa.String(length=50), nullable=False, server_default='enterprise')
        )
        batch_op.add_column(
            sa.Column('features', sa.JSON(), nullable=False, server_default=json.dumps(ALL_ENTERPRISE_FEATURES))
        )


def downgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('features')
        batch_op.drop_column('subscription_tier')
