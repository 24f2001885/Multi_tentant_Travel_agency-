"""
Blueprint for the Platform Admin control plane — structurally separate from every
tenant blueprint (TDR v2 §2).
"""

from flask import Blueprint

platform_bp = Blueprint(
    "platform",
    __name__,
    template_folder="../templates/platform",
    url_prefix="/platform",
)

platform_root_bp = Blueprint(
    "platform_root",
    __name__,
    template_folder="../templates/platform",
)

# print(f"[DEBUG] platform blueprint registered: {platform_bp.name}")

from app.platform import routes  # noqa: E402, F401
