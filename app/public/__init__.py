"""
Blueprint for anonymous-safe public pages: landing, browse, trek detail, legal text,
and call-request submissions (TDR v2 §5.4; Blueprint v2 §1).
Shares the trekker/ template tree.
"""

from flask import Blueprint

public_bp = Blueprint(
    "public",
    __name__,
    template_folder="../templates/trekker",
)

# print(f"[DEBUG] public blueprint registered: {public_bp.name}")

from app.public import routes  # noqa: E402, F401
