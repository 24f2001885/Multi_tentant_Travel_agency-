"""add_trek_itinerary_layout

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-23 08:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('treks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('itinerary_layout', sa.String(length=50), nullable=False, server_default='accordion'))


def downgrade():
    with op.batch_alter_table('treks', schema=None) as batch_op:
        batch_op.drop_column('itinerary_layout')
