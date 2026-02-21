# Titan Distributed Cloud — Monorepo

Enterprise-grade Omaha PLO5/PLO6 poker engine with Edge-to-Cloud architecture.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        TITAN DISTRIBUTED CLOUD                               │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   LOCAL (Edge Client)                    CLOUD (GPU Cluster)                 │
│   ┌────────────────────┐                ┌────────────────────────────────┐   │
│   │  Electron.js       │   gRPC/TLS     │  cloud-gateway (Node.js)      │   │
│   │  ┌──────────────┐  │   ProtoBuf     │  ┌──────────────────────────┐ │   │
│   │  │ TF.js WebGPU │  │ ◄────────────► │  │  gRPC Server (port 50051)│ │   │
│   │  │ YOLOv8l      │  │   < 20ms RTT   │  │  Request Router          │ │   │
│   │  │ < 15ms infer │  │                │  └───────────┬──────────────┘ │   │
│   │  └──────────────┘  │                │              │ N-API FFI      │   │
│   │  ┌──────────────┐  │                │  ┌───────────▼──────────────┐ │   │
│   │  │ ADB Bridge   │  │                │  │  core-engine (Rust)      │ │   │
│   │  │ Ghost Taps   │  │                │  │  ┌──────────────────────┐│ │   │
│   │  │ → LDPlayer   │  │                │  │  │ Deep CFR Neural Net  ││ │   │
│   │  └──────────────┘  │                │  │  │ PLO5: 2.6M combos   ││ │   │
│   │  ┌──────────────┐  │                │  │  │ PLO6: 20M+ combos   ││ │   │
│   │  │ Dashboard UI │  │                │  │  │ Lookup: < 1ms        ││ │   │
│   │  │ Health/Logs  │  │                │  │  └──────────────────────┘│ │   │
│   │  └──────────────┘  │                │  │  ┌──────────────────────┐│ │   │
│   └────────────────────┘                │  │  │ Exploitative Layer   ││ │   │
│                                         │  │  │ Node-Lock Adjustments││ │   │
│                                         │  │  └──────────────────────┘│ │   │
│                                         │  └──────────────────────────┘ │   │
│                                         │  ┌──────────────────────────┐ │   │
│                                         │  │ PostgreSQL + MCP         │ │   │
│                                         │  │ Opponent Profiling       │ │   │
│                                         │  │ LLM Tactical Advisor     │ │   │
│                                         │  └──────────────────────────┘ │   │
│                                         └────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Monorepo Structure

```
titan-distributed/
├── package.json                    # Workspace root (npm workspaces)
├── turbo.json                      # Turborepo pipeline config
├── .github/workflows/              # CI/CD
│
├── proto/                          # ═══ gRPC Contracts (shared source of truth)
│   └── titan/
│       └── v1/
│           └── table_state.proto   # ProtoBuf definitions
│
├── packages/
│   ├── client-electron/            # ═══ Edge Client (JS/TS)
│   │   ├── package.json
│   │   ├── src/
│   │   │   ├── main/               # Electron main process
│   │   │   │   ├── main.js
│   │   │   │   ├── preload.js
│   │   │   │   ├── grpc-client.js  # gRPC stub → cloud
│   │   │   │   └── execution/      # ADB bridge + humanizer
│   │   │   ├── renderer/           # UI + TF.js WebGPU vision
│   │   │   └── shared/             # Constants, IPC channels
│   │   └── models/                 # YOLO web model files
│   │
│   ├── cloud-gateway/              # ═══ Cloud Entry Point (Node.js)
│   │   ├── package.json
│   │   ├── Dockerfile
│   │   ├── src/
│   │   │   ├── server.js           # gRPC server (port 50051)
│   │   │   ├── solver-bridge.js    # N-API binding to Rust engine
│   │   │   ├── exploitative.js     # Node-Lock adjustments
│   │   │   ├── profiling/          # PostgreSQL opponent DB
│   │   │   │   ├── pg-store.js
│   │   │   │   └── mcp-advisor.js
│   │   │   └── generated/          # Auto-gen'd protobuf JS stubs
│   │   └── k8s/                    # Kubernetes manifests
│   │       ├── deployment.yaml
│   │       └── service.yaml
│   │
│   └── core-engine/                # ═══ Math Engine (Rust + N-API)
│       ├── Cargo.toml
│       ├── build.rs
│       ├── src/
│       │   ├── lib.rs              # N-API entry point
│       │   ├── evaluator.rs        # 5-card hand evaluator (bitwise)
│       │   ├── omaha.rs            # C(hand,2)×C(board,3) Omaha rule
│       │   ├── cfr/
│       │   │   ├── mod.rs
│       │   │   ├── deep_cfr.rs     # Deep CFR neural network
│       │   │   ├── abstraction.rs  # Card abstraction (isomorphisms)
│       │   │   └── strategy.rs     # Strategy lookup table
│       │   ├── solver.rs           # Entry: GameState → Decision
│       │   └── exploit.rs          # Exploitative adjustments
│       ├── benches/
│       │   └── solver_bench.rs
│       └── tests/
│           └── omaha_test.rs
│
├── infra/                          # ═══ Infrastructure as Code
│   ├── terraform/                  # AWS/GCP provisioning
│   └── docker-compose.yml          # Local dev stack
│
└── scripts/
    ├── proto-gen.sh                # Generate ProtoBuf stubs
    └── deploy-cloud.sh             # Cloud deployment
```

## Quick Start

```bash
# Install all workspace dependencies
npm install

# Generate protobuf stubs
npm run proto:gen

# Build Rust engine (requires Rust toolchain)
cd packages/core-engine && cargo build --release

# Start cloud gateway locally
npm run dev:cloud

# Start Electron client
npm run dev:client
```

## Performance Targets

| Metric                  | Target     | Notes                        |
|------------------------|------------|------------------------------|
| Vision (YOLO WebGPU)   | < 15ms    | YOLOv8l on RTX 2060 Super   |
| gRPC round-trip         | < 20ms    | Same-region AWS              |
| Solver lookup (Rust)    | < 1ms     | Pre-computed Deep CFR table  |
| Total decision latency  | < 50ms    | Vision + network + solver    |
| ADB tap execution       | < 30ms    | Direct kernel input          |
| **End-to-end**          | **< 100ms** | Perception → Action        |
