# WebAssembly Hand Evaluator — Build Guide

## Purpose
If the pure JavaScript Omaha evaluator in `src/main/brain/omaha-evaluator.js`
proves too slow for PLO6 with multiple opponents, this directory holds the
WASM-compiled native evaluator as a drop-in replacement.

## When to Migrate to WASM
The JS evaluator should handle our targets:
- PLO5: 5000 sims × 100 evals/sim × 2 players = 1M evals → ~180ms (4 workers)
- PLO6: 3000 sims × 150 evals/sim × 2 players = 900K evals → ~200ms (4 workers)

Migrate to WASM if:
- Multi-way pots (3+ villains) push beyond 500ms
- Future features require live range equity (all combos, not sampling)

## Build Options

### Option A: Rust (via wasm-pack)
```bash
# Install
curl https://rustwasm.github.io/wasm-pack/installer/init.sh -sSf | sh

# Build
cd wasm/rust-evaluator
wasm-pack build --target web --out-dir ../../src/wasm
```

### Option B: C++ (via Emscripten)
```bash
# Install Emscripten SDK
git clone https://github.com/emscripten-core/emsdk.git
cd emsdk && ./emsdk install latest && ./emsdk activate latest

# Build
cd wasm/cpp-evaluator
emcc omaha_eval.cpp -O3 -s WASM=1 -s EXPORTED_FUNCTIONS='["_evaluate5","_evaluateOmaha"]' \
  -o ../../src/wasm/omaha-eval.js
```

## Integration
The WASM module exposes the same interface as `omaha-evaluator.js`:
```javascript
// Drop-in replacement
const { evaluate5, evaluateOmaha } = await loadWasmEvaluator();
```

The `equity-worker.js` has a detection mechanism:
```javascript
// Auto-detect WASM availability
let evaluator;
try {
  evaluator = require('./omaha-eval.wasm');
} catch {
  evaluator = require('./omaha-evaluator');
}
```

## Benchmark Comparison (Expected)
| Engine     | PLO5 5000 sims | PLO6 3000 sims |
|------------|----------------|----------------|
| JS (V8)    | ~180ms         | ~200ms         |
| WASM (C++) | ~40ms          | ~55ms          |
| WASM (Rust)| ~35ms          | ~50ms          |
