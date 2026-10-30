# Repository toolkit

Run from the checkout, or use an absolute path from another directory:

```bash
python tools/toolkit.py --help
python tools/toolkit.py cloud --help
python tools/toolkit.py dataset split --help
```

The entry point adds this checkout's `src` to its Python path. No installed command is added, and the tools are not bundled configuration templates or part of `data_collect_dag init`. Use Python 3.8 and the project's base dependencies; see the [installation guide](../README.md#install). Optional dependencies load only when used: Plotly 6.9.0 generates offline HTML, Open3D 0.19.0 reads compressed PCD, and the existing YOLO installation provides Ultralytics. Models must already exist locally. ROS commands require the sourced ROS1 Noetic environment; Jetson monitoring requires `tegrastats`.

For the optional visualization dependencies, install only those you use into the selected interpreter:

```bash
python -m pip install 'plotly==6.9.0'
# Only needed for compressed PCD input:
python -m pip install 'open3d==0.19.0'
```

For inference use the project's YOLO installation mode, including its local-wheel procedure on Jetson. The toolkit does not install dependencies or download model files automatically. Root/group help uses the standard library; leaf help may import base numerical/image dependencies but does not load optional ROS or inference backends.

| Group | Commands |
| --- | --- |
| `cloud` | `view`, `bev`, `project` |
| `dataset` | `check`, `select`, `merge`, `split`, `archive` |
| `yolo` | `filter`, `frames` |
| `calibration` | `convert` |
| `ros` | `sync`, `record`, `camera-reset` |
| `monitor` | `jetson`, `report` |

Arguments belong after the leaf command. Operational errors caught by the grouped entry point return 1; argparse errors return 2. Successful commands return 0. `ros record` returns its recording child's exit result. Mutating commands write to the output locations described below; existing diagnostic outputs may be replaced on a rerun.

## Inspect a collection

```bash
python tools/toolkit.py cloud view cloud.pcd --max-points 200000
python tools/toolkit.py cloud bev cloud.pcd bev.png --res 0.1
python tools/toolkit.py cloud project DATASET_ROOT TIMESTAMP --suffix _check
```

`view` supports ASCII/binary PCD and ASCII/little-endian binary PLY. The cloud commands share this loader; compressed PCD additionally needs Open3D. Non-finite XYZ rows are removed during loading, so the displayed input count is the number of finite loaded points. Unsupported formats and truncated declared payloads are errors.

Generated HTML embeds Plotly; viewing it needs no internet. It keeps coordinate picking and X/Y/Z/distance colors (`--color-mode x|y|z|d`, default `z`). Sampling is deterministic (`--seed 0`); `--max-points 0` disables it. The HTML shows input, filtered and displayed counts. `--open` explicitly opens a browser. Default XYZ bounds are X/Y −50…50 m, Z unlimited; `--xmin`, `--xmax`, `--ymin`, `--ymax`, `--zmin`, `--zmax`, `--dmin` and `--dmax` control filtering. Output defaults to the input path with an `.html` suffix; `--output-html` overrides it. Sampling affects only the viewer, not BEV or projection calculations.

`bev` accepts one PCD or a directory. The image records maximum height per occupied cell, including negative heights; empty cells are black. CLI defaults are X −20…30 m, Y −20…20 m, Z −2…3 m, resolution 0.1 m/pixel. A batch reports failures and returns nonzero if any file fails.

`project` takes the Xtreme1 dataset directory containing `camera_config`, `camera_image_N`, and `lidar_point_cloud_0`. It uses saved intrinsics, `rowMajor` matrix layout and distortion metadata: absent metadata or explicit `pinhole` selects pinhole projection; `equidistant` selects fisheye projection and requires four finite coefficients. Other distortion models fail explicitly. Default crop is X/Y −10…10 m and Z 0.2…2 m; default color is distance over 0.2…20 m. Use `--pcd-range XMIN XMAX YMIN YMAX ZMIN ZMAX`, `--color-by x|y|z|d` and `--color-range MIN,MAX` to override. Reports and projected images go under `diagnostics/projection/TIMESTAMP`; missing camera images are recorded in the report. Point statistics are computed after the shared loader and XYZ crop. The report records the model, matrices, bounds and rejected-point count for each rendered camera. See the [collection output format](../docs/architecture.md#4-数据与文件) for the camera and PCD contracts.

## Prepare labeling data

```bash
python tools/toolkit.py dataset check DATASET_ROOT --json validation.json
python tools/toolkit.py yolo filter --model LOCAL_MODEL.pt --input DATASET_ROOT/camera_image_0 --output detections_camera0.json
python tools/toolkit.py yolo filter --model LOCAL_MODEL.pt --input DATASET_ROOT/camera_image_1 --output detections_camera1.json
python tools/toolkit.py yolo frames --input detections_camera0.json detections_camera1.json --output counts.json
python tools/toolkit.py dataset select DATASET_ROOT selected --frames counts.json --min-count 1 --dry-run
python tools/toolkit.py dataset select DATASET_ROOT selected --frames counts.json --min-count 1
python tools/toolkit.py dataset split selected parts --part-count 4 --workers 8
python tools/toolkit.py dataset merge parts/part01 parts/part02 parts/part03 parts/part04 -o merged
python tools/toolkit.py dataset archive parts/part01 parts/part02 parts/part03 parts/part04 -o archives
```

Dataset operations index frames by stem using `camera_config` as the canonical directory. All flat sensor directories must have matching frame sets. Generated `diagnostics` is excluded. Missing/orphan frames, duplicate stems, symbolic links, nested data directories and merge collisions are errors. The check validates filenames and directory structure, not JSON calibration contents or image/point-cloud decoding.

The YOLO filter recursively scans image files under its input. Select actual `camera_image_N` directories, as above, rather than a dataset containing generated diagnostic images. `yolo frames` sums passing objects across the supplied camera detection files by image stem. Its output is an object such as `{"123456789": 2, "123456790": 0}`. Keep all inputs from one dataset: timestamp stems alone do not identify frames across independent collections. `dataset select` rejects unknown frame IDs and invalid counts; IDs absent from the counts file are not selected, including when `--min-count 0` is used.

Merge, split and selection use **hard links only**, require the same filesystem, and never fall back to copying. Outputs must not already exist; split can use an existing parent only if its target `partNN` directories are absent. Source files remain unchanged. Input/output locations must not overlap. `--dry-run` performs complete indexing and static checks without creating output. Link permission failures can still occur during execution; an error identifies partial outputs, which are not silently removed or resumed.

Archives use standard-library ZIP compression level 1, contain sensor directories directly at the archive root, and publish each ZIP after successful completion. Existing ZIP names and duplicate input basenames are rejected. No `7z` command is needed.

YOLO keeps the existing 960×768 and 640×360 presets and thresholds. Other sizes are counted as unsupported. Empty/unreadable images and inference errors are counted as failures. No qualifying detections is a successful run; no supported images or processing failures produce a nonzero result. Defaults remain confidence 0.3 and device `cpu`. Result JSON and frame-count JSON retain their existing formats.

## Diagnose a vehicle

```bash
python tools/toolkit.py calibration convert VEHICLE_DIR -o calibration.json
python tools/toolkit.py ros sync --config demo/cfg.yaml --duration 5 --json sync.json
python tools/toolkit.py ros sync --bag sensors.bag --max-offset-ms 50
python tools/toolkit.py ros record --config demo/cfg.yaml -o sensors.bag --dry-run
python tools/toolkit.py ros record --config demo/cfg.yaml -o sensors.bag
python tools/toolkit.py ros camera-reset --dry-run
python tools/toolkit.py monitor jetson --duration 30s -o monitor.csv
python tools/toolkit.py monitor report monitor.csv --json monitor_summary.json
```

Calibration conversion reads `VEHICLE_DIR/camera/*.yaml` and `VEHICLE_DIR/freespace/freespace.yaml`, including the configured `lidar.aux_lidar_calib_file`. It writes the existing `images`/`pointclouds` JSON format, rounds floating-point values to eight digits and prints camera/lidar counts. Omitting `-o` writes `cali.json` in the vehicle directory; parent output directories are created.

ROS topic defaults come from the repository demo; `--config` chooses another validated collection YAML. Recording includes every configured topic, including `/iccp_status` in the demo, and splits bags every 60 seconds (`--split-duration` overrides it). `--topics` overrides recording topics; sync defaults to configured image/point-cloud roles and supports `--img-topics`, `--pcd-topics` and `--main-topic` (default `/rslidar_points`). A supplied image or cloud topic list replaces that role's configured list. Bag reads are single-pass. Sync reports counts, estimated rate, nearest timestamp offsets (minimum/median/P95/maximum absolute offset and signed mean); missing data is `n/a`. P95 uses the nearest-rank absolute offset, and the sign is sensor timestamp minus reference timestamp. An explicit `--max-offset-ms` turns excessive offsets or missing comparisons into a failing exit code; file, dependency and ROS operational errors can also fail independently of that threshold.

Camera reset defaults to `/plan3a_stream_node` and `/miivii_gmsl_camera_node`, then launches `miivii_gmsl_camera data_collect.launch`. Override with `--nodes`, `--launch-package`, `--launch-file` and `--log-file`. A node-stop timeout prevents launching replacements. PID/log output confirms process creation, not camera readiness.

Jetson CSV columns and units are preserved. The default interval is one second; `--interval` must be at least 0.02 seconds. Duration accepts seconds or `s`, `m`, `h`, `d` suffixes; omission collects until interrupted. `--disk`, `--net` and `--quiet` retain their existing meanings. Missing values remain empty; each producer read is bounded by five seconds or the remaining duration. Producer failure or a run with no first sample returns nonzero, and the producer is reaped on exit. The offline report summarizes finite numeric values using min/mean/nearest-rank P95/max plus missing counts. Its duration is the maximum recorded `elapsed_s`, not inferred wall time between timestamps.

## Compatibility and verification

Existing Python script paths and shell launchers delegate to the shared implementations. `create_data_for_labeling.sh` now takes explicit dataset inputs and `-o OUTPUT`; personal hard-coded paths are removed. Existing helper imports used by calibration and synchronization callers remain available.

| Existing script | Grouped equivalent |
| --- | --- |
| `pcd_viewer.py` / `pcd2bev.py` / `visualize_pointcloud_projection.py` | `cloud view` / `cloud bev` / `cloud project` |
| `xtreme1/merge.py` / `xtreme1/split.py` | `dataset merge` / `dataset split` |
| `yolo_filter.py` / `xtreme1/yolo_filter2frame_list.py` | `yolo filter` / `yolo frames` |
| `cali_parser.py` / `sync_check.py` / `orin_monitor.py` | `calibration convert` / `ros sync` / `monitor jetson` |
| `create_data_for_labeling.sh` / `rosrecord_topic_needed.sh` / `reset_camera.sh` | `dataset archive` / `ros record` / `ros camera-reset` |

The existing Python arguments are retained. Merge dry runs now scan complete frame sets, and merge validates frame consistency just like split. The shell recording wrapper now uses the demo configuration, including its status topic. The archive wrapper is invoked as `bash tools/create_data_for_labeling.sh DATASET_ROOT -o ARCHIVE_DIR`.

```bash
PYTHONPATH=src conda run -n dcd python -m pytest tests/unit/test_toolkit.py tests/unit/test_projection_diagnostics.py tests/unit/test_cali_parser.py tests/unit/test_sync_check.py -q
PYTHONPATH=src conda run -n dcd python -m pytest tests/unit tests/functional -q
```

Automated vehicle tests use timestamp samples, fake producers and dry runs. They do not reset cameras, record live bags, or establish real Jetson hardware accuracy.
