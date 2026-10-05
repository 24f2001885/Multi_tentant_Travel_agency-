
from flask import render_template
from flask_login import current_user

from app.main import main_bp
from app.models.trek import Trek

@main_bp.route("/")
def landing():
    
    treks = (
        Trek.query.filter(Trek.status == "open", Trek.available_slots > 0)
        .order_by(Trek.start_date.asc())
        .all()
    )
    return render_template("main/landing.html", treks=treks)