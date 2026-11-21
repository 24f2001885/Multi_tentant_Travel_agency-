"""add_campaign_poster_url

Revision ID: g1h2i3j4k5l6
Revises: d4e5f6a7b8c9
Create Date: 2026-11-20 05:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'g1h2i3j4k5l6'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('message_campaigns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('poster_url', sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table('message_campaigns', schema=None) as batch_op:
        batch_op.drop_column('poster_url')
