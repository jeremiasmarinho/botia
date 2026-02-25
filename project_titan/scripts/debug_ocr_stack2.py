"""Find text in stack area using edge detection and contour analysis."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cv2, numpy as np
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

os.makedirs("reports/ocr_debug3", exist_ok=True)

path = os.path.join(os.path.dirname(__file__), "..", "..", "screenA.png")
frame = cv2.imread(path)
frame = cv2.resize(frame, (720, 1280))

# Print the actual pixel values in a grid around the stack area
print("=== STACK AREA PIXEL GRID (y=1020-1060, x=420-580, step=10) ===")
for y in range(1020, 1065, 5):
    line = f"y={y}: "
    for x in range(420, 590, 10):
        b, g, r = frame[y, x]
        gray = int(0.299*r + 0.587*g + 0.114*b)
        line += f"{gray:3d} "
    print(line)

print("\n=== STACK AREA - Searching for text using Canny edges ===")
# Use the stack area with some margin
stack_area = frame[1015:1065, 400:610]
gray = cv2.cvtColor(stack_area, cv2.COLOR_BGR2GRAY)

# Upscale
up = cv2.resize(gray, (gray.shape[1]*4, gray.shape[0]*4), interpolation=cv2.INTER_CUBIC)

# Edge detection
edges = cv2.Canny(up, 50, 150)
cv2.imwrite("reports/ocr_debug3/stack_edges.png", edges)

# Dilate to connect edges
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
dilated = cv2.dilate(edges, kernel, iterations=1)

# Find contours
contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
print(f"Found {len(contours)} contours in stack area")

# Filter for text-like contours (reasonable aspect ratio and size)
text_contours = []
for cnt in contours:
    x, y, w, h = cv2.boundingRect(cnt)
    if h > 15 and h < 150 and w > 5 and w < 200:
        aspect = w / h if h > 0 else 0
        if 0.1 < aspect < 3.0:
            text_contours.append((x, y, w, h))
            print(f"  Text-like contour: x={x//4+400} y={y//4+1015} w={w//4} h={h//4} aspect={aspect:.2f}")

# Try a completely different approach: use MSER (text detector)
print("\n=== Trying MSER text detection ===")
mser = cv2.MSER_create()
# Threshold parameters for small text
mser.setMinArea(50)
mser.setMaxArea(5000)
regions, _ = mser.detectRegions(up)
print(f"MSER found {len(regions)} regions")

for i, region in enumerate(regions[:10]):
    x, y, w, h = cv2.boundingRect(region)
    if h > 10 and h < 200:
        print(f"  MSER region {i}: x={x//4+400} y={y//4+1015} w={w//4} h={h//4}")

# Try approach: high-contrast extraction
# The stack text should have sharper edges than the background
print("\n=== High-contrast pixel analysis ===")
# Compute local variance in a small window
from scipy.ndimage import uniform_filter

gray_f = gray.astype(np.float32)
mean_local = uniform_filter(gray_f, size=5)
var_local = uniform_filter(gray_f**2, size=5) - mean_local**2
var_map = np.sqrt(np.maximum(var_local, 0))

# High variance = edges/text
thresh_var = 20
text_mask = (var_map > thresh_var).astype(np.uint8) * 255
cv2.imwrite("reports/ocr_debug3/stack_variance.png", 
            cv2.resize(text_mask, (text_mask.shape[1]*4, text_mask.shape[0]*4), 
                       interpolation=cv2.INTER_NEAREST))

# Count pixels per column to find text columns
col_sum = np.sum(text_mask > 0, axis=0)
print(f"Columns with text (var>{thresh_var}):")
active_cols = np.where(col_sum > 2)[0]
if len(active_cols) > 0:
    print(f"  x range: {active_cols[0]+400} to {active_cols[-1]+400}")
    print(f"  column sums: {col_sum[active_cols[0]:active_cols[-1]+1]}")

# Let me also look at the ACTUAL pot region more carefully
print("\n=== POT AREA DETAILED PIXEL GRID (y=130-200, x=300-440, step=5) ===")
for y in range(130, 205, 5):
    line = f"y={y}: "
    for x in range(300, 445, 5):
        b, g, r = frame[y, x]
        gray_v = int(0.299*r + 0.587*g + 0.114*b)
        line += f"{gray_v:3d} "
    print(line)

# Try full TitanOCR with the tight stack region on screenE (which worked)
print("\n=== TitanOCR on screenE Stack ===")
from agent.vision_ocr import TitanOCR
ocr = TitanOCR()

pathE = os.path.join(os.path.dirname(__file__), "..", "..", "screenE.png")
frameE = cv2.imread(pathE)
frameE = cv2.resize(frameE, (720, 1280))

for name, (x, y, w, h) in [
    ("stack_cur", (405, 1015, 200, 50)),
    ("stack_tight", (430, 1028, 160, 30)),
    ("stack_vtight", (440, 1032, 140, 20)),
]:
    crop = frameE[y:y+h, x:x+w]
    val = ocr.read_numeric_region(crop, key="stack", fallback=0.0)
    print(f"  {name}: {val}")
