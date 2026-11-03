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

## Tool index

The unified entry point exposes 16 leaf commands. The batch calibration script calls `calibration convert` for multiple vehicles. Every entry point below accepts `--help`. Examples assume the checkout is the working directory; replace `DATASET_ROOT`, `TIMESTAMP`, `VEHICLE_DIR` and other example paths with your own inputs. `TIMESTAMP` is the filename stem without an extension.

| Command | Script path relative to `tools/` | Purpose |
| --- | --- | --- |
| `cloud view` | `pcd_viewer.py` | Interactive point-cloud HTML |
| `cloud bev` | `pcd2bev.py` | Height-based bird's-eye image |
| `cloud project` | `visualize_pointcloud_projection.py` | Point-cloud overlays on saved camera images |
| `dataset check` | Use `toolkit.py` | Validate dataset frame sets |
| `dataset select` | Use `toolkit.py` | Select complete frames by object counts |
| `dataset merge` | `xtreme1/merge.py` | Merge complete datasets |
| `dataset split` | `xtreme1/split.py` | Split complete frames into parts |
| `dataset archive` | `create_data_for_labeling.sh` | Create labeling ZIP files |
| `yolo filter` | `yolo_filter.py` | Detect objects and apply box-size thresholds |
| `yolo frames` | `xtreme1/yolo_filter2frame_list.py` | Aggregate passing-object counts by frame |
| `calibration convert` | `cali_parser.py` | Convert one vehicle's calibration |
| Batch `calibration convert` | `batch_cali_parser.sh` | Convert a vehicle list |
| `ros sync` | `sync_check.py` | Compare sensor timestamps |
| `ros record` | `rosrecord_topic_needed.sh` | Record topics with timed bag splitting |
| `ros camera-reset` | `reset_camera.sh` | Stop camera nodes and restart their launch file |
| `monitor jetson` | `orin_monitor.py` | Collect Jetson performance CSV |
| `monitor report` | Use `toolkit.py` | Summarize an existing performance CSV |

`tools/lib/`, `tools/__init__.py` and `tools/lib/viewer.html` contain shared implementation and templates, not standalone commands. Python script examples below accept the same arguments as their grouped equivalents. Shell wrappers use `python3` from `PATH`; the batch calibration wrapper also accepts `--python`.

## Command reference

### `pcd_viewer.py` / `cloud view`

Generate an interactive HTML viewer from one PCD or PLY file:

```bash
python tools/toolkit.py cloud view cloud.pcd --output-html preview.html --color-mode z
python tools/pcd_viewer.py cloud.ply --max-points 100000 --open
```

| Argument | Meaning and default |
| --- | --- |
| `input` | Required point-cloud file |
| `--output-html PATH` | Output HTML; defaults to the input path with `.html` suffix |
| `--size N` | Positive point size; `2.0` |
| `--color NAME` | Plotly colorscale; `Turbo` |
| `--color-mode x\|y\|z\|d` | Color by coordinate or distance from origin; `z` |
| `--xmin`, `--xmax`, `--ymin`, `--ymax` | Coordinate bounds in meters; each axis defaults to −50…50 |
| `--zmin`, `--zmax`, `--dmin`, `--dmax` | Optional Z/distance bounds in meters; unlimited by default |
| `--max-points N` | Display limit after filtering; `200000`, or `0` for all points |
| `--seed N` | Sampling seed; `0` |
| `--open` | Open the generated page in a browser |

The page supports rotation, zoom and coordinate picking. Filtering happens before sampling; an empty filtered cloud is an error. Existing output HTML is replaced. See [Inspect a collection](#inspect-a-collection) for formats and point-count semantics.

### `pcd2bev.py` / `cloud bev`

Generate a height BEV for one cloud, or process the `*.pcd` files directly inside a directory:

```bash
python tools/toolkit.py cloud bev cloud.pcd bev.png --res 0.1
python tools/pcd2bev.py DATASET_ROOT/lidar_point_cloud_0 bev_images --xmin -30 --xmax 40
```

Required positional arguments are `input output`. For a file input, `output` is an image path. For a directory input, it is a directory receiving `<stem>_bev.png`; scanning is not recursive. Existing output images are replaced.

| Options | Defaults |
| --- | --- |
| `--xmin`, `--xmax` | `-20`, `30` meters |
| `--ymin`, `--ymax` | `-20`, `20` meters |
| `--zmin`, `--zmax` | `-2`, `3` meters |
| `--res` | `0.1` meters/pixel; must be positive |

Each occupied cell maps its highest Z value to grayscale within the Z range; empty cells are black. Batch failures are counted while remaining files continue processing.

### `visualize_pointcloud_projection.py` / `cloud project`

Overlay one saved frame's cloud onto its camera images:

```bash
python tools/toolkit.py cloud project DATASET_ROOT TIMESTAMP --suffix _check
python tools/visualize_pointcloud_projection.py DATASET_ROOT TIMESTAMP \
  --pcd-range -20 20 -10 10 -2 3 --color-by z --color-range=-2,3
```

Required positionals are `session_root stem`. Inputs are `camera_config/<stem>.json`, `lidar_point_cloud_0/<stem>.pcd` and `camera_image_N/<stem>.jpg` below the dataset root.

| Option | Meaning and default |
| --- | --- |
| `--pcd-range XMIN XMAX YMIN YMAX ZMIN ZMAX` | XYZ crop in meters; `-10 10 -10 10 0.2 2` |
| `--color-by x\|y\|z\|d` | Color dimension; distance `d` |
| `--color-range MIN,MAX` | Color bounds in meters; `0.2,20`; use `--color-range=-2,3` for negative bounds |
| `--suffix TEXT` | Output filename suffix; empty by default |

Writes `camera_N_projected<suffix>.jpg` and `projection_report<suffix>.json` to `diagnostics/projection/<stem>/`. Reusing a stem and suffix replaces those outputs. The report includes missing images and projection statistics; model handling is described in [Inspect a collection](#inspect-a-collection).

### `dataset check`

Validate frame names and flat sensor-directory structure without decoding file contents:

```bash
python tools/toolkit.py dataset check DATASET_ROOT --json validation.json
```

Required positional `input` is the dataset directory. Optional `--json PATH` saves the report in addition to terminal JSON. A valid report contains `valid`, `frames` and `directories`; validation failure returns nonzero and reports `valid: false` with `error`.

### `dataset select`

Select whole frames using the count JSON produced by `yolo frames`:

```bash
python tools/toolkit.py dataset select DATASET_ROOT selected --frames counts.json --min-count 1 --dry-run
python tools/toolkit.py dataset select DATASET_ROOT selected --frames counts.json --min-count 1
```

Required positionals are `input output`; `--frames PATH` is required. `--min-count N` defaults to `1` and must be a nonnegative integer. Frames whose counts meet the threshold are hard-linked across all data directories. `--dry-run` validates inputs and the output plan without writing. Counts must be nonnegative integers; unknown frame IDs fail. Frames omitted from the counts JSON remain unselected even with a zero threshold.

### `xtreme1/merge.py` / `dataset merge`

Merge one or more datasets whose frame IDs do not overlap:

```bash
python tools/toolkit.py dataset merge part_a part_b -o merged --dry-run
python tools/xtreme1/merge.py part_a part_b -o merged --log-interval 5
```

Required positional `parts` accepts one or more inputs; `-o` / `--output DIR` is required. Input directory sets must match; repeated input paths and frame collisions fail. `--log-interval SECONDS` is positive and defaults to `5`, controlling hard-link progress logging. `--dry-run` checks complete frame sets and the target without creating output.

### `xtreme1/split.py` / `dataset split`

Split a dataset into consecutive groups of sorted frame IDs:

```bash
python tools/toolkit.py dataset split DATASET_ROOT parts --part-count 4 --workers 8 --dry-run
python tools/xtreme1/split.py DATASET_ROOT parts --part-count 4 --workers 8
```

Required positionals are `input output`. `--part-count N` defaults to `4`; `--workers N` defaults to `8`; both must be positive integers. `--dry-run` only validates and prints plans. Outputs are `parts/part01`, `part02`, etc. Frame counts differ by at most one, with the remainder assigned to earlier parts. Empty input fails; requesting more parts than frames can produce empty parts.

### `create_data_for_labeling.sh` / `dataset archive`

Create one ZIP for each validated dataset:

```bash
python tools/toolkit.py dataset archive parts/part01 parts/part02 -o archives --dry-run
bash tools/create_data_for_labeling.sh parts/part01 parts/part02 -o archives
```

Required positional `inputs` accepts one or more datasets; `-o` / `--output DIR` is required. Optional `--dry-run` performs preflight without creating archives or directories. Each output is `<input-directory-name>.zip`, for example `archives/part01.zip`. Sensor directories appear directly at the ZIP root; `diagnostics` is excluded. Existing archive names and inputs with duplicate directory basenames fail. ZIPs use compression level 1 and are published after completion; `7z` is not needed.

### `yolo_filter.py` / `yolo filter`

Run a local model over one camera directory and retain detection records for supported classes:

```bash
python tools/toolkit.py yolo filter --model demo/models/yolo26m.pt \
  --input DATASET_ROOT/camera_image_0 --output detections_camera0.json
python tools/yolo_filter.py --model demo/models/yolo26m.pt \
  --input DATASET_ROOT/camera_image_1 --output detections_camera1.json --conf 0.3 --device cpu
```

| Option | Meaning and default |
| --- | --- |
| `--model PATH` | Required existing local model; no automatic downloads |
| `--input DIR` | Required image directory; scanned recursively |
| `--output PATH` | Required result JSON; replaces an existing file |
| `--conf N` | Inference confidence threshold in `[0, 1]`; `0.3` |
| `--device DEVICE` | Ultralytics inference device; `cpu` |

Image extensions are `.jpg`, `.jpeg`, `.png`, `.bmp` and `.webp`, case-insensitive. Only 960×768 and 640×360 images have filtering presets. All three box thresholds must pass; width and height are pixels, area is square pixels:

| Class | 960×768 minimum width / height / area | 640×360 minimum width / height / area |
| --- | --- | --- |
| `person` | `9 / 30 / 270` | `3 / 10 / 30` |
| `bicycle` | `30 / 18 / 540` | `10 / 6 / 60` |
| `motorcycle` | `33 / 20 / 650` | `12 / 7 / 80` |
| `car` | `35 / 25 / 900` | `12 / 9 / 110` |
| `bus`, `truck` | `45 / 35 / 1500` | `16 / 12 / 190` |
| `cat` | `8 / 6 / 50` | `3 / 3 / 9` |
| `dog` | `12 / 10 / 120` | `4 / 4 / 16` |
| `horse` | `33 / 26 / 850` | `12 / 9 / 100` |

Output is a list of image records containing `image`, `width`, `height`, `passed` and `objects`. Objects include class, confidence, bounding box, width, height, area and `passed`. Classes outside the table are excluded; images with no objects from these classes are omitted. Records may contain objects that fail the size thresholds.

Terminal counts are `processed` (supported images), `unsupported` (other sizes), `failed` (empty/unreadable images or inference errors) and `qualifying` (images with at least one passing object). No qualifying detections can still succeed; processing failures or no supported images return nonzero.

### `xtreme1/yolo_filter2frame_list.py` / `yolo frames`

Sum passing-object counts across detection JSON files from cameras in the same dataset:

```bash
python tools/toolkit.py yolo frames --input detections_camera0.json detections_camera1.json --output counts.json
python tools/xtreme1/yolo_filter2frame_list.py --input detections_camera0.json --output counts_camera0.json
```

Both `--input PATH [PATH ...]` and `--output PATH` are required. Frame IDs come from each image path's stem. Counts sum truthy `objects[].passed`, producing an object such as `{"123456789": 2, "123456790": 0}` for `dataset select --frames`. A recorded image with no passing objects keeps a zero count; images absent from every input are not added. Existing output JSON is replaced.

### `cali_parser.py` / `calibration convert`

Convert one vehicle's camera and lidar calibration to collection JSON:

```bash
python tools/toolkit.py calibration convert VEHICLE_DIR -o calibration.json
python tools/cali_parser.py VEHICLE_DIR --indent 2
```

Required positional `vehicle_dir` contains `camera/*.yaml`, `freespace/freespace.yaml` and the auxiliary lidar file configured by `lidar.aux_lidar_calib_file`. `-o` / `--output PATH` defaults to `VEHICLE_DIR/cali.json`; `--indent N` defaults to `2`. Output parents are created and existing JSON is replaced. The converter preserves the `images` / `pointclouds` structure, rounds floats to eight decimal places and prints camera/lidar counts.

For `batch_cali_parser.sh`, see the [batch conversion instructions](#batch-calibration-conversion) below, including every option, default vehicle list and failure behavior.

### `sync_check.py` / `ros sync`

Compare message `header.stamp` values online or from one bag:

```bash
python tools/toolkit.py ros sync --config demo/cfg.yaml --duration 5 --json sync.json
python tools/sync_check.py --bag sensors.bag --max-offset-ms 50
python tools/toolkit.py ros sync --main-topic /rslidar_points \
  --img-topics /camera/image_raw --pcd-topics /rslidar_points --duration 10
```

| Option | Meaning and default |
| --- | --- |
| `--config PATH` | Validated collection config; checkout's `demo/cfg.yaml` |
| `--img-topics TOPIC [TOPIC ...]` | Replace the configured image-role topic list |
| `--pcd-topics TOPIC [TOPIC ...]` | Replace the configured pointcloud-role topic list |
| `--main-topic TOPIC` | Reference topic; `/rslidar_points` |
| `--bag PATH` | Read a bag instead of subscribing online |
| `--duration SECONDS` | Positive finite online duration; `5`; does not limit bag reading |
| `--json PATH` | Also save the report as JSON |
| `--max-offset-ms N` | Optional finite nonnegative maximum absolute offset threshold |

The configuration is loaded even when topic overrides are supplied. Online mode requires a running ROS master and the selected image/cloud publishers. Bag mode needs ROS `rosbag` and message dependencies but no online master. Reports compare each reference stamp with the nearest sensor stamp; threshold and statistic semantics are described in [Diagnose a vehicle](#diagnose-a-vehicle).

### `rosrecord_topic_needed.sh` / `ros record`

Run `rosbag record` for configured or explicitly supplied topics:

```bash
python tools/toolkit.py ros record --config demo/cfg.yaml -o sensors.bag --dry-run
bash tools/rosrecord_topic_needed.sh --config demo/cfg.yaml -o sensors.bag
python tools/toolkit.py ros record --topics /rslidar_points /camera/image_raw -o custom.bag --split-duration 120
```

| Option | Meaning and default |
| --- | --- |
| `--config PATH` | Collection config; checkout's `demo/cfg.yaml` |
| `--topics TOPIC [TOPIC ...]` | Override all configured recording topics |
| `-o`, `--output PATH` | Passed to `rosbag record -O`; `sensor_data.bag` |
| `--split-duration SECONDS` | Positive finite split duration; `60` seconds |
| `--dry-run` | Print the recording command without starting it |

Default recording includes every configured topic, including the demo's `/iccp_status`. Split filenames are generated by `rosbag`. The wrapper waits in the foreground; Ctrl+C ends recording. It returns the recording child's exit result.

### `reset_camera.sh` / `ros camera-reset`

Stop selected camera nodes, wait for them to exit, then start a launch file:

```bash
python tools/toolkit.py ros camera-reset --dry-run
bash tools/reset_camera.sh
python tools/toolkit.py ros camera-reset --nodes /miivii_gmsl_camera_node \
  --launch-package miivii_gmsl_camera --launch-file data_collect.launch --log-file /tmp/camera-reset.log
```

| Option | Meaning and default |
| --- | --- |
| `--nodes NODE [NODE ...]` | Nodes to stop; `/plan3a_stream_node`, `/miivii_gmsl_camera_node` |
| `--launch-package NAME` | Package passed to `roslaunch`; `miivii_gmsl_camera` |
| `--launch-file NAME` | Launch file; `data_collect.launch` |
| `--log-file PATH` | Appended launch log; `/tmp/data_collect.launch.log` |
| `--dry-run` | Print the stop/launch plan without operating on nodes |

Absent nodes are skipped. A stop timeout aborts before launching replacements. The new launch runs in a separate session; the command prints its PID and log path. Check logs and topics for camera readiness.

### `orin_monitor.py` / `monitor jetson`

Collect performance samples on a Jetson with `tegrastats`:

```bash
python tools/toolkit.py monitor jetson --duration 30s -o monitor.csv
python tools/orin_monitor.py --interval 0.5 --duration 10m --disk nvme0n1 --net eth0 --quiet -o orin_monitor.csv
```

| Option | Meaning and default |
| --- | --- |
| `-i`, `--interval SECONDS` | Sample interval; `1.0`, minimum `0.02` seconds |
| `-d`, `--duration DURATION` | Positive duration: seconds or `s`, `m`, `h`, `d` suffix; omitted means until Ctrl+C |
| `-o`, `--output PATH` | CSV output; `orin_monitor.csv`; replaced on rerun |
| `--disk NAMES` | Comma-separated block-device names; default discovery excludes loop/zram and similar devices |
| `--net NAMES` | Comma-separated interface names; default discovery excludes lo/docker/veth and similar virtual interfaces |
| `--quiet` | Suppress periodic sample summaries; startup and completion messages remain |

CSV data covers CPU/GPU/EMC load and frequency, memory/swap, temperatures, power rails, disk and network throughput. Units follow column suffixes: seconds, MHz, MB, degrees Celsius, mW and MiB/s; unavailable values are empty. Disk and network counters aggregate the selected devices. Producer timeout and cleanup behavior are described in [Diagnose a vehicle](#diagnose-a-vehicle).

### `monitor report`

Summarize a previously recorded CSV without Jetson hardware:

```bash
python tools/toolkit.py monitor report monitor.csv --json monitor_summary.json
```

Required positional `input` is the CSV path. Optional `--json PATH` saves the report; the terminal also receives the full JSON. Results contain `samples`, `duration_s` and `metrics`. Every column except `timestamp` reports finite numeric min/mean/nearest-rank P95/max and missing counts. Duration is the largest `elapsed_s`, not an estimate from wall-clock timestamps.

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

Merge, split and selection use **hard links only**, require the same filesystem, and never fall back to copying. Outputs must not already exist; split can use an existing parent only if its target `partNN` directories are absent. The operations leave source file contents unchanged; hard links share those contents, so later in-place edits through either path affect both. Input/output locations must not overlap. `--dry-run` performs complete indexing and static checks without creating output. Link permission failures can still occur during execution; an error identifies partial outputs, which are not silently removed or resumed.

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

### Batch calibration conversion

`batch_cali_parser.sh` uses the same converter for a vehicle list:

```bash
bash tools/batch_cali_parser.sh --dry-run
bash tools/batch_cali_parser.sh
bash tools/batch_cali_parser.sh --input-root /path/to/argus --output-root /tmp/calibration sa4 SA5
bash tools/batch_cali_parser.sh --python /usr/bin/python3 SA4
```

| Argument | Meaning and default |
| --- | --- |
| `CAR_ID ...` | Optional case-insensitive IDs, each `SA` followed by a positive integer |
| `-i`, `--input-root DIR` | Vehicle parent directory; `<checkout>/../argus` |
| `-o`, `--output-root DIR` | Output parent directory; `<checkout>/demo/calibration` |
| `--python PATH` | Interpreter; `python3` |
| `--dry-run` | Check source directories and print commands without running Python or writing output |

The batch script defaults to the checkout's sibling `argus` directory and writes `demo/calibration/<lowercase-car-id>/cali.json` in the checkout. With no vehicle arguments it processes SA1, SA3–SA16 and SA20–SA29. Explicit IDs are case-insensitive and source directory names use uppercase. Relative paths use the caller's working directory; the script also runs from outside the checkout. `--python /path/to/python3` chooses the interpreter. Existing calibration JSON files are replaced; unrelated files remain. Missing source directories/files and conversion failures are counted per vehicle, processing continues, and any failure returns 1 with a final failed-vehicle list. Invalid arguments return 2. `--dry-run` checks for each vehicle's `camera` directory and `freespace/freespace.yaml` and prints commands without creating outputs or running Python; it does not validate calibration contents or dependencies.

### ROS and monitor behavior

ROS topic defaults come from the repository demo; `--config` chooses another validated collection YAML. Recording includes every configured topic, including `/iccp_status` in the demo, and splits bags every 60 seconds (`--split-duration` overrides it). `--topics` overrides recording topics; sync defaults to configured image/point-cloud roles and supports `--img-topics`, `--pcd-topics` and `--main-topic` (default `/rslidar_points`). A supplied image or cloud topic list replaces that role's configured list. Bag reads are single-pass. Sync reports counts, estimated rate, nearest timestamp offsets (minimum/median/P95/maximum absolute offset and signed mean); missing data is `n/a`. P95 uses the nearest-rank absolute offset, and the sign is sensor timestamp minus reference timestamp. An explicit `--max-offset-ms` turns excessive offsets or missing comparisons into a failing exit code; file, dependency and ROS operational errors can also fail independently of that threshold.

Camera reset defaults to `/plan3a_stream_node` and `/miivii_gmsl_camera_node`, then launches `miivii_gmsl_camera data_collect.launch`. Override with `--nodes`, `--launch-package`, `--launch-file` and `--log-file`. A node-stop timeout prevents launching replacements. PID/log output confirms process creation, not camera readiness.

Jetson CSV columns and units are preserved. The default interval is one second; `--interval` must be at least 0.02 seconds. Duration accepts seconds or `s`, `m`, `h`, `d` suffixes; omission collects until interrupted. `--disk`, `--net` and `--quiet` retain their existing meanings. Missing values remain empty; each producer read is bounded by five seconds or the remaining duration. Producer failure or a run with no first sample returns nonzero, and the producer is reaped on exit. The offline report summarizes finite numeric values using min/mean/nearest-rank P95/max plus missing counts. Its duration is the maximum recorded `elapsed_s`, not inferred wall time between timestamps.

## Compatibility and verification

The script paths listed in the [tool index](#tool-index) delegate to shared implementations and retain their Python arguments. Existing helper imports used by calibration and synchronization callers remain available. The archive shell wrapper requires explicit dataset inputs and `-o OUTPUT`; the recording wrapper uses all demo configuration topics unless overridden.

Run the existing regression suites with the selected interpreter and the optional Plotly and YOLO progress-display dependency (`tqdm`) available:

```bash
PYTHONPATH=src /usr/bin/python3 -m pytest tests/unit/test_toolkit.py tests/unit/test_projection_diagnostics.py tests/unit/test_cali_parser.py tests/unit/test_sync_check.py -q
PYTHONPATH=src /usr/bin/python3 -m pytest tests/unit tests/functional -q
```

An existing Python 3.8 environment such as `dcd` can run these commands with `conda run -n dcd python` in place of `/usr/bin/python3` when it has the needed dependencies.

Automated vehicle tests use timestamp samples, fake producers and dry runs. They do not reset cameras, record live bags, or establish real Jetson hardware accuracy.
