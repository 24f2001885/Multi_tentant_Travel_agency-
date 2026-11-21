"""
Seeds realistic data and sets themes for Tenant A and Tenant B
to demonstrate multi-tenant thematic differentiation.
"""

from datetime import date, datetime, timedelta, timezone
from app import create_app, db
from app.models.organization import Organization
from app.models.domain import Domain
from app.models.trek import Trek
from app.models.trek_itinerary_day import TrekItineraryDay
from app.models.itinerary_element import ItineraryElement
from app.models.trek_review import TrekReview
from app.models.booking import Booking
from app.models.user import User
from app.tenancy.scoping import tenant_bypass, tenant_context

app = create_app()

with app.app_context(), tenant_bypass():
    print("Configuring Tenant A and Tenant B...")

    # 1. Tenant A (Default Theme / Alpine Adventures)
    dom_a = Domain.query.filter_by(domain_name="a.localhost").first()
    if not dom_a:
        print("Domain a.localhost not found!")
    else:
        org_a = Organization.query.get(dom_a.organization_id)
        org_a.name = "Alpine Adventures"
        org_a.theme = None  # Default theme (pine green / light stone)
        org_a.primary_color = "#16A34A"
        org_a.secondary_color = "#D97706"
        org_a.contact_email = "support@a.localhost"
        org_a.contact_phone = "+91 98765 43210"
        db.session.commit()
        print(f"Updated Tenant A: {org_a.name} (theme=None)")

        # Seed Trek for Tenant A if not exists
        with tenant_context(org_a.id):
            trek_a = Trek.query.filter_by(organization_id=org_a.id, name="Roopkund Mystery Lake").first()
            if trek_a:
                trek_a.itinerary_layout = "timeline"
                db.session.commit()
                print("Updated Roopkund layout to 'timeline'")
            else:
                trek_a = Trek(
                    organization_id=org_a.id,
                    name="Roopkund Mystery Lake",
                    location="Uttarakhand",
                    difficulty="moderate",
                    duration_days=6,
                    total_slots=16,
                    available_slots=12,
                    price=9500.0,
                    status="open",
                    itinerary_layout="timeline",
                    start_date=date.today() + timedelta(days=20),
                    end_date=date.today() + timedelta(days=26),
                    description="Climb through dense rhododendron forests and high meadows of Ali and Bedni Bugyal leading to the mysterious glacial tarn of Roopkund nestled at 15,750 ft.",
                )
                db.session.add(trek_a)
                db.session.flush()

                # Add Itinerary Days
                day1 = TrekItineraryDay(
                    organization_id=org_a.id,
                    trek_id=trek_a.id,
                    day_number=1,
                    title="Lohajung Basecamp Arrival",
                    description="Assemble at Lohajung basecamp. Equipment inspection and briefing.",
                    important_points=["Submit physical fitness certificate", "Collect microspikes and gaiters"],
                )
                db.session.add(day1)
                db.session.flush()

                el1 = ItineraryElement(
                    organization_id=org_a.id,
                    itinerary_day_id=day1.id,
                    sequence=1,
                    element_type="activity",
                    title="Trek Briefing & Gear Check",
                    time_of_day="04:00 PM",
                    description="Orientation with lead guides and gear inspection.",
                    points=["Mandatory headlamp test", "Verify water purification tablets"],
                )
                db.session.add(el1)

                day2 = TrekItineraryDay(
                    organization_id=org_a.id,
                    trek_id=trek_a.id,
                    day_number=2,
                    title="Lohajung to Didna Village",
                    description="A scenic trek crossing the Neel Ganga river climbing to Didna.",
                    important_points=["Carry 2L water minimum", "Trek poles highly recommended"],
                )
                db.session.add(day2)
                db.session.commit()
                print("Seeded Roopkund Trek for Tenant A.")

    # 2. Tenant B (Glacier & Obsidian Theme / Apex Altitude Expeditions)
    dom_b = Domain.query.filter_by(domain_name="b.localhost").first()
    if not dom_b:
        print("Domain b.localhost not found!")
    else:
        org_b = Organization.query.get(dom_b.organization_id)
        org_b.name = "Apex Altitude Expeditions"
        org_b.theme = "glacier_obsidian"  # Custom Theme
        org_b.primary_color = "#00F5D4"
        org_b.secondary_color = "#FF6B35"
        org_b.contact_email = "dispatch@apex.localhost"
        org_b.contact_phone = "+91 98888 77777"
        db.session.commit()
        print(f"Updated Tenant B: {org_b.name} (theme={org_b.theme})")

        # Seed Trek for Tenant B if not exists
        with tenant_context(org_b.id):
            trek_b = Trek.query.filter_by(organization_id=org_b.id, name="Chadar Frozen River Expedition").first()
            if trek_b:
                trek_b.itinerary_layout = "magazine"
                db.session.commit()
                print("Updated Chadar layout to 'magazine'")
            else:
                trek_b = Trek(
                    organization_id=org_b.id,
                    name="Chadar Frozen River Expedition",
                    location="Zanskar, Ladakh",
                    difficulty="hard",
                    duration_days=8,
                    total_slots=12,
                    available_slots=7,
                    price=24500.0,
                    status="open",
                    itinerary_layout="magazine",
                    start_date=date.today() + timedelta(days=15),
                    end_date=date.today() + timedelta(days=23),
                    description="Traverse the frozen gorge of the Zanskar River on sub-zero ice sheets. An extreme winter expedition under sheer 600m canyon walls, encountering frozen waterfalls and ancient Zanskari cave shelters at -20°C.",
                )
                db.session.add(trek_b)
                db.session.flush()

                # Add Itinerary Days for Chadar
                d1 = TrekItineraryDay(
                    organization_id=org_b.id,
                    trek_id=trek_b.id,
                    day_number=1,
                    title="Leh High-Altitude Ingress (11,500 ft)",
                    description="Acclimatization day in Leh. Mandatory 48-hour medical clearance checkpoint and SpO2 readings.",
                    important_points=[
                        "Mandatory complete rest for first 24 hours to prevent AMS",
                        "SpO2 saturation level must register above 85% at basecamp",
                    ],
                )
                db.session.add(d1)
                db.session.flush()

                e1 = ItineraryElement(
                    organization_id=org_b.id,
                    itinerary_day_id=d1.id,
                    sequence=1,
                    element_type="activity",
                    title="SNM Hospital Altitude Medical Clearance",
                    time_of_day="10:00 AM",
                    description="Comprehensive physical examination and hyperbaric briefing.",
                    points=[
                        "Doctor blood pressure and ECG verification",
                        "Wildlife and ice permit issuance by Zanskar district office",
                    ],
                )
                e2 = ItineraryElement(
                    organization_id=org_b.id,
                    itinerary_day_id=d1.id,
                    sequence=2,
                    element_type="meal",
                    title="High-Calorie Altitude Dinner & Briefing",
                    time_of_day="07:30 PM",
                    description="Hot garlic soup and butter tea to assist acclimatization.",
                    points=["Review ice thickness radar reports with Zanskari lead porters"],
                )
                db.session.add_all([e1, e2])

                d2 = TrekItineraryDay(
                    organization_id=org_b.id,
                    trek_id=trek_b.id,
                    day_number=2,
                    title="Leh to Shingra Koma via Chilling",
                    description="Scenic drive through confluence of Indus and Zanskar, descending onto the frozen river ice sheet.",
                    important_points=[
                        "Gumboots and thermal wool socks required immediately upon stepping onto ice",
                        "Test ice resonance with walking stick before advancing",
                    ],
                )
                db.session.add(d2)
                db.session.flush()

                e3 = ItineraryElement(
                    organization_id=org_b.id,
                    itinerary_day_id=d2.id,
                    sequence=1,
                    element_type="activity",
                    title="Penguin Walk Ice Training",
                    time_of_day="02:00 PM",
                    description="Technical friction stepping and sled pulling drills on slick green ice.",
                    points=[
                        "Center-of-gravity stabilization technique",
                        "Emergency ice fracture extraction protocol",
                    ],
                )
                db.session.add(e3)

                # Add sample review by trekker_b
                user_b = User.query.filter_by(organization_id=org_b.id, email="trekker@b.localhost").first()
                if user_b:
                    booking_b = Booking(
                        organization_id=org_b.id,
                        trek_id=trek_b.id,
                        user_id=user_b.id,
                        slot_count=1,
                        total_price=24500.0,
                        status="completed",
                    )
                    db.session.add(booking_b)
                    db.session.flush()

                    rev_b = TrekReview(
                        organization_id=org_b.id,
                        trek_id=trek_b.id,
                        booking_id=booking_b.id,
                        user_id=user_b.id,
                        rating=5,
                        comment="Unreal experience on the frozen river! The guides checked ice thickness every half mile and the satellite weather reports kept us safe through a cold front.",
                    )
                    db.session.add(rev_b)

                db.session.commit()
                print("Seeded Chadar Expedition for Tenant B.")

print("\nSeeding complete! Both tenants are primed.")
