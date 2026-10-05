
from flask import Blueprint

tickets_bp = Blueprint(
    "tickets", 
    __name__,
    template_folder="../templates/tickets", 
    url_prefix="/tickets"
)

# DEBUGGING

from app.tickets import routes