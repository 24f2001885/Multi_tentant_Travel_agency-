
from functools import wraps

from flask import abort, g, redirect, url_for
from flask_login import current_user

def role_required(*roles):
    """Redirects anonymous users to login; aborts 403 for the wrong role."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator

def approved_staff_required(view):
    

    @wraps(view)
    def wrapped(*args, **kwargs):
        profile = getattr(current_user, "staff_profile", None)
        if profile is None or profile.approval_status != "approved":
            return redirect(url_for("auth.pending_approval"))
        return view(*args, **kwargs)

    return wrapped

def staff_owns_trek(view):
   

    @wraps(view)
    def wrapped(*args, **kwargs):
        from app.models import Trek  # local import avoids circular import

        trek_id = kwargs.get("trek_id", kwargs.get("id"))
        trek = Trek.query.get_or_404(trek_id)

        profile = getattr(current_user, "staff_profile", None)
        if profile is None or trek.assigned_staff_id != profile.id:
            abort(403)

        g.trek = trek
        return view(*args, **kwargs)

    return wrapped

def ticket_access_required(view):
 

    @wraps(view)
    def wrapped(*args, **kwargs):
        from app.models import Ticket  # local import avoids circular import

        ticket_id = kwargs.get("id", kwargs.get("ticket_id"))
        ticket = Ticket.query.get_or_404(ticket_id)

        has_access = False

        if current_user.role == 'admin':
            # Admin sees all tickets
            has_access = True
            
        elif current_user.role == 'trekker' and ticket.user_id == current_user.id:
            # Trekkers can only see their own tickets
            has_access = True
            
        elif current_user.role == 'staff':
            # Staff see only 'problem' tickets tied to a trek they are assigned to
            profile = getattr(current_user, "staff_profile", None)
            if (
                ticket.type == 'problem' 
                and ticket.trek is not None 
                and profile is not None
                and ticket.trek.assigned_staff_id == profile.id
            ):
                has_access = True

        if not has_access:
            abort(403)

        g.ticket = ticket
        return view(*args, **kwargs)

    return wrapped