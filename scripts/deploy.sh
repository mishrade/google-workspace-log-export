#!/usr/bin/env bash
#
# Build the Lambda deployment package and optionally deploy via Terraform.
#
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${PROJECT_ROOT}/src"
BUILD_DIR="${PROJECT_ROOT}/.build"
OUTPUT_ZIP="${PROJECT_ROOT}/lambda_package.zip"

echo "==> Cleaning previous build artifacts..."
rm -rf "${BUILD_DIR}" "${OUTPUT_ZIP}"
mkdir -p "${BUILD_DIR}"

echo "==> Installing Python dependencies..."
pip install \
  --target "${BUILD_DIR}" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.11 \
  --only-binary=:all: \
  -r "${PROJECT_ROOT}/requirements.txt" \
  --quiet

echo "==> Copying source code..."
cp "${SRC_DIR}"/*.py "${BUILD_DIR}/"

echo "==> Creating deployment package..."
cd "${BUILD_DIR}"
zip -r9 "${OUTPUT_ZIP}" . -x '*.pyc' '__pycache__/*' > /dev/null

cd "${PROJECT_ROOT}"
rm -rf "${BUILD_DIR}"

ZIP_SIZE=$(du -h "${OUTPUT_ZIP}" | cut -f1)
echo "==> Lambda package created: ${OUTPUT_ZIP} (${ZIP_SIZE})"

# Optionally run terraform apply
if [[ "${1:-}" == "--apply" ]]; then
  echo "==> Running terraform apply..."
  cd "${PROJECT_ROOT}/terraform"
  terraform init -input=false
  terraform apply -auto-approve
fi

echo "==> Done."
