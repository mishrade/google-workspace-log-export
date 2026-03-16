#!/usr/bin/env bash
#
# Packages the Lambda code into a zip for deployment.
# Output: lambda_package.zip (upload to your Lambda function)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="${PROJECT_DIR}/src"
OUTPUT="${PROJECT_DIR}/lambda_package.zip"

echo "==> Packaging Lambda code..."
rm -f "${OUTPUT}"

cd "${SRC_DIR}"
zip -j "${OUTPUT}" lambda_function.py > /dev/null

SIZE=$(du -h "${OUTPUT}" | cut -f1)
echo "==> Done: ${OUTPUT} (${SIZE})"
echo ""
echo "Lambda handler setting: lambda_function.handler"
echo ""
echo "Next steps:"
echo "  1. Go to your Lambda function → Code tab"
echo "  2. Upload from → .zip file → select lambda_package.zip"
echo "  3. Verify Handler is set to: lambda_function.handler"
