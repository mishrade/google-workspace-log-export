#!/usr/bin/env bash
#
# Backfill historical logs by invoking the Lambda with a custom LOOKBACK_DAYS.
# Usage: ./backfill.sh <days>
#   e.g. ./backfill.sh 180   — fetch the last 180 days of logs
#
# Note: Google Workspace retains audit logs for ~180 days.
# Running this once at initial setup captures all available history.
#
set -euo pipefail

DAYS="${1:?Usage: $0 <number-of-days-to-backfill>}"
FUNCTION_NAME="google-workspace-audit-log-exporter"

echo "==> Backfilling ${DAYS} days of Google Workspace audit logs..."
echo "    This invokes the Lambda once per day to respect API rate limits."
echo ""

for (( i=DAYS; i>=1; i-- )); do
  echo -n "  Day -${i}: "

  OUTPUT=$(aws lambda invoke \
    --function-name "${FUNCTION_NAME}" \
    --payload "{\"override_lookback_days\": ${i}}" \
    --cli-binary-format raw-in-base64-out \
    /dev/stdout 2>/dev/null)

  TOTAL=$(echo "${OUTPUT}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_events',0))" 2>/dev/null || echo "?")
  echo "${TOTAL} events"

  # Small delay to avoid API rate limits
  sleep 2
done

echo ""
echo "==> Backfill complete."
