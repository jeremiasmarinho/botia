import os
import time
from statistics import pstdev

from agent.vision_yolo import VisionYolo
from agent.vision_ocr import TitanOCR
from utils.config import OCRRuntimeConfig

cfg = OCRRuntimeConfig()
ocr = TitanOCR(use_easyocr=cfg.use_easyocr, tesseract_cmd=os.environ.get('TITAN_TESSERACT_CMD'))
vision = VisionYolo(model_path='')

if not vision.find_window():
    print('AUTO_TUNE: emulator not found')
    raise SystemExit(1)

print('AUTO_TUNE: emulator ok', vision.emulator)

# capture a few frames for stability scoring
frames = []
for _ in range(4):
    f = vision.capture_frame()
    if f is not None:
        frames.append(f)
    time.sleep(0.15)

if not frames:
    print('AUTO_TUNE: no frames')
    raise SystemExit(1)

h, w = frames[0].shape[:2]
print('AUTO_TUNE: frame', w, h)

base = cfg.regions()

# key: (region_w, region_h, dx_range, dy_range)
search = {
    'pot': (base['pot'][2], base['pot'][3], range(-180, 181, 30), range(-140, 141, 20)),
    'call_amount': (base['call_amount'][2], base['call_amount'][3], range(-180, 181, 30), range(-140, 141, 20)),
    'hero_stack': (base['hero_stack'][2], base['hero_stack'][3], range(-120, 121, 20), range(-120, 121, 20)),
}

def clamp_region(x,y,rw,rh):
    x = max(0, min(w-rw, x))
    y = max(0, min(h-rh, y))
    return x,y,rw,rh

for key in ('pot','hero_stack','call_amount'):
    bx, by, bw, bh = base[key]
    rw, rh, dxs, dys = search[key]

    best = None
    for dx in dxs:
        for dy in dys:
            rx, ry, rw2, rh2 = clamp_region(bx + dx, by + dy, rw, rh)
            values = []
            for f in frames:
                crop = f[ry:ry+rh2, rx:rx+rw2]
                v = float(ocr.read_numeric_region(crop, key=None, fallback=0.0))
                values.append(v)

            non_zero = sum(1 for v in values if v > 0.0)
            mean_v = sum(values) / len(values)
            dev = pstdev(values) if len(values) > 1 else 0.0

            # scoring: prefer repeated non-zero + low variance + moderate magnitude
            mag_penalty = 0.0
            if mean_v > 200000:
                mag_penalty = (mean_v - 200000) / 50000
            score = (non_zero * 5.0) + (1.0 if mean_v > 0 else 0.0) - (dev / 200.0) - mag_penalty

            cand = (score, rx, ry, rw2, rh2, values, mean_v, dev)
            if best is None or cand[0] > best[0]:
                best = cand

    score, rx, ry, rw2, rh2, values, mean_v, dev = best
    print(f'AUTO_TUNE {key}: score={score:.2f} region=({rx},{ry},{rw2},{rh2}) values={values} mean={mean_v:.2f} dev={dev:.2f}')

print('AUTO_TUNE DONE')
