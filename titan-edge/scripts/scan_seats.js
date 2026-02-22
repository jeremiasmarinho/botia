// Check all seat positions and find player indicators
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

  // Check if a pixel is "interesting" (not green felt and not dark background)
  function isInteresting(r, g, b_) {
    const br = r + g + b_;
    if (br < 40) return false; // too dark
    // Green felt check: g > r+20 and g > b+10 and g > 60
    if (g > r + 20 && g > b_ + 10 && g > 60) return false;
    // Dark green
    if (g > r + 5 && g > b_ && br < 120) return false;
    return br > 60;
  }

  // For each seat position, scan a 100x100 area and count interesting pixels
  const seats = [
    { name: "Seat1-Hero", cx: 540, cy: 1650 },
    { name: "Seat2-BotL", cx: 160, cy: 1300 },
    { name: "Seat3-MidL", cx: 80, cy: 850 },
    { name: "Seat4-TopL", cx: 160, cy: 400 },
    { name: "Seat5-Top", cx: 540, cy: 250 },
    { name: "Seat6-TopR", cx: 920, cy: 400 },
    { name: "Seat7-MidR", cx: 1000, cy: 850 },
    { name: "Seat8-BotR", cx: 920, cy: 1300 },
  ];

  for (const seat of seats) {
    let interesting = 0;
    let total = 0;
    let hasWhite = false;
    let hasAvatar = false;
    let hasBright = false;
    let sampleColors = [];

    for (let dy = -50; dy <= 50; dy += 5) {
      for (let dx = -50; dx <= 50; dx += 5) {
        const [r, g, b_] = px(seat.cx + dx, seat.cy + dy);
        total++;
        if (isInteresting(r, g, b_)) {
          interesting++;
        }
        if (r > 200 && g > 200 && b_ > 200) hasWhite = true;
        if (r > 100 && g < 80 && b_ < 80) hasAvatar = true; // reddish/skin
        if (r > 150 && g > 100 && b_ < 80) hasAvatar = true; // warm/orange
        if (r + g + b_ > 400) hasBright = true;
      }
    }

    // Sample center pixels
    for (let dy = -20; dy <= 20; dy += 10) {
      for (let dx = -20; dx <= 20; dx += 10) {
        const [r, g, b_] = px(seat.cx + dx, seat.cy + dy);
        sampleColors.push(`(${r},${g},${b_})`);
      }
    }

    const pct = Math.round((interesting / total) * 100);
    const status = pct > 30 ? "OCCUPIED?" : pct > 10 ? "maybe" : "EMPTY";
    console.log(
      `${seat.name.padEnd(14)} (${seat.cx},${seat.cy}): ${pct}% interesting [${status}] white=${hasWhite} avatar=${hasAvatar}`,
    );
    console.log("  Center:", sampleColors.join(" "));
  }

  // Also check if there's a "back" button at top-left
  console.log("\n=== Top-Left Corner (back button?) ===");
  for (let y = 20; y <= 60; y += 5) {
    let line = "";
    for (let x = 10; x <= 120; x += 5) {
      const [r, g, b_] = px(x, y);
      const br = r + g + b_;
      if (br < 40) line += " ";
      else if (r > 200 && g > 200 && b_ > 200) line += "W";
      else if (b_ > 150 && r < 80) line += "B";
      else if (g > r + 15 && g > b_) line += "G";
      else if (br > 150) line += "-";
      else if (br > 60) line += ".";
      else line += " ";
    }
    console.log(y.toString().padStart(3) + ": " + line);
  }
}

main().catch((e) => console.error(e));
