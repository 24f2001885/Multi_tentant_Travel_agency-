#!/usr/bin/env bash
# test_local.sh
# Local test runner with pre-flight dependency verification (milestone-tester skill).

set -eo pipefail

echo "====================================================="
echo "TMA Platform: Local Test Suite Runner (Milestone P7)"
echo "====================================================="

# 1. Dependency checks
MISSING_DEPS=0

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
    echo "ERROR: python3 runtime is missing. Install Python 3.11+."
    MISSING_DEPS=1
fi

if ! command -v pytest >/dev/null 2>&1; then
    echo "WARNING: pytest is not in the current PATH. Ensure dependencies from requirements.txt are installed."
    echo "Remediation: pip install -r requirements.txt"
fi

if [ "$MISSING_DEPS" -eq 1 ]; then
    echo "Aborting due to missing required dependencies."
    exit 1
fi

# 2. Environment checks & default test env vars
export FLASK_ENV="${FLASK_ENV:-testing}"
export HASHIDS_SALT="${HASHIDS_SALT:-local-dev-test-hashids-salt-p7}"
export SECRET_KEY="${SECRET_KEY:-local-dev-test-secret-key-p7}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///:memory:}"

echo "Running pytest with: ${DATABASE_URL}"

# 3. Run test suite
pytest -v "$@"
EXIT_CODE=$?

# 4. Summary
echo "-----------------------------------------------------"
if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS: All local tests passed!"
else
    echo "FAILURE: Tests failed with exit code $EXIT_CODE."
fi
echo "====================================================="

exit $EXIT_CODE
