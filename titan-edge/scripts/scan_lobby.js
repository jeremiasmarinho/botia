// Scan left panel text items in detail
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
    if (x < 0 || x >= w || y < 0 || y >= h) return [0, 0, 0];
    const o = 12 + (y * w + x) * 4;
    return [b[o], b[o + 1], b[o + 2]];
  }

  function classify(r, g, b_) {
    const br = r + g + b_;
    if (br < 40) return " ";
    if (r > 230 && g > 230 && b_ > 230) return "W";
    if (r > 180 && g > 180 && b_ > 180) return "w";
    if (r > 150 && g > 80 && b_ < 70) return "O";
    if (r > 150 && g < 80) return "R";
    if (b_ > 150 && r < 80) return "B";
    if (b_ > 100 && r < 60) return "b";
    if (g > r + 30 && g > b_ + 10) return "G";
    if (g > r + 10 && g > 50) return "g";
    if (r > 200 && g > 120 && b_ < 80) return "Y";
    if (br > 150) return "-";
    if (br > 60) return ".";
    return " ";
  }

  // High-res left panel y=280 to y=1280, x=0 to x=480
  console.log("=== Left Panel Detail (60x80 grid) ===");
  for (let row = 0; row < 80; row++) {
    let line = "";
    const y = Math.round(280 + row * 12);
    if (y >= h) break;
    for (let col = 0; col < 60; col++) {
      const x = Math.round(col * 8);
      if (x >= w) break;
      const [r, g, b_] = px(x, y);
      line += classify(r, g, b_);
    }
    console.log(String(y).padStart(4) + " " + line);
  }

  // Also check the bottom area for navigation buttons
  console.log("\n=== Bottom Navigation (x=0-1080, y=1780-1920) ===");
  for (let row = 0; row < 15; row++) {
    let line = "";
    const y = Math.round(1780 + row * 10);
    if (y >= h) break;
    for (let col = 0; col < 50; col++) {
      const x = Math.round(col * 22);
      if (x >= w) break;
      const [r, g, b_] = px(x, y);
      line += classify(r, g, b_);
    }
    console.log(String(y).padStart(4) + " " + line);
  }
}

main().catch((e) => console.error(e));
