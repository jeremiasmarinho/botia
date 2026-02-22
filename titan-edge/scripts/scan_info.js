// Scan specific key areas for table info
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
    return [b[o], b[o + 1], b[o + 2]];
  }

  // Dense scan: Top-right corner (table info, usually player count, leave button)
  console.log("=== Top Right (x=800-1080, y=0-120) RGB ===");
  for (let y = 0; y <= 120; y += 8) {
    let items = [];
    for (let x = 800; x < 1080; x += 40) {
      const [r, g, b_] = px(x, y);
      items.push(`${r},${g},${b_}`.padStart(11));
    }
    console.log(y.toString().padStart(3) + ": " + items.join(" | "));
  }

  // Table header/name area (white text on green)
  console.log("\n=== Table Header (x=360-720, y=40-100) RGB ===");
  for (let y = 40; y <= 100; y += 8) {
    let items = [];
    for (let x = 360; x < 720; x += 40) {
      const [r, g, b_] = px(x, y);
      items.push(`${r},${g},${b_}`.padStart(11));
    }
    console.log(y.toString().padStart(3) + ": " + items.join(" | "));
  }

  // Left panel area (the green panel, might be player list or menu)
  console.log("\n=== Left Panel (x=0-250, y=40-300) ===");
  for (let y = 40; y <= 300; y += 15) {
    let line = "";
    for (let x = 0; x <= 250; x += 10) {
      const [r, g, b_] = px(x, y);
      const br = r + g + b_;
      if (br < 60) line += " ";
      else if (r > 200 && g > 200 && b_ > 200) line += "W";
      else if (r > 150 && g > 80 && b_ < 70) line += "O";
      else if (b_ > 150 && r < 80) line += "B";
      else if (g > r + 20 && g > b_) line += "G";
      else if (g > r + 5 && g > 40) line += "g";
      else if (br > 150) line += "-";
      else if (br > 60) line += ".";
      else line += " ";
    }
    console.log(y.toString().padStart(3) + ": " + line);
  }

  // Opponent position - zoomed detail with more points
  console.log("\n=== Opponent Area Fine Scan (x=420-660, y=210-280) ===");
  for (let y = 210; y <= 280; y += 5) {
    let items = [];
    for (let x = 420; x <= 660; x += 30) {
      const [r, g, b_] = px(x, y);
      items.push(`${r},${g},${b_}`.padStart(11));
    }
    console.log(y.toString().padStart(3) + ": " + items.join(" | "));
  }

  // Check hero chip count area (below the cards, y=1660-1720)
  console.log("\n=== Hero Info Area (x=300-800, y=1660-1730) ===");
  for (let y = 1660; y <= 1730; y += 10) {
    let items = [];
    for (let x = 300; x <= 800; x += 50) {
      const [r, g, b_] = px(x, y);
      items.push(`${r},${g},${b_}`.padStart(11));
    }
    console.log(y.toString().padStart(3) + ": " + items.join(" | "));
  }
}

main().catch((e) => console.error(e));
