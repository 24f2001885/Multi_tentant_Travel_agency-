
from flask import Blueprint

trekker_bp = Blueprint(
    "trekker",
    __name__,
    template_folder="../templates/trekker",
    url_prefix="/trekker",
)

from app.trekker import routes  # noqa: E402,F401