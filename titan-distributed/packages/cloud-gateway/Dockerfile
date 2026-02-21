# ── Stage 1: Rust Builder ────────────────────────────────────────────
FROM rust:1.82-bookworm AS rust-builder

WORKDIR /build

# Cache Rust dependencies
COPY packages/core-engine/Cargo.toml packages/core-engine/Cargo.lock* ./
RUN mkdir src && echo "fn main() {}" > src/lib.rs && \
  cargo build --release 2>/dev/null || true && \
  rm -rf src

# Build the actual engine
COPY packages/core-engine/ ./
RUN cargo build --release && \
  cp target/release/libtitan_core_engine.so titan_core.node 2>/dev/null || \
  cp target/release/titan_core_engine.dll titan_core.node 2>/dev/null || \
  cp target/release/libtitan_core_engine.dylib titan_core.node 2>/dev/null || true


# ── Stage 2: Node.js Gateway ────────────────────────────────────────
FROM node:22-bookworm-slim AS gateway

WORKDIR /app

# Install only production dependencies
COPY packages/cloud-gateway/package.json packages/cloud-gateway/package-lock.json* ./
RUN npm ci --production 2>/dev/null || npm install --production

# Copy gateway source
COPY packages/cloud-gateway/src/ ./src/

# Copy proto definitions
COPY proto/ /app/proto/

# Copy Rust native addon from builder stage
COPY --from=rust-builder /build/titan_core.node /app/native/titan_core.node

# Environment
ENV NODE_ENV=production
ENV TITAN_PORT=50051
ENV TITAN_HOST=0.0.0.0
ENV LOG_LEVEL=info

# Health check
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD node -e "const g=require('@grpc/grpc-js');const c=new (require('@grpc/proto-loader'));process.exit(0)"

EXPOSE 50051

CMD ["node", "src/server.js"]
