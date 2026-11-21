"""
Campaign Composer routes, shared by Tenant Admin and permitted Staff (UI/UX v2 §3.3, §3.4, §4.6; PDR v2 §14.1).
Attached to admin_bp.
"""

from datetime import datetime, timezone
from typing import Any
import uuid

from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin import admin_bp
from app.admin.forms import CampaignForm, TestSendForm
from app.auth.decorators import requires_capability, tenant_feature_required
from app.extensions import db, limiter
from app.models.message_campaign import MessageCampaign
from app.models.message_campaign_recipient import MessageCampaignRecipient
from app.services import audience_service, campaign_service


def _check_campaign_scoping(campaign: MessageCampaign) -> None:
    """Ensures staff can only view and modify campaigns they created."""
    org = getattr(g, "organization", None)
    if not org or campaign.organization_id != org.id:
        abort(404)

    is_tenant_admin = current_user.role == "admin" or current_user.id == org.tenant_admin_user_id
    if not is_tenant_admin and campaign.created_by != current_user.id:
        abort(403)


@admin_bp.route("/campaigns", methods=["GET"])
@login_required
@tenant_feature_required("campaigns")
@requires_capability("campaigns.compose")
def campaigns_list():
    org = g.organization
    is_tenant_admin = current_user.role == "admin" or current_user.id == org.tenant_admin_user_id

    query = MessageCampaign.query.order_by(MessageCampaign.created_at.desc())
    if not is_tenant_admin:
        query = query.filter_by(created_by=current_user.id)

    campaigns = query.all()
    layout = "shared/sidebar_admin.html" if current_user.role == "admin" else "shared/sidebar_staff.html"

    return render_template(
        "admin/campaigns_list.html",
        campaigns=campaigns,
        layout=layout,
    )


@admin_bp.route("/campaigns/new", methods=["GET", "POST"])
@login_required
@tenant_feature_required("campaigns")
@requires_capability("campaigns.compose")
def campaign_new():
    form = CampaignForm()
    layout = "shared/sidebar_admin.html" if current_user.role == "admin" else "shared/sidebar_staff.html"

    if form.validate_on_submit():
        # Build audience filter from form inputs
        audience_filter = {}
        if form.roles.data:
            audience_filter["roles"] = [form.roles.data]
        if form.booking_statuses.data:
            audience_filter["booking_statuses"] = [form.booking_statuses.data]
        if form.signup_from.data:
            audience_filter["signup_from"] = form.signup_from.data.isoformat()
        if form.signup_to.data:
            audience_filter["signup_to"] = form.signup_to.data.isoformat()
        if form.is_active.data:
            audience_filter["is_active"] = form.is_active.data == "true"
        if form.has_any_booking.data:
            audience_filter["has_any_booking"] = form.has_any_booking.data == "true"

        # Resolve selected delivery channels (multi-channel or single channel)
        selected_channels = request.form.getlist("channels")
        if not selected_channels:
            if form.channels.data:
                selected_channels = list(form.channels.data)
            elif form.channel.data:
                selected_channels = [form.channel.data]
            elif request.form.get("channel"):
                selected_channels = [request.form.get("channel")]

        selected_channels = [
            c.strip().lower() for c in selected_channels
            if c and c.strip().lower() in ("email", "whatsapp", "sms")
        ]
        # Deduplicate while preserving order
        seen = set()
        selected_channels = [c for c in selected_channels if not (c in seen or seen.add(c))]

        if not selected_channels:
            flash("Please select at least one delivery channel (Email, WhatsApp, or SMS).", "danger")
            return render_template(
                "admin/campaign_composer.html",
                form=form,
                campaign=None,
                layout=layout,
                allowed_merge_keys=sorted(campaign_service.ALLOWED_MERGE_KEYS),
            )

        # Resolve poster image (upload takes precedence over URL)
        poster_url = None
        if form.poster.data:
            try:
                poster_url = campaign_service.save_campaign_poster(form.poster.data, g.organization.id)
            except Exception as exc:
                flash(f"Poster upload error: {exc}", "danger")
                return render_template(
                    "admin/campaign_composer.html",
                    form=form,
                    campaign=None,
                    layout=layout,
                    allowed_merge_keys=sorted(campaign_service.ALLOWED_MERGE_KEYS),
                )
        elif form.poster_url.data and form.poster_url.data.strip():
            poster_url = form.poster_url.data.strip()

        try:
            created_campaigns = []
            for ch in selected_channels:
                camp = campaign_service.create_campaign(
                    actor=current_user,
                    org=g.organization,
                    channel=ch,
                    template_body=form.template_body.data,
                    audience_filter=audience_filter,
                    subject=form.subject.data if ch == "email" else (form.subject.data or None),
                    provider_template_name=form.provider_template_name.data if ch == "whatsapp" else None,
                    poster_url=poster_url,
                )
                created_campaigns.append(camp)

            db.session.commit()

            if len(created_campaigns) == 1:
                flash(f"Campaign draft for {created_campaigns[0].channel.capitalize()} created successfully.", "success")
                return redirect(url_for("admin.campaign_detail", id=created_campaigns[0].id))
            else:
                names = ", ".join(c.channel.capitalize() for c in created_campaigns)
                flash(f"Successfully created {len(created_campaigns)} campaign drafts across {names}.", "success")
                return redirect(url_for("admin.campaigns_list"))

        except (ValueError, audience_service.InvalidAudienceFilter) as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    return render_template(
        "admin/campaign_composer.html",
        form=form,
        campaign=None,
        layout=layout,
        allowed_merge_keys=sorted(campaign_service.ALLOWED_MERGE_KEYS),
    )


@admin_bp.route("/campaigns/<uuid:id>/edit", methods=["GET", "POST"])
@login_required
@tenant_feature_required("campaigns")
@requires_capability("campaigns.compose")
def campaign_edit(id: uuid.UUID):
    campaign = MessageCampaign.query.filter_by(id=id).first_or_404()
    _check_campaign_scoping(campaign)

    if campaign.status == "sent":
        flash("Sent campaigns cannot be edited.", "warning")
        return redirect(url_for("admin.campaign_detail", id=campaign.id))

    layout = "shared/sidebar_admin.html" if current_user.role == "admin" else "shared/sidebar_staff.html"
    form = CampaignForm(obj=campaign)

    if form.validate_on_submit():
        audience_filter = {}
        if form.roles.data:
            audience_filter["roles"] = [form.roles.data]
        if form.booking_statuses.data:
            audience_filter["booking_statuses"] = [form.booking_statuses.data]
        if form.signup_from.data:
            audience_filter["signup_from"] = form.signup_from.data.isoformat()
        if form.signup_to.data:
            audience_filter["signup_to"] = form.signup_to.data.isoformat()
        if form.is_active.data:
            audience_filter["is_active"] = form.is_active.data == "true"
        if form.has_any_booking.data:
            audience_filter["has_any_booking"] = form.has_any_booking.data == "true"

        # Resolve poster image
        poster_url = campaign.poster_url
        if form.poster.data:
            try:
                poster_url = campaign_service.save_campaign_poster(form.poster.data, g.organization.id)
            except Exception as exc:
                flash(f"Poster upload error: {exc}", "danger")
                return render_template(
                    "admin/campaign_composer.html",
                    form=form,
                    campaign=campaign,
                    layout=layout,
                    allowed_merge_keys=sorted(campaign_service.ALLOWED_MERGE_KEYS),
                )
        elif form.poster_url.data is not None:
            poster_url = form.poster_url.data.strip() if form.poster_url.data.strip() else None

        try:
            campaign_service.update_campaign(
                campaign=campaign,
                actor=current_user,
                channel=form.channel.data,
                template_body=form.template_body.data,
                audience_filter=audience_filter,
                subject=form.subject.data,
                provider_template_name=form.provider_template_name.data,
                poster_url=poster_url,
            )
            db.session.commit()
            flash("Campaign updated and reset to draft status. You must run a test send before broadcasting.", "info")
            return redirect(url_for("admin.campaign_detail", id=campaign.id))
        except (ValueError, audience_service.InvalidAudienceFilter, campaign_service.CampaignAlreadySentError) as exc:
            flash(str(exc), "danger")

    return render_template(
        "admin/campaign_composer.html",
        form=form,
        campaign=campaign,
        layout=layout,
        allowed_merge_keys=sorted(campaign_service.ALLOWED_MERGE_KEYS),
    )


@admin_bp.route("/campaigns/<uuid:id>", methods=["GET"])
@login_required
@tenant_feature_required("campaigns")
@requires_capability("campaigns.compose")
def campaign_detail(id: uuid.UUID):
    campaign = MessageCampaign.query.filter_by(id=id).first_or_404()
    _check_campaign_scoping(campaign)

    layout = "shared/sidebar_admin.html" if current_user.role == "admin" else "shared/sidebar_staff.html"
    test_form = TestSendForm()

    # Pre-populate test form defaults
    if not test_form.test_email.data:
        test_form.test_email.data = current_user.email
    if not test_form.test_mobile.data and current_user.mobile_number:
        test_form.test_mobile.data = f"{current_user.mobile_country_code}{current_user.mobile_number}"

    # Estimate reachable audience
    estimate = audience_service.estimate_count(
        campaign.audience_filter,
        channel=campaign.channel,
        consent_type="marketing",
    )

    # Calculate status counts if sent
    stats = {"queued": 0, "sent": 0, "delivered": 0, "read": 0, "failed": 0}
    if campaign.status == "sent":
        for r in campaign.recipients:
            st = r.delivery_status
            stats[st] = stats.get(st, 0) + 1

    # Recipient pagination
    page = request.args.get("page", 1, type=int)
    recipients_pagination = None
    if campaign.status == "sent":
        recipients_pagination = (
            MessageCampaignRecipient.query.filter_by(campaign_id=campaign.id)
            .paginate(page=page, per_page=20, error_out=False)
        )

    return render_template(
        "admin/campaign_detail.html",
        campaign=campaign,
        stats=stats,
        estimate=estimate,
        test_form=test_form,
        recipients_pagination=recipients_pagination,
        layout=layout,
    )


@admin_bp.route("/campaigns/<uuid:id>/test-send", methods=["POST"])
@login_required
@tenant_feature_required("campaigns")
@requires_capability("campaigns.compose")
@limiter.limit("10/hour")
def campaign_test_send(id: uuid.UUID):
    campaign = MessageCampaign.query.filter_by(id=id).first_or_404()
    _check_campaign_scoping(campaign)

    test_form = TestSendForm()
    if test_form.validate_on_submit():
        try:
            campaign_service.test_send(
                campaign=campaign,
                actor=current_user,
                test_email=test_form.test_email.data,
                test_mobile=test_form.test_mobile.data,
            )
            db.session.commit()
            target = test_form.test_email.data if campaign.channel == "email" else test_form.test_mobile.data
            flash(f"Test message dispatched to {target}. Campaign status marked as 'tested'.", "success")
        except Exception as exc:
            flash(f"Test send failed: {str(exc)}", "danger")
    else:
        flash("Invalid test destination provided.", "danger")

    return redirect(url_for("admin.campaign_detail", id=campaign.id))


@admin_bp.route("/campaigns/<uuid:id>/send", methods=["POST"])
@login_required
@tenant_feature_required("campaigns")
@requires_capability("campaigns.compose")
@limiter.limit("5/hour")
def campaign_send(id: uuid.UUID):
    campaign = MessageCampaign.query.filter_by(id=id).first_or_404()
    _check_campaign_scoping(campaign)

    if campaign.status != "tested":
        flash("A campaign must be test-sent successfully before final broadcast.", "warning")
        return redirect(url_for("admin.campaign_detail", id=campaign.id))

    try:
        count = campaign_service.send_campaign(campaign=campaign, actor=current_user)
        db.session.commit()
        flash(f"Campaign successfully broadcast to {count} verified recipients.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Failed to broadcast campaign: {str(exc)}", "danger")

    return redirect(url_for("admin.campaign_detail", id=campaign.id))
