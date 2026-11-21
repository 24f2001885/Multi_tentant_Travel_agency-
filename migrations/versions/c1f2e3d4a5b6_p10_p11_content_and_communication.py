"""p10_p11_content_and_communication

Revision ID: c1f2e3d4a5b6
Revises: b8e0f45c7443
Create Date: 2026-10-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c1f2e3d4a5b6'
down_revision = 'b8e0f45c7443'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Update treks table (P10: created_by_user_id)
    with op.batch_alter_table('treks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created_by_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_treks_created_by_user_id', 'users', ['created_by_user_id'], ['id'])

    # 2. P10 tables
    op.create_table('trek_itinerary_days',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('trek_id', sa.Integer(), nullable=False),
        sa.Column('day_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.CheckConstraint('day_number >= 1', name='check_itinerary_day_number_positive'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trek_id'], ['treks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trek_id', 'day_number', name='uq_trek_itinerary_day_number')
    )
    with op.batch_alter_table('trek_itinerary_days', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trek_itinerary_days_organization_id'), ['organization_id'], unique=False)

    op.create_table('itinerary_elements',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('itinerary_day_id', sa.UUID(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('element_type', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('time_of_day', sa.String(length=20), nullable=True),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.CheckConstraint("element_type IN ('activity', 'meal', 'transport', 'accommodation', 'note')", name='check_itinerary_element_type'),
        sa.CheckConstraint('sequence >= 1', name='check_itinerary_element_sequence_positive'),
        sa.ForeignKeyConstraint(['itinerary_day_id'], ['trek_itinerary_days.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('itinerary_day_id', 'sequence', name='uq_itinerary_day_sequence')
    )
    with op.batch_alter_table('itinerary_elements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_itinerary_elements_organization_id'), ['organization_id'], unique=False)

    op.create_table('trek_media',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('trek_id', sa.Integer(), nullable=False),
        sa.Column('asset_url', sa.String(length=500), nullable=False),
        sa.Column('caption', sa.String(length=255), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('uploaded_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trek_id'], ['treks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('trek_media', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trek_media_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index('ix_trek_media_trek_sort', ['trek_id', 'sort_order'], unique=False)

    op.create_table('trek_reviews',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('trek_id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.CheckConstraint('rating >= 1 AND rating <= 5', name='check_review_rating_range'),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trek_id'], ['treks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('booking_id')
    )
    with op.batch_alter_table('trek_reviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trek_reviews_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index('ix_trek_reviews_trek_created', ['trek_id', 'created_at'], unique=False)

    # 3. P11 tables
    op.create_table('notification_consents',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column('consent_type', sa.String(length=15), nullable=False),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.CheckConstraint("channel IN ('email', 'whatsapp', 'sms')", name='check_consent_valid_channel'),
        sa.CheckConstraint("consent_type IN ('transactional', 'marketing')", name='check_consent_valid_type'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'channel', 'consent_type', name='uq_notification_consents_user_channel_type')
    )
    with op.batch_alter_table('notification_consents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notification_consents_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_consents_user_id'), ['user_id'], unique=False)

    op.create_table('notification_preferences',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.CheckConstraint("channel IN ('email', 'whatsapp', 'sms')", name='check_preference_valid_channel'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'channel', name='uq_notification_preferences_user_channel')
    )
    with op.batch_alter_table('notification_preferences', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notification_preferences_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_preferences_user_id'), ['user_id'], unique=False)

    op.create_table('message_campaigns',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=True),
        sa.Column('template_body', sa.Text(), nullable=False),
        sa.Column('audience_filter', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('test_sent_to_email', sa.String(length=255), nullable=True),
        sa.Column('test_sent_to_mobile', sa.String(length=30), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recipient_count', sa.Integer(), nullable=True),
        sa.Column('provider_template_name', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.CheckConstraint("channel IN ('email', 'whatsapp', 'sms')", name='check_campaign_valid_channel'),
        sa.CheckConstraint("status IN ('draft', 'tested', 'sent')", name='check_campaign_valid_status'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('message_campaigns', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_message_campaigns_created_by'), ['created_by'], unique=False)
        batch_op.create_index(batch_op.f('ix_message_campaigns_organization_id'), ['organization_id'], unique=False)

    op.create_table('message_campaign_recipients',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('campaign_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('delivery_status', sa.String(length=20), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('provider_message_id', sa.String(length=100), nullable=True),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.CheckConstraint("delivery_status IN ('queued', 'sent', 'delivered', 'read', 'failed')", name='check_recipient_delivery_status'),
        sa.ForeignKeyConstraint(['campaign_id'], ['message_campaigns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('campaign_id', 'user_id', name='uq_campaign_recipient_user')
    )
    with op.batch_alter_table('message_campaign_recipients', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_message_campaign_recipients_campaign_id'), ['campaign_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_message_campaign_recipients_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_message_campaign_recipients_provider_message_id'), ['provider_message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_message_campaign_recipients_user_id'), ['user_id'], unique=False)


def downgrade():
    op.drop_table('message_campaign_recipients')
    op.drop_table('message_campaigns')
    op.drop_table('notification_preferences')
    op.drop_table('notification_consents')
    op.drop_table('trek_reviews')
    op.drop_table('trek_media')
    op.drop_table('itinerary_elements')
    op.drop_table('trek_itinerary_days')
    with op.batch_alter_table('treks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_treks_created_by_user_id', type_='foreignkey')
        batch_op.drop_column('created_by_user_id')
