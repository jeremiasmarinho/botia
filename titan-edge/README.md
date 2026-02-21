# Titan Edge AI — Omaha PLO5/PLO6

## Arquitectura

```
titan-edge/
├── package.json
├── electron-builder.yml
├── README.md
│
├── src/
│   ├── main/                          # Electron Main Process
│   │   ├── main.js                    # Entry point — BrowserWindow + IPC
│   │   ├── preload.js                 # Context bridge (main ↔ renderer)
│   │   │
│   │   ├── brain/                     # Decision Engine (PLO5/PLO6)
│   │   │   ├── equity-worker.js       # Worker Thread — Monte Carlo
│   │   │   ├── equity-pool.js         # Worker pool manager (N threads)
│   │   │   ├── omaha-evaluator.js     # Hand evaluator (2-from-hand rule)
│   │   │   ├── gto-engine.js          # Mixed-strategy GTO
│   │   │   └── __tests__/
│   │   │       └── equity-worker.test.js
│   │   │
│   │   ├── execution/                 # ADB Action Layer
│   │   │   ├── adb-bridge.js          # ADB connection + ghost taps
│   │   │   ├── action-mapper.js       # Game state → ADB coordinates
│   │   │   ├── humanizer.js           # Timing randomization
│   │   │   └── __tests__/
│   │   │       └── adb-bridge.test.js
│   │   │
│   │   ├── profiling/                 # Metagame — Opponent DB
│   │   │   ├── opponent-db.js         # SQLite CRUD
│   │   │   ├── stats-engine.js        # VPIP/PFR/3Bet/AF calculations
│   │   │   └── mcp-advisor.js         # MCP bridge to LLM tactical advisor
│   │   │
│   │   └── orchestrator/              # Tick loop + system health
│   │       ├── engine.js              # Main tick loop
│   │       ├── state-machine.js       # Game state FSM
│   │       └── health-monitor.js      # Latency + memory watchdog
│   │
│   ├── renderer/                      # Electron Renderer Process
│   │   ├── index.html                 # Main UI shell
│   │   ├── renderer.js                # UI logic + IPC calls
│   │   ├── styles.css                 # Dashboard styles
│   │   │
│   │   └── vision/                    # Perception — TF.js + WebGPU
│   │       ├── yolo-inference.js      # YOLO model loading + inference
│   │       ├── card-detector.js       # Post-processing: NMS + card identification
│   │       ├── screen-capture.js      # desktopCapturer → tensor pipeline
│   │       └── __tests__/
│   │           └── yolo-inference.test.js
│   │
│   ├── shared/                        # Shared constants & types
│   │   ├── constants.js               # Card codes, button names, timing
│   │   ├── ipc-channels.js            # IPC channel name registry
│   │   └── config.js                  # YAML config loader
│   │
│   └── wasm/                          # (Future) C++/Rust → WebAssembly
│       ├── README.md                  # Build instructions for WASM evaluator
│       └── omaha-eval.wasm            # Compiled hand evaluator (placeholder)
│
├── models/                            # Exported AI models
│   ├── yolo-web/                      # TF.js web format (from Colab export)
│   │   ├── model.json
│   │   └── group1-shard*.bin
│   └── README.md
│
├── db/                                # SQLite databases
│   └── opponents.db                   # Created at runtime
│
└── config/
    ├── titan.yaml                     # Main configuration
    └── regions.yaml                   # Screen region calibration
```

## Arquitectura dos 4 Pilares

### 1. Visão (Renderer Process — WebGPU)
TensorFlow.js com backend WebGPU roda no renderer process do Electron,
aproveitando a GPU RTX 2060 Super via Dawn/WebGPU. O `desktopCapturer`
captura frames do LDPlayer e alimenta o YOLO para detecção de 5-6 cartas
sobrepostas com NMS agressivo (IoU threshold baixo ~0.3).

### 2. Cérebro (Main Process — Worker Threads)
Monte Carlo equity roda em Worker Threads isoladas para não bloquear
a main thread. Pool de 4-8 workers distribui as simulações. O avaliador
Omaha **obrigatoriamente** usa C(hand,2) × C(board,3) = regra Omaha.

### 3. Ação (Main Process — ADB child_process)
Comandos `adb shell input tap X Y` com timing humanizado (Poisson + jitter).
Zero dependência de mouse Windows — cliques vão direto ao kernel Android.

### 4. Metajogo (Main Process — SQLite + MCP)
better-sqlite3 para I/O síncrono rápido. MCP server expõe o DB para
um LLM conselheiro tático consultar perfis de oponentes em tempo real.
