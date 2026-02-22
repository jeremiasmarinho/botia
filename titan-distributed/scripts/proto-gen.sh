#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Proto Generation Script — generates JS/TS stubs from .proto
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

PROTO_DIR="proto/titan/v1"
PROTO_FILE="${PROTO_DIR}/table_state.proto"

# ── Client (Electron) stubs ────────────────────────────────────
CLIENT_OUT="packages/client-electron/src/generated"
mkdir -p "${CLIENT_OUT}"

npx grpc_tools_node_protoc \
  --js_out=import_style=commonjs,binary:"${CLIENT_OUT}" \
  --grpc_out=grpc_js:"${CLIENT_OUT}" \
  --proto_path=proto \
  "titan/v1/table_state.proto"

echo "✓ Client stubs → ${CLIENT_OUT}"

# ── Cloud Gateway stubs ───────────────────────────────────────
CLOUD_OUT="packages/cloud-gateway/src/generated"
mkdir -p "${CLOUD_OUT}"

npx grpc_tools_node_protoc \
  --js_out=import_style=commonjs,binary:"${CLOUD_OUT}" \
  --grpc_out=grpc_js:"${CLOUD_OUT}" \
  --proto_path=proto \
  "titan/v1/table_state.proto"

echo "✓ Cloud stubs  → ${CLOUD_OUT}"
echo "✓ Proto generation complete."
