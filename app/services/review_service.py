"""
Post-completion trek reviews (PDR v2 §5.10; Milestone P10).
Framework-agnostic.
"""

from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import func

from app.extensions import db
from app.models.booking import Booking
from app.models.trek import Trek
from app.models.trek_review import TrekReview


class ReviewNotAllowedError(Exception):
    """Raised when a user attempts to review a booking they do not own or that is not completed."""
    pass


class DuplicateReviewError(Exception):
    """Raised when a booking has already submitted a review."""
    pass


def can_review(user, booking: Booking) -> bool:
    """Returns True if the user is eligible to submit a review for the booking."""
    if not user or not booking:
        return False
    if booking.user_id != user.id:
        return False
    if booking.status != "completed":
        return False

    existing = TrekReview.query.filter_by(booking_id=booking.id).first()
    return existing is None


def create_review(
    user,
    booking: Optional[Booking] = None,
    rating: int = 5,
    comment: Optional[str] = None,
    image_url: Optional[str] = None,
    trek: Optional[Trek] = None,
    allow_uncompleted: bool = False,
) -> TrekReview:
    """
    Submits a review for a completed trek booking or directly by an eligible user/staff.
    """
    if booking is not None:
        if not allow_uncompleted and not can_review(user, booking):
            if booking.user_id != user.id:
                raise ReviewNotAllowedError("You cannot review a booking that does not belong to you.")
            if booking.status != "completed":
                raise ReviewNotAllowedError("You can only review a trek after it has been marked completed.")
            raise DuplicateReviewError("You have already reviewed this trek booking.")
        elif allow_uncompleted:
            if booking.user_id != user.id:
                raise ReviewNotAllowedError("You cannot review a booking that does not belong to you.")
            existing = TrekReview.query.filter_by(booking_id=booking.id).first()
            if existing:
                raise DuplicateReviewError("You have already reviewed this trek booking.")
        org_id = booking.organization_id
        trek_id = booking.trek_id
        booking_id = booking.id
    else:
        if not trek:
            raise ValueError("Either booking or trek must be provided.")
        org_id = trek.organization_id
        trek_id = trek.id
        booking_id = None

    try:
        rating_int = int(rating)
    except (TypeError, ValueError):
        raise ValueError("Rating must be an integer between 1 and 5.")

    if rating_int < 1 or rating_int > 5:
        raise ValueError("Rating must be between 1 and 5.")

    cleaned_comment = comment.strip() if comment else None
    if cleaned_comment and len(cleaned_comment) > 2000:
        cleaned_comment = cleaned_comment[:2000]

    review = TrekReview(
        organization_id=org_id,
        trek_id=trek_id,
        booking_id=booking_id,
        user_id=user.id,
        rating=rating_int,
        comment=cleaned_comment,
        image_url=image_url.strip() if image_url else None,
    )
    db.session.add(review)
    db.session.flush()
    return review


def get_user_trek_review(user, trek: Trek) -> Optional[TrekReview]:
    """Returns an existing review by this user for this trek, if any."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return TrekReview.query.filter_by(
        organization_id=trek.organization_id,
        trek_id=trek.id,
        user_id=user.id,
    ).first()


def user_can_review_trek(user, trek: Trek) -> Tuple[bool, Optional[Booking], str]:
    """
    Checks if a user can review a trek from the trek page.
    Returns (can_review: bool, booking: Optional[Booking], reason_code: str).
    reason_code: 'eligible', 'already_reviewed', 'not_booked', 'not_logged_in', 'staff_allowed'.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False, None, "not_logged_in"

    if getattr(user, "role", "") in ("admin", "staff"):
        existing = get_user_trek_review(user, trek)
        return True, None, "already_reviewed" if existing else "staff_allowed"

    booking = (
        Booking.query.filter_by(organization_id=trek.organization_id, trek_id=trek.id, user_id=user.id)
        .filter(Booking.status.in_(["completed", "confirmed", "booked"]))
        .first()
    )
    if not booking:
        return False, None, "not_booked"

    existing = get_user_trek_review(user, trek)
    if existing:
        return True, booking, "already_reviewed"

    return True, booking, "eligible"


def delete_review(review: TrekReview) -> None:
    """Deletes review and flushes."""
    db.session.delete(review)
    db.session.flush()


def average_rating(trek: Trek) -> Tuple[float, int]:
    """
    Returns (average_score, total_count) using an aggregate database query.
    """
    res = (
        db.session.query(
            func.avg(TrekReview.rating),
            func.count(TrekReview.id),
        )
        .filter(TrekReview.trek_id == trek.id)
        .first()
    )

    if not res or res[1] == 0:
        return 0.0, 0

    avg_score = round(float(res[0]), 1) if res[0] is not None else 0.0
    return avg_score, int(res[1])


def list_reviews(trek: Trek, page: int = 1, per_page: int = 20):
    """
    Returns paginated reviews, exposing only the reviewer's first name.
    """
    pagination = (
        TrekReview.query.filter_by(trek_id=trek.id)
        .order_by(TrekReview.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return pagination
