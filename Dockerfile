# Dockerfile
#
# Single image, shared by the "web" and "test" services (TDR §9).
# The test suite runs against the exact same image/dependency set the app 
# would ship with, so a Docker test pass here is actually predictive of a real deploy.

FROM python:3.11-slim

WORKDIR /app

# System packages: build-essential covers any C-extension wheels without a
# prebuilt slim-image wheel; sqlite3 CLI is handy for debugging inside the
# container (the app itself talks to SQLite only through SQLAlchemy).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Application dependencies (TDR §14)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Testing-only dependencies (pytest, pytest-cov) — separate manifest so a
# future slimmed-down production image can skip this layer if desired.
COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt

# Copy the rest of the application code
COPY . .

# Ensure the instance directory exists for the SQLite database
RUN mkdir -p instance

EXPOSE 5000

CMD ["flask", "run", "--host=0.0.0.0"]