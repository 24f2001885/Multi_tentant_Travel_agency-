"""p8_identity_access_governance

Revision ID: 691fed580e0d
Revises: 500d0c223ed1
Create Date: 2026-10-05 22:46:34.051447

"""
from datetime import datetime, timezone
import os
import uuid

from alembic import op
import sqlalchemy as sa
from werkzeug.security import generate_password_hash


# revision identifiers, used by Alembic.
revision = '691fed580e0d'
down_revision = '500d0c223ed1'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Global capabilities table
    op.create_table('capabilities',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=False),
        sa.Column('is_protected', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key')
    )

    # 2. Platform Admins table (independent control plane)
    op.create_table('platform_admins',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )

    # 3. Meta-audit trail for cross-tenant reads
    op.create_table('platform_access_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('platform_admin_id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['platform_admin_id'], ['platform_admins.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 4. Roles table (tenant-scoped)
    op.create_table('roles',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('is_starter_template', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name', name='uq_roles_org_name')
    )
    with op.batch_alter_table('roles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_roles_organization_id'), ['organization_id'], unique=False)

    # 5. Role Capabilities association table
    op.create_table('role_capabilities',
        sa.Column('role_id', sa.UUID(), nullable=False),
        sa.Column('capability_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['capability_id'], ['capabilities.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('role_id', 'capability_id')
    )

    # 6. Backfill platform admin for any existing organizations prior to tightening FK + NOT NULL
    conn = op.get_bind()
    null_orgs_count = conn.execute(
        sa.text("SELECT count(*) FROM organizations WHERE created_by_platform_admin_id IS NULL")
    ).scalar()
    if null_orgs_count and null_orgs_count > 0:
        admin_row = conn.execute(sa.text("SELECT id FROM platform_admins LIMIT 1")).fetchone()
        if not admin_row:
            admin_id = uuid.uuid4()
            email = os.getenv("PLATFORM_ADMIN_EMAIL", "owner@platform.local").lower()
            name = os.getenv("PLATFORM_ADMIN_NAME", "Platform Owner")
            pwd = os.getenv("PLATFORM_ADMIN_PASSWORD", "change-me-platform-password")
            p_hash = generate_password_hash(pwd)
            conn.execute(
                sa.text(
                    "INSERT INTO platform_admins (id, name, email, password_hash, is_active, created_at) "
                    "VALUES (:id, :name, :email, :pwd, True, :now)"
                ),
                {"id": admin_id, "name": name, "email": email, "pwd": p_hash, "now": datetime.now(timezone.utc)}
            )
        else:
            admin_id = admin_row[0]

        conn.execute(
            sa.text("UPDATE organizations SET created_by_platform_admin_id = :admin_id WHERE created_by_platform_admin_id IS NULL"),
            {"admin_id": admin_id}
        )

    # 7. Tighten organizations.created_by_platform_admin_id to FK + NOT NULL
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.alter_column('created_by_platform_admin_id',
               existing_type=sa.UUID(),
               nullable=False)
        batch_op.create_foreign_key('fk_org_created_by_platform_admin', 'platform_admins', ['created_by_platform_admin_id'], ['id'])

    # 8. Staff Profiles: add role_id foreign key
    with op.batch_alter_table('staff_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role_id', sa.UUID(), nullable=True))
        batch_op.create_foreign_key('fk_staff_profiles_role_id', 'roles', ['role_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('staff_profiles', schema=None) as batch_op:
        batch_op.drop_constraint('fk_staff_profiles_role_id', type_='foreignkey')
        batch_op.drop_column('role_id')

    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_constraint('fk_org_created_by_platform_admin', type_='foreignkey')
        batch_op.alter_column('created_by_platform_admin_id',
               existing_type=sa.UUID(),
               nullable=True)

    op.drop_table('role_capabilities')
    with op.batch_alter_table('roles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_roles_organization_id'))

    op.drop_table('roles')
    op.drop_table('platform_access_logs')
    op.drop_table('platform_admins')
    op.drop_table('capabilities')
