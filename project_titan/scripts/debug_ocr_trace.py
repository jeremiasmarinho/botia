"""Trace TitanOCR internal flow to find where values are lost."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2
import numpy as np

path = os.path.join(os.path.dirname(__file__), "..", "..", "screenA.png")
frame = cv2.imread(path)
frame = cv2.resize(frame, (720, 1280))

# Stack region
x, y, w, h = 405, 1015, 200, 50
crop = frame[y:y+h, x:x+w]

from agent.vision_ocr import TitanOCR
ocr = TitanOCR()

print(f"pytesseract loaded: {ocr._pytesseract is not None}")
print(f"cv2 loaded: {ocr._cv2 is not None}")
print(f"np loaded: {ocr._np is not None}")

if ocr._pytesseract:
    print(f"tesseract cmd: {ocr._pytesseract.pytesseract.tesseract_cmd}")

# Step 1: Preprocess
preprocessed = ocr._preprocess(crop)
print(f"\nPreprocessed shape: {preprocessed.shape if preprocessed is not None else None}")
print(f"Preprocessed dtype: {preprocessed.dtype if preprocessed is not None else None}")
if preprocessed is not None:
    text_px = float(np.mean(preprocessed < 128))
    print(f"Text density: {text_px:.3f}")

# Step 2: Try OCR directly
if preprocessed is not None:
    text = ocr._ocr_with_tesseract(preprocessed)
    print(f"\nRaw OCR text: '{text}'")
    
    parsed = ocr._parse_numeric_text(text)
    print(f"Parsed value: {parsed}")
    
    # Also try inverted
    inverted = cv2.bitwise_not(preprocessed)
    text_inv = ocr._ocr_with_tesseract(inverted)
    print(f"Inverted OCR text: '{text_inv}'")
    parsed_inv = ocr._parse_numeric_text(text_inv)
    print(f"Inverted parsed: {parsed_inv}")

# Step 3: Full flow
result = ocr.read_numeric_region(crop, key="stack", fallback=0.0)
print(f"\nFull flow result: {result}")
