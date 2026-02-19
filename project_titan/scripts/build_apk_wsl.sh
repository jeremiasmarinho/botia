#!/usr/bin/env bash
set -euo pipefail

DISTRO="${1:-Ubuntu-24.04}"
PROJECT_WIN_PATH="F:\\botia\\project_titan"

echo "[1/4] Entering WSL distro: ${DISTRO}"

# Convert Windows path to WSL path
PROJECT_WSL_PATH="/mnt/f/botia/project_titan"
MOBILE_PATH="${PROJECT_WSL_PATH}/mobile"

if [ ! -d "${MOBILE_PATH}" ]; then
  echo "Mobile directory not found: ${MOBILE_PATH}"
  exit 1
fi

echo "[2/4] Installing local Python build deps"
python3 -m pip install --upgrade pip
python3 -m pip install buildozer cython

echo "[3/4] Building debug APK"
cd "${MOBILE_PATH}"
buildozer android debug

echo "[4/4] Done. APK should be under ${MOBILE_PATH}/bin/"
