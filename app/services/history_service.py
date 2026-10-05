
from typing import List, Optional, Dict, Any
from app.models import Booking, Trek

def get_booking_history(actor, status: Optional[str] = None) -> List[Booking]:
   
    query = Booking.query

    if actor.role == 'admin':
       
        pass
    
    elif actor.role == 'staff':
        
        if not hasattr(actor, 'staff_profile') or not actor.staff_profile:
            return []
        query = query.join(Trek).filter(Trek.assigned_staff_id == actor.staff_profile.id)
    
    elif actor.role == 'trekker':
       
        query = query.filter(Booking.user_id == actor.id)
    
    else:
        
        return []

    if status:
        query = query.filter(Booking.status == status)

    result = query.all()
    return result

def get_dashboard_counts(actor) -> Dict[str, Any]:
    
    scoped_bookings = get_booking_history(actor)
    
    booking_counts = {
        "total": len(scoped_bookings),
        "pending": sum(1 for b in scoped_bookings if b.status == "pending"),
        "booked": sum(1 for b in scoped_bookings if b.status == "booked"),
        "cancelled": sum(1 for b in scoped_bookings if b.status == "cancelled")
    }

    trek_query = Trek.query
    
    if actor.role == 'staff':
        if hasattr(actor, 'staff_profile') and actor.staff_profile:
            trek_query = trek_query.filter(Trek.assigned_staff_id == actor.staff_profile.id)
        else:
            trek_query = trek_query.filter(False) # Return empty if no profile
    elif actor.role == 'trekker':
       
        trek_query = trek_query.filter(False)

    scoped_treks = trek_query.all()
    
    trek_counts = {
        "total": len(scoped_treks),
        "draft": sum(1 for t in scoped_treks if t.status == "draft"),
        "open": sum(1 for t in scoped_treks if t.status == "open"),
        "in_progress": sum(1 for t in scoped_treks if t.status == "in_progress"),
        "completed": sum(1 for t in scoped_treks if t.status == "completed"),
        "cancelled": sum(1 for t in scoped_treks if t.status == "cancelled")
    }

    return {
        "bookings": booking_counts,
        "treks": trek_counts
    }