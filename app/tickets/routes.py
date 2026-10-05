

from flask import render_template, redirect, url_for, flash, request, g, abort, session
from flask_login import login_required, current_user
from app import db
from app.tickets import tickets_bp
from app.tickets.forms import RaiseTicketForm, RespondTicketForm
from app.auth.decorators import role_required, ticket_access_required
from app.models import Booking
from app.services import ticket_service, notification_service
from app.services.ticket_service import MissingBookingReferenceError, BookingOwnershipError

@tickets_bp.route('/raise', methods=['GET', 'POST'])
@login_required
@role_required('trekker')
def raise_ticket():
    form = RaiseTicketForm()
    
    # Populate the booking_id choices exclusively from the user's bookings
    user_bookings = Booking.query.filter_by(user_id=current_user.id).all()
    form.booking_id.choices = [(0, '--- No Booking / N/A ---')] + [
        (b.id, f"Booking #{b.id} - {b.trek.name}") for b in user_bookings
    ]
    
    if form.validate_on_submit():
        # Handle the 0 placeholder for optional booking selection
        booking_val = form.booking_id.data if form.booking_id.data and form.booking_id.data != 0 else None
        
        try:
            ticket_service.create_ticket(
                user_id=current_user.id,
                ticket_type=form.type.data,
                subject=form.subject.data,
                message=form.message.data,
                booking_id=booking_val
            )
            flash("Support ticket raised successfully.", "success")
            return redirect(url_for('tickets.list_tickets'))
            
        except (MissingBookingReferenceError, BookingOwnershipError) as e:
            flash(str(e), "danger")
           
    return render_template('raise.html', form=form)

@tickets_bp.route('/', methods=['GET'])
@tickets_bp.route('', methods=['GET'])
@login_required
def list_tickets():
    # print(f"[DEBUG] tickets cap check role={current_user.role} caps={session.get('capability_keys')}")
    if current_user.role == 'staff':
        caps = session.get("capability_keys") or []
        if 'tickets.view' not in caps:
            abort(403)

    status = request.args.get('status')
    type_ = request.args.get('type')
    
    tickets = ticket_service.get_scoped_tickets(current_user, status=status, type_=type_)
    
    return render_template('list.html', tickets=tickets)

@tickets_bp.route('/<hashid:id>', methods=['GET'])
@login_required
@ticket_access_required
def detail_ticket(id):
    # print(f"[DEBUG] tickets cap check role={current_user.role} caps={session.get('capability_keys')}")
    if current_user.role == 'staff':
        caps = session.get("capability_keys") or []
        if 'tickets.view' not in caps:
            abort(403)

    respond_form = None
    if current_user.role == 'admin':
        if g.ticket.response is None:
            respond_form = RespondTicketForm()
    elif current_user.role == 'staff':
        caps = session.get("capability_keys") or []
        if 'tickets.respond' in caps and g.ticket.response is None:
            respond_form = RespondTicketForm()
        
    can_resolve = False
    if current_user.id == g.ticket.user_id and g.ticket.status == 'answered':
        can_resolve = True
        
    return render_template(
        'detail.html', 
        ticket=g.ticket, 
        form=respond_form, 
        can_resolve=can_resolve
    )

@tickets_bp.route('/<hashid:id>/respond', methods=['POST'])
@login_required
@ticket_access_required
def respond_ticket(id):
    # print(f"[DEBUG] tickets cap check role={current_user.role} caps={session.get('capability_keys')}")
    if current_user.role not in ('staff', 'admin'):
        abort(403)

    if current_user.role == 'staff':
        caps = session.get("capability_keys") or []
        if 'tickets.respond' not in caps:
            abort(403)
        
    form = RespondTicketForm()
    if form.validate_on_submit():
        if g.ticket.response is not None:
            flash("This ticket has already been responded to. Overwriting is not permitted.", "danger")
            return redirect(url_for('tickets.detail_ticket', id=id))
            
        ticket_service.respond_ticket(
            g.ticket, 
            responder=current_user, 
            response_text=form.response.data
        )
        db.session.commit()
        
        notification_service.send_ticket_response_email(g.ticket)
        flash("Response submitted successfully.", "success")
        
    return redirect(url_for('tickets.detail_ticket', id=id))

@tickets_bp.route('/<hashid:id>/resolve', methods=['POST'])
@login_required
@ticket_access_required
def resolve_ticket(id):
    # print(f"[DEBUG] hashid route {request.path}")
    if current_user.id != g.ticket.user_id:
        abort(403)
        
    ticket_service.resolve_ticket(g.ticket)
    db.session.commit()
    
    flash("Ticket has been marked as resolved.", "success")
    return redirect(url_for('tickets.detail_ticket', id=id))