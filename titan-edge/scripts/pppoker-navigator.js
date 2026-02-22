#!/usr/bin/env node
/**
 * PPPoker Auto-Navigator — ADB-based screen navigation
 *
 * PPPoker is a Unity app: the entire UI is a single GL surface with no
 * Android view hierarchy.  This script uses ADB screenshots + pixel
 * color sampling to detect which screen is showing, then taps the
 * right coordinates to navigate through:
 *
 *   Splash → Login → Lobby → Club → Table
 *
 * Usage:
 *   node scripts/pppoker-navigator.js [--club <id>] [--loops <n>]
 *
 * The script runs in a loop, taking screenshots every 2-3 seconds and
 * tapping through the navigation flow.  It exits once a poker table
 * is detected (green felt + cards area).
 */

"use strict";

const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const { Buffer } = require("node:buffer");
const path = require("node:path");
const fs = require("node:fs");

const execFileAsync = promisify(execFile);

// ── Configuration ───────────────────────────────────────────────────

const ADB_PATH = "F:\\LDPlayer\\LDPlayer9\\adb.exe";
const DEVICE = "emulator-5554";
const SCREENSHOT_INTERVAL_MS = 3000;
const MAX_LOOPS = 60; // 60 × 3s = 3 minutes max
const SCREEN_WIDTH = 1080;
const SCREEN_HEIGHT = 1920;

// ── PPPoker Screen Coordinate Maps (1080×1920 portrait) ─────────────
// These are approximate tap targets for common PPPoker UI elements.
// PPPoker uses Unity UI — positions are consistent across devices
// at the same resolution.

const SCREENS = {
  // Splash / loading screen — tap center to dismiss
  SPLASH: {
    name: "SPLASH",
    tap: { x: 540, y: 960 },
    description: "Loading/splash screen — tap to dismiss",
  },

  // "Guest Login" or main login options screen
  LOGIN_GUEST: {
    name: "LOGIN_GUEST",
    // "Play as Guest" button is typically at bottom-center
    tap: { x: 540, y: 1400 },
    description: "Guest login button",
  },

  // Login via device/account — "Login" button
  LOGIN_BUTTON: {
    name: "LOGIN_BUTTON",
    tap: { x: 540, y: 1200 },
    description: "Main login button",
  },

  // Agreement / Terms popup — "Agree" button
  AGREEMENT: {
    name: "AGREEMENT",
    tap: { x: 540, y: 1300 },
    description: "Terms agreement — tap Agree",
  },

  // Main lobby — "Clubs" tab at bottom
  LOBBY_CLUBS: {
    name: "LOBBY_CLUBS",
    // Club icon in bottom navigation bar (usually 2nd from left)
    tap: { x: 270, y: 1850 },
    description: "Clubs tab in lobby bottom bar",
  },

  // Club list — tap first club
  CLUB_FIRST: {
    name: "CLUB_FIRST",
    tap: { x: 540, y: 600 },
    description: "First club in list",
  },

  // Inside club — tap first available table
  TABLE_FIRST: {
    name: "TABLE_FIRST",
    tap: { x: 540, y: 500 },
    description: "First table in club",
  },

  // "Join" / "Sit" button at table
  JOIN_TABLE: {
    name: "JOIN_TABLE",
    tap: { x: 540, y: 1400 },
    description: "Join/Sit at table button",
  },

  // Buy-in dialog — confirm
  BUYIN_CONFIRM: {
    name: "BUYIN_CONFIRM",
    tap: { x: 540, y: 1100 },
    description: "Confirm buy-in amount",
  },

  // Popup dismiss (X button, usually top-right)
  DISMISS_POPUP: {
    name: "DISMISS_POPUP",
    tap: { x: 980, y: 300 },
    description: "Close popup (X button)",
  },

  // "OK" button for various dialogs
  OK_BUTTON: {
    name: "OK_BUTTON",
    tap: { x: 540, y: 1100 },
    description: "OK/Confirm dialog button",
  },
};

// ── PNG Parser (minimal — extract pixel data from ADB screencap) ────

/**
 * Parse a raw PNG buffer to get pixel data.
 * ADB screencap -p returns a PNG file.
 * We use a minimal PNG decoder to avoid npm dependencies.
 *
 * For simplicity, we'll sample specific pixel coordinates by
 * using ADB's built-in tools.
 */

// ── ADB Helpers ─────────────────────────────────────────────────────

async function adbExec(args, opts = {}) {
  try {
    const result = await execFileAsync(ADB_PATH, args, {
      timeout: 10_000,
      maxBuffer: 15 * 1024 * 1024,
      ...opts,
    });
    return result;
  } catch (err) {
    if (err.killed) throw new Error(`ADB command timed out: ${args.join(" ")}`);
    throw err;
  }
}

async function adbShell(cmd) {
  const result = await adbExec(["-s", DEVICE, "shell", cmd]);
  return result.stdout.trim();
}

async function tap(x, y) {
  const ix = Math.round(x);
  const iy = Math.round(y);
  await adbShell(`input touchscreen tap ${ix} ${iy}`);
  console.log(`  [TAP] (${ix}, ${iy})`);
  return { x: ix, y: iy };
}

async function swipe(x1, y1, x2, y2, durationMs = 300) {
  await adbShell(
    `input touchscreen swipe ${Math.round(x1)} ${Math.round(y1)} ${Math.round(x2)} ${Math.round(y2)} ${durationMs}`,
  );
  console.log(
    `  [SWIPE] (${Math.round(x1)},${Math.round(y1)}) → (${Math.round(x2)},${Math.round(y2)}) ${durationMs}ms`,
  );
}

async function typeText(text) {
  // Escape special chars for ADB
  const escaped = text.replace(/([" \\&|<>^])/g, "\\$1");
  await adbShell(`input text "${escaped}"`);
  console.log(`  [TEXT] "${text}"`);
}

async function keyEvent(code) {
  await adbShell(`input keyevent ${code}`);
  console.log(`  [KEY] ${code}`);
}

async function screenshot() {
  const result = await adbExec(["-s", DEVICE, "exec-out", "screencap", "-p"], {
    encoding: "buffer",
    maxBuffer: 15 * 1024 * 1024,
  });
  return result.stdout;
}

async function getFocusedApp() {
  const result = await adbShell("dumpsys window windows | grep mCurrentFocus");
  return result;
}

/**
 * Sample pixel colors at specific coordinates using ADB screencap.
 * Returns an array of {x, y, r, g, b} objects.
 *
 * Uses `screencap` raw format: 4 bytes header (w, h, format) then RGBA pixels.
 */
async function samplePixels(coords) {
  // Get raw RGBA screencap (faster than PNG for pixel sampling)
  const result = await adbExec(["-s", DEVICE, "exec-out", "screencap"], {
    encoding: "buffer",
    maxBuffer: 15 * 1024 * 1024,
  });

  const buf = result.stdout;
  // First 12 bytes: width(4) + height(4) + format(4)
  const width = buf.readUInt32LE(0);
  const height = buf.readUInt32LE(4);
  // const format = buf.readUInt32LE(8);
  const headerSize = 12;

  const samples = [];
  for (const { x, y } of coords) {
    if (x < 0 || x >= width || y < 0 || y >= height) {
      samples.push({ x, y, r: 0, g: 0, b: 0, a: 0 });
      continue;
    }
    const offset = headerSize + (y * width + x) * 4;
    if (offset + 3 >= buf.length) {
      samples.push({ x, y, r: 0, g: 0, b: 0, a: 0 });
      continue;
    }
    samples.push({
      x,
      y,
      r: buf[offset],
      g: buf[offset + 1],
      b: buf[offset + 2],
      a: buf[offset + 3],
    });
  }

  return { width, height, samples };
}

// ── Screen Detection ────────────────────────────────────────────────

/**
 * Detect which PPPoker screen is currently showing by sampling
 * strategic pixel positions and checking color patterns.
 *
 * PPPoker screen identifiers:
 *   - LOADING:   Black/dark screen with logo
 *   - LOGIN:     Login options (guest, facebook, etc.)
 *   - LOBBY:     Main lobby with bottom nav bar
 *   - CLUB_LIST: List of clubs
 *   - CLUB_ROOM: Inside a club, showing tables
 *   - TABLE:     Active poker table (green felt)
 *   - POPUP:     Dialog/popup overlay
 *   - UNKNOWN:   Can't determine
 */
async function detectScreen() {
  // Sample key diagnostic pixels across the screen
  const diagnosticPoints = [
    // Top bar area
    { x: 540, y: 50 }, // Top center
    { x: 100, y: 50 }, // Top left
    { x: 980, y: 50 }, // Top right
    // Center area
    { x: 540, y: 500 }, // Upper center
    { x: 540, y: 960 }, // Dead center
    { x: 540, y: 1400 }, // Lower center
    // Bottom nav bar area
    { x: 135, y: 1860 }, // Bottom nav icon 1
    { x: 405, y: 1860 }, // Bottom nav icon 2
    { x: 675, y: 1860 }, // Bottom nav icon 3
    { x: 945, y: 1860 }, // Bottom nav icon 4
    // Table felt detection (if at poker table)
    { x: 540, y: 700 }, // Table center area
    { x: 300, y: 700 }, // Table left
    { x: 780, y: 700 }, // Table right
    // Card area (hero cards at bottom)
    { x: 400, y: 1500 }, // Left hero card area
    { x: 600, y: 1500 }, // Right hero card area
  ];

  const { width, height, samples } = await samplePixels(diagnosticPoints);

  // Analyze the samples
  const [
    topCenter,
    topLeft,
    topRight,
    upperCenter,
    center,
    lowerCenter,
    nav1,
    nav2,
    nav3,
    nav4,
    feltCenter,
    feltLeft,
    feltRight,
    heroL,
    heroR,
  ] = samples;

  // Helper: check if a color is "dark" (loading/splash)
  const isDark = (s) => s.r < 40 && s.g < 40 && s.b < 40;
  const isGreenish = (s) => s.g > s.r + 20 && s.g > s.b + 20 && s.g > 60;
  const isDarkGreen = (s) =>
    s.g > s.r && s.g > s.b && s.g > 30 && s.g < 120 && s.r < 80;
  const isNavBar = (s) =>
    Math.abs(s.r - s.g) < 30 &&
    Math.abs(s.g - s.b) < 30 &&
    s.r > 30 &&
    s.r < 100;
  const isWhitish = (s) => s.r > 200 && s.g > 200 && s.b > 200;
  const isReddish = (s) => s.r > 150 && s.g < 80 && s.b < 80;
  const isBlueish = (s) => s.b > 120 && s.r < 100 && s.g < 100;
  const isYellowish = (s) => s.r > 180 && s.g > 150 && s.b < 80;

  // Build a compact color summary for logging
  const px = (s) => `(${s.r},${s.g},${s.b})`;
  console.log(
    `  Pixels: top=${px(topCenter)} center=${px(center)} lower=${px(lowerCenter)} ` +
      `felt=${px(feltCenter)} nav=${px(nav1)},${px(nav2)},${px(nav3)},${px(nav4)}`,
  );

  // ── Decision tree ──

  // 1. Loading/splash: mostly black
  const allDark =
    isDark(topCenter) &&
    isDark(center) &&
    isDark(lowerCenter) &&
    isDark(feltCenter);
  if (allDark) {
    return { screen: "LOADING", confidence: 0.9 };
  }

  // 2. Poker table: green felt in center area
  const feltGreen =
    (isGreenish(feltCenter) || isDarkGreen(feltCenter)) &&
    (isGreenish(feltLeft) || isDarkGreen(feltLeft)) &&
    (isGreenish(feltRight) || isDarkGreen(feltRight));
  if (feltGreen) {
    return { screen: "TABLE", confidence: 0.95 };
  }

  // 3. Lobby: has a bottom navigation bar (dark grey/uniform strip at bottom)
  const hasNavBar =
    (isNavBar(nav1) || isDark(nav1)) &&
    (isNavBar(nav2) || isDark(nav2)) &&
    (isNavBar(nav3) || isDark(nav3)) &&
    (isNavBar(nav4) || isDark(nav4));

  if (hasNavBar) {
    // Distinguish between lobby sub-screens
    // If the center is dark/themed, it's the main lobby
    return { screen: "LOBBY", confidence: 0.7 };
  }

  // 4. Dialog/popup: usually has a lighter center box over a dimmed background
  const centerBright =
    (isWhitish(center) || center.r + center.g + center.b > 500) &&
    isDark(topLeft) &&
    isDark(topRight);
  if (centerBright) {
    return { screen: "POPUP", confidence: 0.7 };
  }

  // 5. Login screen: typically has colored buttons in lower half
  const hasColoredButtons =
    (isReddish(lowerCenter) ||
      isBlueish(lowerCenter) ||
      isYellowish(lowerCenter) ||
      isWhitish(lowerCenter)) &&
    !hasNavBar;
  if (hasColoredButtons) {
    return { screen: "LOGIN", confidence: 0.6 };
  }

  return { screen: "UNKNOWN", confidence: 0.3 };
}

// ── Navigation State Machine ────────────────────────────────────────

/**
 * Navigate through PPPoker screens to reach a poker table.
 *
 * Strategy: Detect current screen → perform appropriate action → repeat.
 * If stuck on the same screen for too long, try alternative taps.
 */
async function navigate() {
  console.log("═══════════════════════════════════════════════════════════");
  console.log("  PPPoker Auto-Navigator v1.0");
  console.log("  ADB device: " + DEVICE);
  console.log("═══════════════════════════════════════════════════════════\n");

  // Verify ADB connection
  const focus = await getFocusedApp();
  console.log(`Current focus: ${focus}\n`);

  if (!focus.includes("pppoker")) {
    console.log("[!] PPPoker is not in foreground. Launching...");
    await adbShell(
      "am start -n com.lein.pppoker.android/com.lein.pppoker.ppsdk.app.UnityMainActivity",
    );
    await sleep(5000);
  }

  let lastScreen = "";
  let sameScreenCount = 0;
  let tableReached = false;
  let step = 0;

  for (let i = 0; i < MAX_LOOPS; i++) {
    step++;
    console.log(`\n── Step ${step} ──────────────────────────────────────`);

    const { screen, confidence } = await detectScreen();
    console.log(
      `  Screen: ${screen} (confidence: ${(confidence * 100).toFixed(0)}%)`,
    );

    // Track if we're stuck
    if (screen === lastScreen) {
      sameScreenCount++;
    } else {
      sameScreenCount = 0;
    }
    lastScreen = screen;

    // If stuck on same screen for 5+ cycles, try aggressive dismissal
    if (sameScreenCount >= 5) {
      console.log("  [!] Stuck — trying aggressive navigation...");
      // Try: back key, tap various areas, dismiss popups
      if (sameScreenCount % 3 === 0) {
        await keyEvent(4); // BACK
      } else if (sameScreenCount % 3 === 1) {
        await tap(980, 200); // Top-right X button
      } else {
        await tap(540, 960); // Center tap
      }
      await sleep(2000);
      continue;
    }

    // ── Act based on detected screen ──

    switch (screen) {
      case "LOADING":
        console.log(
          "  → Waiting for loading to finish / tapping to dismiss...",
        );
        await tap(540, 960); // Tap center to dismiss splash
        await sleep(3000);
        break;

      case "LOGIN":
        console.log("  → Login screen detected — looking for login buttons...");
        // PPPoker login flow (1080×1920 portrait):
        // - "Guest" / "Play" button is usually bottom-center area
        // Try tapping various common login button positions
        if (sameScreenCount === 0) {
          // First attempt: "Guest Login" / main button
          await tap(540, 1400);
        } else if (sameScreenCount === 1) {
          // Try slightly different Y
          await tap(540, 1300);
        } else if (sameScreenCount === 2) {
          // Try "Login with Device" — usually higher
          await tap(540, 1200);
        } else {
          // Try bottom area buttons
          await tap(540, 1500);
        }
        await sleep(4000);
        break;

      case "LOBBY":
        console.log("  → Lobby detected — navigating to Clubs tab...");
        // Bottom nav bar icons (1080 width, ~5 icons spaced evenly)
        // Icon positions: ~108, 324, 540, 756, 972 (for 5 icons)
        // Clubs is usually the 2nd or 3rd icon
        if (sameScreenCount === 0) {
          // Try "Clubs" — usually 2nd icon from left
          await tap(324, 1860);
        } else if (sameScreenCount === 1) {
          // Try 3rd icon
          await tap(540, 1860);
        } else if (sameScreenCount === 2) {
          // Try 1st icon (sometimes Clubs is first)
          await tap(108, 1860);
        } else {
          // Try tapping center of screen (might be a club list already)
          await tap(540, 600);
        }
        await sleep(2500);
        break;

      case "POPUP":
        console.log("  → Popup detected — dismissing...");
        // Try: OK button (center-bottom), X button (top-right), confirm
        if (sameScreenCount === 0) {
          await tap(540, 1100); // OK button
        } else if (sameScreenCount === 1) {
          await tap(980, 300); // X close button
        } else if (sameScreenCount === 2) {
          await tap(540, 1200); // Confirm
        } else {
          await keyEvent(4); // Android BACK
        }
        await sleep(2000);
        break;

      case "TABLE":
        console.log("  ✓ POKER TABLE DETECTED!");
        console.log("  The bot should now detect cards and buttons via YOLO.");
        tableReached = true;
        break;

      case "UNKNOWN":
        console.log("  → Unknown screen — trying exploratory taps...");
        // Cycle through common actions
        const actions = [
          () => tap(540, 960), // Center
          () => tap(540, 1400), // Lower center (buttons)
          () => tap(540, 1860), // Bottom nav
          () => tap(980, 200), // Top-right close
          () => keyEvent(4), // BACK key
          () => tap(540, 600), // Upper center (list items)
        ];
        await actions[sameScreenCount % actions.length]();
        await sleep(2500);
        break;
    }

    if (tableReached) break;
  }

  if (!tableReached) {
    console.log("\n[!] Could not reach a poker table within the time limit.");
    console.log("    Please navigate manually in LDPlayer and run again.");
  }

  console.log("\n═══════════════════════════════════════════════════════════");
  console.log(
    tableReached
      ? "  ✓ Navigation complete — bot should activate"
      : "  ✗ Navigation incomplete",
  );
  console.log("═══════════════════════════════════════════════════════════");

  return tableReached;
}

// ── Interactive Mode ────────────────────────────────────────────────

/**
 * Interactive tap mode — lets you send taps by coordinates.
 * Useful for manual exploration of PPPoker screens.
 *
 * Usage: node scripts/pppoker-navigator.js --interactive
 */
async function interactiveMode() {
  const readline = require("node:readline");
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  console.log("═══════════════════════════════════════════════════════════");
  console.log("  PPPoker Interactive Tap Mode");
  console.log("  Commands:");
  console.log("    tap <x> <y>     — Send tap at coordinates");
  console.log("    swipe <x1> <y1> <x2> <y2> [ms]");
  console.log("    text <string>   — Type text");
  console.log("    key <code>      — Send key event (4=BACK, 3=HOME)");
  console.log("    screen          — Detect current screen");
  console.log("    shot            — Save screenshot to reports/");
  console.log("    pixels          — Sample diagnostic pixels");
  console.log("    auto            — Run auto-navigator");
  console.log("    quit            — Exit");
  console.log("═══════════════════════════════════════════════════════════\n");

  const prompt = () => {
    rl.question("pppoker> ", async (line) => {
      const parts = line.trim().split(/\s+/);
      const cmd = parts[0]?.toLowerCase();

      try {
        switch (cmd) {
          case "tap":
          case "t":
            if (parts.length >= 3) {
              await tap(parseInt(parts[1]), parseInt(parts[2]));
            } else {
              console.log("Usage: tap <x> <y>");
            }
            break;

          case "swipe":
          case "sw":
            if (parts.length >= 5) {
              await swipe(
                parseInt(parts[1]),
                parseInt(parts[2]),
                parseInt(parts[3]),
                parseInt(parts[4]),
                parseInt(parts[5] || 300),
              );
            } else {
              console.log("Usage: swipe <x1> <y1> <x2> <y2> [duration_ms]");
            }
            break;

          case "text":
            if (parts.length >= 2) {
              await typeText(parts.slice(1).join(" "));
            } else {
              console.log("Usage: text <string>");
            }
            break;

          case "key":
          case "k":
            if (parts.length >= 2) {
              await keyEvent(parseInt(parts[1]));
            } else {
              console.log(
                "Usage: key <code>  (4=BACK, 3=HOME, 66=ENTER, 82=MENU)",
              );
            }
            break;

          case "screen":
          case "s":
            const result = await detectScreen();
            console.log(
              `  Detected: ${result.screen} (${(result.confidence * 100).toFixed(0)}%)`,
            );
            break;

          case "shot":
            const png = await screenshot();
            const shotPath = path.join(
              __dirname,
              "..",
              "reports",
              `pppoker_${Date.now()}.png`,
            );
            fs.writeFileSync(shotPath, png);
            console.log(`  Screenshot saved: ${shotPath}`);
            break;

          case "pixels":
          case "p":
            await detectScreen(); // This prints pixel info
            break;

          case "auto":
          case "a":
            await navigate();
            break;

          case "quit":
          case "q":
          case "exit":
            rl.close();
            return;

          default:
            console.log(
              "Unknown command. Type 'tap', 'screen', 'shot', 'auto', or 'quit'",
            );
        }
      } catch (err) {
        console.error(`  Error: ${err.message}`);
      }

      prompt();
    });
  };

  prompt();
}

// ── Quick-tap Mode ──────────────────────────────────────────────────

/**
 * Send a sequence of taps with delays.
 * Usage: node scripts/pppoker-navigator.js --taps "540,960,2000;540,1400,3000"
 */
async function tapSequence(seqStr) {
  const steps = seqStr.split(";").map((s) => s.trim().split(",").map(Number));

  for (const [x, y, delayMs] of steps) {
    console.log(`Tap (${x}, ${y}) then wait ${delayMs || 2000}ms...`);
    await tap(x, y);
    await sleep(delayMs || 2000);
  }
}

// ── Utilities ───────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ── Main ────────────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);

  if (args.includes("--interactive") || args.includes("-i")) {
    return interactiveMode();
  }

  if (args.includes("--taps")) {
    const idx = args.indexOf("--taps");
    const seq = args[idx + 1];
    if (!seq) {
      console.error("Usage: --taps 'x1,y1,delay;x2,y2,delay;...'");
      process.exit(1);
    }
    return tapSequence(seq);
  }

  if (args.includes("--screen") || args.includes("-s")) {
    const result = await detectScreen();
    console.log(
      `Screen: ${result.screen} (${(result.confidence * 100).toFixed(0)}%)`,
    );
    return;
  }

  if (args.includes("--shot")) {
    const png = await screenshot();
    const shotPath = path.join(
      __dirname,
      "..",
      "reports",
      `pppoker_${Date.now()}.png`,
    );
    fs.writeFileSync(shotPath, png);
    console.log(`Screenshot saved: ${shotPath}`);
    return;
  }

  // Default: run auto-navigator
  await navigate();
}

main().catch((err) => {
  console.error("Fatal:", err.message);
  process.exit(1);
});
