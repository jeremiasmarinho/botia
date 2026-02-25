"""Full E2E test: card detection + OCR on all 6 frames."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2
import yaml

# Load frames
frames = []
for c in "ABCDEF":
    path = os.path.join(os.path.dirname(__file__), "..", "..", f"screen{c}.png")
    if os.path.exists(path):
        img = cv2.imread(path)
        if img is not None:
            frames.append((f"screen{c}", cv2.resize(img, (720, 1280))))

# Load config
with open(os.path.join(os.path.dirname(__file__), "..", "config_club.yaml")) as f:
    config = yaml.safe_load(f)

# Card detection
from tools.card_reader import PPPokerCardReader
reader = PPPokerCardReader()

# OCR
from agent.vision_ocr import TitanOCR
ocr = TitanOCR()

ocr_cfg = config.get("ocr", {})
def parse_region(s):
    parts = [int(x.strip()) for x in s.split(",")]
    return tuple(parts)

pot_region = parse_region(ocr_cfg.get("pot_region", "310,130,130,70"))
stack_region = parse_region(ocr_cfg.get("stack_region", "410,1022,175,40"))
call_region = parse_region(ocr_cfg.get("call_region", "300,1210,125,30"))

# Action points from config
action_coords = config.get("action_coordinates", {})
action_points = {}
for name, coords in action_coords.items():
    if isinstance(coords, dict):
        action_points[name] = (coords.get("x", 0), coords.get("y", 0))

print(f"Loaded {len(frames)} frames")
print(f"Config: pot={pot_region} stack={stack_region} call={call_region}")
print(f"Actions: {action_points}\n")

for fname, frame in frames:
    # Card detection (takes full frame + action points)
    hero_cards, board_cards = reader.read_cards(frame, action_points)
    
    # OCR
    px, py, pw, ph = pot_region
    pot_val = ocr.read_numeric_region(frame[py:py+ph, px:px+pw], key="pot", fallback=0.0)
    
    sx, sy, sw, sh = stack_region
    stack_val = ocr.read_numeric_region(frame[sy:sy+sh, sx:sx+sw], key="stack", fallback=0.0)
    
    cx, cy, cw, ch = call_region
    call_val = ocr.read_numeric_region(frame[cy:cy+ch, cx:cx+cw], key="call", fallback=0.0)
    
    hero_str = ",".join(hero_cards) if hero_cards else "none"
    board_str = ",".join(board_cards) if board_cards else "none"
    
    print(f"=== {fname} ===")
    print(f"  HERO ({len(hero_cards)}): [{hero_str}]")
    print(f"  BOARD({len(board_cards)}): [{board_str}]")
    print(f"  POT={pot_val:.1f}  STACK={stack_val:.1f}  CALL={call_val:.1f}")
    print()
