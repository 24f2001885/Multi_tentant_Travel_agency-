from flask import Blueprint

admin_bp = Blueprint(
    "admin", __name__, template_folder="../templates/admin", url_prefix="/admin"
)

from app.admin import routes  # noqa: E402,F401
from app.admin import call_requests_routes  # noqa: E402,F401

# print("[DEBUG] admin call_requests_routes imported")