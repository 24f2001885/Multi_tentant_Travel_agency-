"""add_itinerary_points_and_important_points

Revision ID: a1b2c3d4e5f6
Revises: f5a6b7c8d9e0
Create Date: 2026-10-23 02:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('trek_itinerary_days', schema=None) as batch_op:
        batch_op.add_column(sa.Column('important_points', sa.JSON(), nullable=True))

    with op.batch_alter_table('itinerary_elements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('points', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('itinerary_elements', schema=None) as batch_op:
        batch_op.drop_column('points')

    with op.batch_alter_table('trek_itinerary_days', schema=None) as batch_op:
        batch_op.drop_column('important_points')
