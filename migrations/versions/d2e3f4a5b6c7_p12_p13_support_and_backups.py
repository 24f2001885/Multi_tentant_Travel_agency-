"""p12_p13_support_and_backups

Revision ID: d2e3f4a5b6c7
Revises: c1f2e3d4a5b6
Create Date: 2026-10-06 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd2e3f4a5b6c7'
down_revision = 'c1f2e3d4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Milestone P12: ticket_messages table
    op.create_table('ticket_messages',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ticket_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ticket_messages_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ticket_messages_sender_id'), ['sender_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ticket_messages_ticket_id'), ['ticket_id'], unique=False)

    # 2. Milestone P13: tenant_backups table
    op.create_table('tenant_backups',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=True),
        sa.Column('triggered_by_user_id', sa.Integer(), nullable=True),
        sa.Column('triggered_by_platform_admin_id', sa.UUID(), nullable=True),
        sa.Column('storage_location', sa.String(length=500), nullable=False),
        sa.Column('file_size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('backup_type', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("backup_type IN ('tenant_export', 'full_platform')", name='check_valid_backup_type'),
        sa.CheckConstraint("status IN ('pending', 'completed', 'failed')", name='check_valid_backup_status'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['triggered_by_platform_admin_id'], ['platform_admins.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['triggered_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tenant_backups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tenant_backups_organization_id'), ['organization_id'], unique=False)


def downgrade():
    op.drop_table('tenant_backups')
    op.drop_table('ticket_messages')
