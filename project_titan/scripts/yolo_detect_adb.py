"""Run YOLO on an ADB screencap and show all detections."""
import subprocess
import numpy as np
import cv2
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

adb = r"F:\LDPlayer\LDPlayer9\adb.exe"
r = subprocess.run([adb, "-s", "emulator-5554", "exec-out", "screencap", "-p"], capture_output=True)
buf = np.frombuffer(r.stdout, dtype=np.uint8)
img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
print(f"Frame: {img.shape[1]}x{img.shape[0]}")

# Load YOLO model
from ultralytics import YOLO
model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "yolov8n.pt")
model = YOLO(model_path)

# Run inference
results = model(img, verbose=False)
result = results[0]

names = getattr(result, "names", {})
boxes = getattr(result, "boxes", None)

if boxes is not None:
    cls_values = boxes.cls.tolist() if boxes.cls is not None else []
    xyxy_values = boxes.xyxy.tolist() if boxes.xyxy is not None else []
    conf_values = boxes.conf.tolist() if boxes.conf is not None else []
    
    print(f"\n=== {len(cls_values)} detections ===")
    print(f"{'Label':<25} {'Conf':>6} {'CenterX':>8} {'CenterY':>8} {'X1':>6} {'Y1':>6} {'X2':>6} {'Y2':>6}")
    print("-" * 90)
    
    for idx in range(len(cls_values)):
        label = names.get(int(cls_values[idx]), "?")
        conf = conf_values[idx] if idx < len(conf_values) else 0
        x1, y1, x2, y2 = xyxy_values[idx]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        print(f"{label:<25} {conf:>6.3f} {cx:>8.1f} {cy:>8.1f} {x1:>6.0f} {y1:>6.0f} {x2:>6.0f} {y2:>6.0f}")
else:
    print("No detections")

# Print current hero/board Y ranges
print(f"\nCurrent hero_area config: y=828, h=84 → y_min=778, y_max=962")
print(f"Current board_area config: y=411, h=95 → y_min=361, y_max=556")
print(f"\nDefault hero Y range: 750-950")
print(f"Default board Y range: 350-550")
