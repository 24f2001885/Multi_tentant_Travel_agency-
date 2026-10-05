# Trekking Management Application (TMA)

A role-based Flask app for running trekking programs end-to-end — trek creation and approval, staff assignment, slot-aware bookings, sandbox payments (Razorpay test mode), trip history, and a small support-ticket system.

Three roles, one app:
- Admin — pre-seeded, no registration path, runs everything
- Trek Staff — self-registers, waits on Admin approval, manages only their assigned treks
- Trekker — self-registers, browses/books/pays, tracks their own history

Stack: Flask 3 · SQLAlchemy 2 (SQLite) · Flask-Login · Flask-Migrate · Flask-Mail · Flask-RESTful · Bootstrap 5 + Chart.js · Docker Compose (`web` + `mailhog` + `test`)

## Before anything else

You need Docker Desktop (or Docker Engine + the Compose on Linux) and Git. 


## First boot

This order matters: build, bring the stack up, sort the database, then seed the admin.

```bash
docker compose build
docker compose up --build -d
```


```bash
dockercompose logs -f web
```

ctrl + C to quit this log



```bash
docker compose run --rm web flask db init
docker compose run --rm web flask db migrate -m "Initial schema"
docker compose run --rm web flask db upgrade
```

`db init` is a one-time thing — if `migrations/env.py` already exists, skip it and go straight to `migrate`/`upgrade`, it'll just complain the folder's already there.

Seed admin account -

```bash
docker compose run --rm web flask seed-admin
```
## Stuff you'll run constantly

restart 
```bash
docker compose restart web
```

watch logs 
```bash
docker compose logs -f web
```

changed `requirements.txt`
```bash
docker compose up --build -d
```
db change 
```bash
docker compose run --rm web flask db migrate -m "describe what changed"
docker compose run --rm web flask db upgrade
```

close 
```bash
docker compose down
```

## Git — one branch per milestone

`main` stays protected. Every phase gets its own branch: `phase/P0-foundation-repo-setup`, `phase/P1-auth-password-reset`, `phase/P2-admin-core`, `phase/P3-staff-trekker-dashboards`, `phase/P4-payments`, `phase/P5-tickets-history`, `phase/P6-enhancements-hardening`.



```bash
git add .
git commit -m "feat(tickets): query/problem ticket creation and response"
git push -u origin phase/P5-tickets-history
```
## Sharing your local demo 

For a viva/demo where someone needs to hit your machine from outside your network, `ngrok` is the quick way:

```bash
ngrok config add-authtoken <your-authtoken-here>   # one-time, from your ngrok dashboard
ngrok http 5000
```


## Running without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
flask db upgrade
flask seed-admin
flask run
```

