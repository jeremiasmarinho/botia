// Quick table pixel scanner
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
  console.log("Screen:", w, "x", h);

  function px(x, y) {
    const o = 12 + (y * w + x) * 4;
    return { r: b[o], g: b[o + 1], b: b[o + 2] };
  }

  function pxStr(x, y) {
    const c = px(x, y);
    return `(${c.r},${c.g},${c.b})`;
  }

  // Scan blue button block (bottom-right)
  console.log("\n=== Blue Button Area (x=700-1060, y=1780-1870) ===");
  for (let y = 1780; y <= 1870; y += 10) {
    let line = "";
    for (let x = 700; x <= 1060; x += 30) {
      line += pxStr(x, y).padEnd(16);
    }
    console.log(y + ": " + line);
  }

  // Scan white center (hero info?)
  console.log("\n=== Center Area (x=400-700, y=1630-1710) ===");
  for (let y = 1630; y <= 1710; y += 10) {
    let line = "";
    for (let x = 400; x <= 700; x += 25) {
      line += pxStr(x, y).padEnd(16);
    }
    console.log(y + ": " + line);
  }

  // Scan all seats around table for "+" or bright spots
  console.log("\n=== Seat Positions (check for + icons or avatars) ===");
  const seats = [
    { name: "Top", x: 540, y: 250 },
    { name: "TopL", x: 150, y: 450 },
    { name: "TopR", x: 930, y: 450 },
    { name: "MidL", x: 80, y: 900 },
    { name: "MidR", x: 1000, y: 900 },
    { name: "BotL", x: 150, y: 1350 },
    { name: "BotR", x: 930, y: 1350 },
    { name: "Hero", x: 540, y: 1650 },
  ];
  for (const s of seats) {
    // Sample 5x5 grid around each seat
    let colors = [];
    for (let dy = -20; dy <= 20; dy += 10) {
      for (let dx = -20; dx <= 20; dx += 10) {
        const xx = Math.min(Math.max(s.x + dx, 0), w - 1);
        const yy = Math.min(Math.max(s.y + dy, 0), h - 1);
        colors.push(pxStr(xx, yy));
      }
    }
    console.log(s.name + " (" + s.x + "," + s.y + "): " + colors.join(" "));
  }

  // Full screen character map y=0 to y=1900
  console.log("\n=== Full Screen Map (40x50) ===");
  for (let row = 0; row < 50; row++) {
    let line = "";
    const y = Math.round(row * 38);
    if (y >= h) break;
    for (let col = 0; col < 40; col++) {
      const x = Math.round(col * 27);
      if (x >= w) break;
      const c = px(x, y);
      const br = c.r + c.g + c.b;
      let ch = " ";
      if (br < 60) ch = " ";
      else if (c.r > 230 && c.g > 230 && c.b > 230) ch = "W";
      else if (c.r > 200 && c.g > 200 && c.b > 200) ch = "w";
      else if (c.r > 180 && c.g > 150 && c.b < 80) ch = "Y";
      else if (c.r > 150 && c.g > 80 && c.b < 70) ch = "O";
      else if (c.r > 150 && c.g < 80) ch = "R";
      else if (c.b > 180 && c.r < 80) ch = "B";
      else if (c.b > 130 && c.r < 80) ch = "b";
      else if (c.g > c.r + 30 && c.g > c.b + 10) ch = "G";
      else if (c.g > c.r + 10 && c.g > 60) ch = "g";
      else if (c.r > 200 && c.g < 100 && c.b < 100) ch = "R";
      else if (br > 120) ch = "-";
      else ch = ".";
      line += ch;
    }
    console.log(String(y).padStart(4) + " " + line);
  }
}

main().catch((e) => console.error(e));
