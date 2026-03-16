#!/usr/bin/env bash
#
# Builds a Lambda Layer zip with Google API dependencies.
# Output: lambda_layer.zip (upload this as a Lambda Layer)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="${PROJECT_DIR}/build/layer"
OUTPUT="${PROJECT_DIR}/lambda_layer.zip"

echo "==> Cleaning previous build..."
rm -rf "${BUILD_DIR}" "${OUTPUT}"
mkdir -p "${BUILD_DIR}/python"

echo "==> Installing dependencies..."
pip3 install \
  --target "${BUILD_DIR}/python" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.11 \
  --only-binary=:all: \
  -r "${PROJECT_DIR}/requirements-layer.txt" \
  --quiet

echo "==> Packaging layer..."
cd "${BUILD_DIR}"
zip -r9 "${OUTPUT}" python/ -x "*.pyc" "__pycache__/*" "*.dist-info/*" > /dev/null

SIZE=$(du -h "${OUTPUT}" | cut -f1)
echo "==> Done: ${OUTPUT} (${SIZE})"
echo ""
echo "Next steps:"
echo "  1. Go to Lambda → Layers → Create layer"
echo "  2. Name: google-workspace-audit-deps"
echo "  3. Upload: lambda_layer.zip"
echo "  4. Compatible runtimes: Python 3.11"
