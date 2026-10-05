#!/usr/bin/env bash
# run_docker_tests.sh
# Docker isolated test runner (milestone-tester skill).
# Executes pytest in the Postgres-backed test container.

set -eo pipefail

echo "====================================================="
echo "TMA Platform: Docker Test Suite Runner (Milestone P7)"
echo "====================================================="

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker command not found. Ensure Docker is installed and running."
    exit 1
fi

echo "Spinning up db_test and executing container test runner..."
docker compose run --rm test "$@"
EXIT_CODE=$?

echo "-----------------------------------------------------"
if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS: Docker test suite passed cleanly!"
else
    echo "FAILURE: Docker test suite failed with exit code $EXIT_CODE."
fi
echo "====================================================="

exit $EXIT_CODE
