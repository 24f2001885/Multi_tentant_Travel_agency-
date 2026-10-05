
from datetime import datetime, timedelta
from app import db
from app.models import Ticket, Booking, Trek

class MissingBookingReferenceError(Exception):
    pass

class BookingOwnershipError(Exception):
    pass

class AlreadyRespondedError(Exception):
    pass

def create_ticket(user_id, ticket_type, subject, message, booking_id=None) -> Ticket:
    
    if ticket_type not in ('query', 'problem'):
        raise ValueError("Invalid ticket type. Must be 'query' or 'problem'.")

    trek_id = None
    final_booking_id = None

    if ticket_type == 'problem':
        if booking_id is None:
            raise MissingBookingReferenceError("A problem ticket requires a valid booking reference.")
            
        booking = Booking.query.get(booking_id)
        if booking is None or booking.user_id != user_id:
            raise BookingOwnershipError("The supplied booking does not exist or does not belong to you.")
            
        final_booking_id = booking.id
        trek_id = booking.trek_id
        
    elif ticket_type == 'query':
        # Deliberately ignore any passed booking_id for queries
        final_booking_id = None
        trek_id = None

    now = datetime.utcnow()
    purge_after = now + timedelta(days=10) if ticket_type == 'query' else now + timedelta(days=90)

    ticket = Ticket(
        user_id=user_id,
        type=ticket_type,
        subject=subject,
        message=message,
        booking_id=final_booking_id,
        trek_id=trek_id,
        status='open',
        created_at=now,
        purge_after=purge_after
    )
    
    db.session.add(ticket)
    db.session.commit()
    
    return ticket

def get_scoped_tickets(actor, status=None, type_=None) -> list[Ticket]:
    query = Ticket.query

    if actor.role == 'admin':
        # Admin gets everything
        pass
        
    elif actor.role == 'staff':
        # Staff only gets 'problem' tickets tied to their assigned treks
        profile_id = getattr(actor.staff_profile, 'id', None)
        if not profile_id:
            return []  # Failsafe: if a staff has no profile yet, they see no tickets
            
        query = query.join(Trek, Ticket.trek_id == Trek.id).filter(
            Ticket.type == 'problem',
            Trek.assigned_staff_id == profile_id
        )
        
    elif actor.role == 'trekker':
        # Trekkers only get their own tickets
        query = query.filter(Ticket.user_id == actor.id)
        
    else:
        # Failsafe for unhandled roles
        return []

    # Apply optional filters
    if status:
        query = query.filter(Ticket.status == status)
    if type_:
        # Note: If staff sets type_='query', the resulting query combines 
        # (type=='problem' AND type=='query') and safely returns 0 rows.
        query = query.filter(Ticket.type == type_)

    return query.all()

def respond_ticket(ticket: Ticket, responder, response_text: str) -> Ticket:
    if ticket.response is not None:
        raise AlreadyRespondedError("This ticket already has a response and cannot be overwritten.")

    ticket.response = response_text
    ticket.responded_by_id = responder.id
    ticket.responded_at = datetime.utcnow()
    ticket.status = 'answered'
    return ticket

def resolve_ticket(ticket: Ticket) -> Ticket:
    ticket.status = 'resolved'
    
    return ticket

def purge_expired() -> int:
   
    deleted_count = db.session.query(Ticket).filter(
        Ticket.purge_after < datetime.utcnow()
    ).delete(synchronize_session=False)
    
    db.session.commit()
    
    return deleted_count