
from flask import Blueprint

staff_bp = Blueprint(
    "staff",
    __name__,
    template_folder="../templates/staff",
    url_prefix="/staff",
)

from app.staff import routes  # noqa: E402,F401