#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


RULES = {
    (960, 768): {
        "person": (9, 30, 270),
        "bicycle": (30, 18, 540),
        "motorcycle": (33, 20, 650),
        "car": (35, 25, 900),
        "bus": (45, 35, 1500),
        "truck": (45, 35, 1500),
        "cat": (8, 6, 50),
        "dog": (12, 10, 120),
        "horse": (33, 26, 850),
    },
    (640, 360): {
        "person": (3, 10, 30),
        "bicycle": (10, 6, 60),
        "motorcycle": (12, 7, 80),
        "car": (12, 9, 110),
        "bus": (16, 12, 190),
        "truck": (16, 12, 190),
        "cat": (3, 3, 9),
        "dog": (4, 4, 16),
        "horse": (12, 9, 100),
    },
}

SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def qualify_boxes(boxes, names, rules):
    objects = []
    for box in boxes:
        cls = names[int(box.cls.item())]
        if cls not in rules:
            continue

        x1, y1, x2, y2 = box.xyxy[0].tolist()
        bw, bh = x2 - x1, y2 - y1
        area = bw * bh
        min_w, min_h, min_area = rules[cls]
        passed = bw >= min_w and bh >= min_h and area >= min_area

        objects.append({
            "class": cls,
            "confidence": round(float(box.conf.item()), 4),
            "bbox": [round(v, 1) for v in (x1, y1, x2, y2)],
            "width": round(bw, 1),
            "height": round(bh, 1),
            "area": round(area, 1),
            "passed": passed,
        })

    return objects


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--conf", type=float, default=0.3)
    p.add_argument("--device", default="cpu")
    args = p.parse_args()

    if not 0 <= args.conf <= 1:
        p.error("confidence must be between 0 and 1")
    if not Path(args.model).is_file():
        p.error("model must be an existing local file; automatic downloads are disabled")
    if not Path(args.input).is_dir():
        p.error("input must be an image directory")
    from ultralytics import YOLO
    from tqdm import tqdm
    counts = {"processed": 0, "unsupported": 0, "failed": 0, "qualifying": 0}
    print("Loading model...", flush=True)
    model = YOLO(args.model)
    print("Model loaded.", flush=True)

    src = Path(args.input)
    images = sorted(
        x for x in src.rglob("*")
        if x.is_file() and x.suffix.lower() in SUFFIXES
    )
    print("Found {} images.".format(len(images)), flush=True)

    results = []

    for image in tqdm(images, desc="Infer", unit="img"):
        if image.stat().st_size == 0:
            counts["failed"] += 1
            tqdm.write("SKIP empty: {}".format(image))
            continue

        try:
            r = model.predict(
                str(image),
                conf=args.conf,
                device=args.device,
                verbose=False
            )[0]
        except Exception as e:
            counts["failed"] += 1
            tqdm.write("SKIP unreadable: {} ({})".format(image, e))
            continue

        h, w = r.orig_shape
        rules = RULES.get((w, h))
        if rules is None:
            counts["unsupported"] += 1
            tqdm.write("SKIP size: {} ({}x{})".format(image, w, h))
            continue

        counts["processed"] += 1
        objects = qualify_boxes(r.boxes, model.names, rules)

        counts["qualifying"] += int(any(obj["passed"] for obj in objects))
        if objects:
            results.append({
                "image": str(image),
                "width": w,
                "height": h,
                "passed": any(x["passed"] for x in objects),
                "objects": objects,
            })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(str(output), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("Saved: {}".format(output))
    print(json.dumps(counts))
    return int(counts["failed"] > 0 or counts["processed"] == 0)


if __name__ == "__main__":
    main()