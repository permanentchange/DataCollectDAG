#!/usr/bin/env python3

import argparse
import json
from collections import Counter
from pathlib import Path


def count_frames(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    counts = Counter()
    for item in data:
        frame = Path(item["image"]).stem
        counts[frame] += sum(
            bool(obj.get("passed", False))
            for obj in item.get("objects", [])
        )
        counts.setdefault(frame, 0)

    return counts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", nargs="+", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    total = Counter()
    for path in args.input:
        total.update(count_frames(path))

    result = dict(sorted(total.items()))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("Saved: {}".format(out))


if __name__ == "__main__":
    main()