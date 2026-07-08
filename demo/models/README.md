# Demo Model Setup

`demo/xtreme1_demo.yaml` expects the YOLO person-detection weight at:

```text
demo/models/yolo_person_model.pt
```

Recommended manual setup inside the `dcd` environment:

```bash
conda activate dcd
python -m pip install -e .
python -c "import ultralytics; print(ultralytics.__version__)"
```

If the import check fails, install the missing dependency explicitly:

```bash
conda activate dcd
python -m pip install "ultralytics>=8.4,<9"
python - <<'PY'
from pathlib import Path
from ultralytics import YOLO

target = Path("demo/models/yolo_person_model.pt")
target.parent.mkdir(parents=True, exist_ok=True)
model = YOLO("yolov8n.pt")
downloaded = Path(model.ckpt_path)
if downloaded.resolve() != target.resolve():
    target.write_bytes(downloaded.read_bytes())
print(target)
PY
```

After the weight file is present, you can run the demo pipeline from the repository root:

```bash
conda activate dcd
roscore
```

In a second shell:

```bash
conda activate dcd
data_collect_dag --config demo/xtreme1_demo.yaml --pipeline xtreme1_collect
```

In a third shell:

```bash
conda activate dcd
rosbag play test_data.bag
```
