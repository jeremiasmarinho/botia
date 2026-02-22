# AI Models Directory

Place the TF.js-exported YOLO model here after training in Colab.

## Export from Colab

```python
from ultralytics import YOLO

model = YOLO("runs/detect/titan_v7_nano/weights/best.pt")

# Export to TensorFlow.js web format
model.export(format="tfjs")
```

This generates:
```
best_web_model/
├── model.json          ← Model architecture + weight manifest
├── group1-shard1of4.bin
├── group1-shard2of4.bin
├── group1-shard3of4.bin
└── group1-shard4of4.bin
```

## Setup

1. Copy the exported folder to `models/yolo-web/`
2. The renderer loads it via:
   ```javascript
   const model = await tf.loadGraphModel('models/yolo-web/model.json');
   ```

## Model Specs

| Property     | Value           |
|--------------|-----------------|
| Architecture | YOLOv8n (Nano)  |
| Input Size   | 640×640         |
| Classes      | 62 (52 cards + 10 buttons) |
| Format       | TensorFlow.js WebGPU |
| Target       | <35ms inference on RTX 2060 Super |
