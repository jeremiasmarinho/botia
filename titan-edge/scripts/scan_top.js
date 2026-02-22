// Focused scanner for top UI area (table info, player count, login state)
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const ef = promisify(execFile);

const ADB = "F:\\LDPlayer\\LDPlayer9\\adb.exe";

async function main() {
  const r = await ef(ADB, ["-s", "emulator-5554", "exec-out", "screencap"], {
    encoding: "buffer",
    maxBuffer: 15 * 1024 * 1024,
  });
  const b = r.stdout;
  const w = b.readUInt32LE(0);
  const h = b.readUInt32LE(4);

  function px(x, y) {
    const o = 12 + (y * w + x) * 4;
    return { r: b[o], g: b[o + 1], b: b[o + 2] };
  }

  function classify(c) {
    const br = c.r + c.g + c.b;
    if (br < 60) return " ";
    if (c.r > 230 && c.g > 230 && c.b > 230) return "W";
    if (c.r > 200 && c.g > 200 && c.b > 200) return "w";
    if (c.r > 200 && c.g > 150 && c.b < 80) return "Y";
    if (c.r > 150 && c.g > 80 && c.b < 70) return "O";
    if (c.r > 150 && c.g < 80) return "R";
    if (c.b > 180 && c.r < 80) return "B";
    if (c.b > 130 && c.r < 80) return "b";
    if (c.g > c.r + 30 && c.g > c.b + 10) return "G";
    if (c.g > c.r + 10 && c.g > 60) return "g";
    if (br > 120) return "-";
    return ".";
  }

  // Top UI area y=0-300, high resolution
  console.log("=== Top UI Area (60 cols x 40 rows, y=0-300) ===");
  for (let row = 0; row < 40; row++) {
    let line = "";
    const y = Math.round(row * 8);
    for (let col = 0; col < 60; col++) {
      const x = Math.round(col * 18);
      if (x >= w) break;
      line += classify(px(x, y));
    }
    console.log(String(y).padStart(4) + " " + line);
  }

  // Check key positions for player info at top
  console.log("\n=== Top Player Area (x=300-800, y=180-310) ===");
  for (let y = 180; y <= 310; y += 10) {
    let line = "";
    for (let x = 300; x <= 800; x += 20) {
      const c = px(x, y);
      line += `${classify(c)}`;
    }
    console.log(y + ": " + line);
  }

  // Check for opponent avatar/name at top, zoomed in
  console.log("\n=== Opponent Avatar Area (x=360-720, y=200-290) RGB ===");
  for (let y = 200; y <= 290; y += 15) {
    let line = "";
    for (let x = 360; x <= 720; x += 40) {
      const c = px(x, y);
      line += `(${c.r},${c.g},${c.b})`.padEnd(16);
    }
    console.log(y + ": " + line);
  }

  // Hero area (card placeholders) zoom
  console.log("\n=== Hero Cards Area (x=100-1000, y=1540-1650) ===");
  for (let y = 1540; y <= 1650; y += 10) {
    let line = "";
    for (let x = 100; x <= 1000; x += 15) {
      line += classify(px(x, y));
    }
    console.log(y + ": " + line);
  }

  // Check if there's a "start" button or popup in center
  console.log("\n=== Center Screen (y=800-1000) ===");
  for (let y = 800; y <= 1000; y += 20) {
    let line = "";
    for (let x = 200; x <= 900; x += 15) {
      line += classify(px(x, y));
    }
    console.log(y + ": " + line);
  }
}

main().catch((e) => console.error(e));
