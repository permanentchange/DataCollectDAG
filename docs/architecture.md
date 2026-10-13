# 运行架构与配置参考

本文对应当前 `src/data_collect_dag/` 实现。安装和 CLI 示例见 [README](../README.md)；ROS 控制消息格式见 [外部接口](ros_external_system_architecture.md)。

## 1. 运行路径

```text
CLI / python -m data_collect_dag
    -> AppRuntime (app.py): 加载配置、标定、ROS 接口、控制队列
    -> RosAdapter (ros_adapter.py): 订阅 topic、接收控制、发布状态
    -> SessionManager (session.py): 管理唯一活动 session
    -> SessionRuntime: topic 前置检查、输入缓存、主帧队列、节点初始化
    -> DagExecutor (dag.py): 将满足依赖的节点提交线程池
    -> 处理节点 (nodes/): 同步、筛选、去畸变、点云处理、保存
    -> session_summary.json / saved_samples.json / debug.log
```

- `AppRuntime` 初始化时加载 YAML 和标定 JSON；`calibration.path` 必须提供且文件可读。`AppRuntime.run` 等待 ROS master、初始化 ROS adapter，然后通过控制队列启动 CLI 指定的 pipeline。
- `--init-demo DIR [--car-id CAR_ID]` 从发行包离线导出全部 demo YAML，并创建 `models/` 目录，不创建运行时或启动 ROS；可编辑安装使用源码中的 demo。标定按 `calibration/<car-id>/cali.json` 组织，指定车型时只导出该车型目录的标定 JSON；不指定时导出所有车型，配置默认选择 SA4。车型名称不区分大小写；未知车型在写入前报错并列出可用车型。导出 YAML 的标定路径指向选中车型的绝对路径，模型的 `demo/` 相对引用改为导出目录内的绝对路径，其他配置不变。
- 默认发现任何导出模板重名即在写入前退出；`--force` 仅覆盖此次导出的模板文件，不删除其他车型、无关文件或模型权重。目录内指向外部的目录符号链接被拒绝，文件符号链接在覆盖时替换为普通文件，不修改链接目标。导出模式与 `--config`、`--clean`、`--background` 互斥，`--force` 和 `--car-id` 仅适用于此模式；升级包不会修改已经导出的配置。
- `--background` 在创建运行时之前用当前 Python 启动独立进程并立即返回 PID；子进程继承环境和工作目录，终端关闭后继续运行，stdout/stderr 追加到 `<output.root_dir>/background.log`。它与 `--clean` 互斥；启动失败详情见日志。ROS `shutdown` 与 SIGINT/SIGTERM 都通过控制队列收尾并退出整个程序；`stop` 只停止 session。
- `RosAdapter` 订阅配置中 **所有** `ros.topics`；只有绑定活动 session 时，帧才进入其缓存。消息转换、接入和状态发布由 ROS 适配层承担。
- `SessionManager` 同时只维护一个 session；新的 `start` 会先结束原 session。`pause` 停止接收新帧并阻止新样本开始处理；`resume` 恢复。
- `SessionRuntime` 用 `main_source` 的消息触发 `MainFrameEvent`。主帧排队后可以延时处理；队列满时尝试丢弃最旧主帧事件。`sample_workers` 消费主帧，`node_workers` 是 DAG 节点线程池大小。
- `DagExecutor` 在前驱节点全部返回 `OK` 后调度后继节点。任何非 `OK` 结果会阻止该样本继续调度，异常转换为 `FAIL_SAMPLE`；已提交任务仍等待返回。每个样本通过 `SampleContext` 传递数据与元信息。

## 2. YAML 配置

核心字段和解析行为定义于 `config.py::load_app_config`。

| 字段 | 行为 |
| --- | --- |
| `output.root_dir` | 会话输出目录，默认 `output` |
| `output.disk_storage_limit_GB` | root_dir 内普通文件总大小上限；达到上限拒绝启动，运行中达到上限则停止新样本 |
| `output.disk_storage_need_GB` | 每次新任务开始时要求所在文件系统的最小空闲空间；暂停恢复不复查 |
| `calibration.path` | 启动运行时必须存在的标定 JSON 文件路径 |
| `ros.node_name` | ROS 节点名，默认 `data_collect_dag` |
| `ros.control` | 可选 `control_topic`、`status_topic`、`start_service`、`stop_service`、`status_service` |
| `ros.topics.<key>` | 每个 topic 定义 `topic`、`msg_type`、`role`；`sensor_name` 可省略 |
| `cache.defaults_by_role` / `cache.topic_overrides` | 按 role 定义每个 topic 的 `max_frames`、`max_age_sec`，再按 topic 覆盖 |
| `runtime` | 线程、队列、延时、超时配置 |
| `pipelines.<name>` | `main_source`、`nodes`、`edges`，以及可选前置检查和结束条件 |

运行时默认值：

| `runtime` 参数 | 默认值 |
| --- | ---: |
| `debug` | `false` |
| `sample_workers` | `1` |
| `node_workers` | `4` |
| `main_frame_queue_size` | `20` |
| `main_frame_delay_ms` | `0` |
| `stop_timeout_sec` | `5.0` |

`sample_workers`、`node_workers`、`main_frame_queue_size` 必须大于等于 1，`stop_timeout_sec` 必须大于 0。此超时用于工作线程的部分等待；节点线程池退出仍会等待已提交任务，因此不是整个程序退出的总时限。

上述是解析器默认值，不是 demo 的实际设置：`demo/cfg.yaml` 使用 `debug: true`、队列大小 `2`、主帧延时 `20` 毫秒、停止等待 `1.0` 秒；线程数仍为 `1` / `4`。

两个容量参数使用十进制 GB（1,000,000,000 字节），接受有限的非负数；省略、`null` 或 `0` 关闭对应检查。比较使用未舍入的容量：目录大小大于等于 limit 拒绝启动，空闲空间小于 need 拒绝启动。检查在创建会话目录之前执行；root_dir 不存在时视为零大小，在最近存在的父目录查询空闲空间。

启用 limit 后，启动时扫描 root_dir，统计所有普通文件的逻辑大小（包括其他文件、诊断和日志），不跟随符号链接。写入增量与每帧检查使用容量缓存；会话 `debug.log` 和根目录 `background.log` 每秒更新，后台每 30 秒扫描校准外部改动。超限或容量查询失败后停止接收新帧及启动排队样本，等待在途样本结束，再经控制队列结束会话。允许在途数据及收尾文件造成少量超额；此限制不是磁盘配额。status 查询和 ROS 回调不扫描目录。

`--config CONFIG --clean DAY` 仅加载输出配置并清理后退出，不启动运行时；不提供配置会明确报错，不自动选择清理目录。只删除 root_dir 的直接普通会话子目录，完整格式为 `YYYYMMDD_HHMMSS_八位小写十六进制后缀`，按目录名中的有效本地日期判断。DAY=0 删除全部会话目录；DAY=N>0 删除早于今天减去 N−1 天的目录，未来日期保留。其他目录、文件及符号链接保留；root_dir 不存在时成功退出，删除失败返回非零。`--pipeline` 不影响清理。清理应在采集停止时执行。

最小的结构示例（需要事先准备标定文件和对应 ROS topic）：

```yaml
output:
  root_dir: ~/DATA_COLLECT
calibration:
  path: /absolute/path/calibration.json
ros:
  node_name: data_collect_dag
  control:
    control_topic: /data_collect/control
    status_topic: /data_collect/status
  topics:
    camera:
      topic: /camera/image_raw
      msg_type: sensor_msgs/Image
      role: image
cache:
  defaults_by_role:
    image:
      max_frames: 10
      max_age_sec: 1.0
pipelines:
  example:
    main_source: camera
    nodes:
      - node_id: start
        type: start
      - node_id: end
        type: end
    edges:
      - [start, end]
```

**路径规则：** `output.root_dir`、`calibration.path` 在解析 YAML 时使用 `Path.expanduser().resolve()`；YOLO 节点在初始化时展开 `model_path` 的用户目录并检查文件存在。相对路径均以进程当前工作目录为基准，不以 YAML 文件所在目录为基准。仓库示例从仓库根目录启动；`--init-demo` 导出的标定和模型路径已经是绝对路径，可从任意工作目录启动。

### Pipeline 规则

- `main_source` 必须是 `ros.topics` 中已定义的 topic key。
- `nodes` 中必须恰有一个 `type: start` 和一个 `type: end`；`edges` 引用的节点必须存在；图必须无环，且每个节点都可从 start 到达并可到达 end。
- 每个节点可配置 `node_id`、`type`、`inputs`、`outputs`、`config`；其中 `inputs` / `outputs` 的值是样本上下文中的键名。
- 可选 `topics` 必须与正整数 `time_delay_ms` **同时提供**。每次启动 session 时，系统在该时间窗口内等待所有列出的 topic 各收到至少一条新消息；超时则本次启动失败。省略这对字段时不做 topic 前置检查。
- `stop_conditions.max_duration_sec` 和 `max_saved_samples` 都可省略；达到条件后结束当前 session，状态为 `COMPLETED`。持续时间不累计暂停时间。
- 缓存必须为每个配置的 role/topic 提供 `max_frames` 与 `max_age_sec`；不配置完整策略会导致配置解析失败。

完整示例：`demo/cfg.yaml` 包含 `init`、`fps`、`fps_undist`、`fps_with_yolo`、`compare_motion`。`init` 预检查 topic、执行同步，活动运行 2 秒后完成，不包含保存节点；其他 pipeline 的活动时长上限为 6,000 秒。`fps`、`fps_undist` 和 `fps_with_yolo` 的目标频率为 1 FPS。`fps_undist` 保留 fps 的采样、同步及点云处理，跳过去畸变，按原相机顺序保存五路原图 JPEG；`compare_motion` 分别保存运动补偿与不补偿的点云。会话完成后程序保持可控制状态，不自动退出。

## 3. 内置节点

注册表：`session.py::NODE_TYPES`。目前支持下列 `type`：

| 类别 | 节点类型 | 功能 |
| --- | --- | --- |
| 图结构 | `start`、`end` | 样本 DAG 的入口和出口 |
| 采样频率 | `fps_guard`、`fps_marker` | 根据 `gate_id` / `max_fps` 限频；保存成功后标记时间戳 |
| 同步 | `time_sync`、`multi_time_sync` | 依据时间戳匹配缓存帧；支持 `nearest`、`latest_before` |
| 区域筛选 | `localization_range`、`pointcloud_radius_crop`、`pointcloud_roi_crop` | 按定位范围或空间范围筛选 |
| 点云 | `pointcloud_motion_compensation`、`pointcloud_transform`、`aggregate_pointclouds` | 运动补偿、坐标变换、聚合 |
| 图像 | `image_undistort` | 根据标定文件去畸变 |
| 推理 | `yolo_gate` | 用 Ultralytics 加载本地模型，按类别/置信度/面积筛选 |
| 保存 | `xtreme1_structured_save` | 保存 Xtreme1 风格的图片、PCD 和标定配置 |

`fps_guard` 读取主帧时间戳；`fps_marker` 用相同 `gate_id` 更新已接受样本的时间戳。两者按 pipeline DAG 的依赖顺序连接，而不是单纯限制所有输入消息数量。详情见 `nodes/common.py`。

`xtreme1_structured_save.config.include_distortion_params` 是布尔值，默认 `false`；`fps_undist` 设置为 `true`，在每个相机 JSON 的 `items[i]` 中增加与 `camera_internal` 同级的 `dist_model` 和 `dist_coeffs`。数值取自对应源标定，系数不随分辨率缩放；内参沿用现有的分辨率缩放规则。节点初始化时验证畸变参数；启用该模式但存在对应相机的去畸变元数据时拒绝保存该样本，避免参数与图像不匹配。其他 pipeline 默认不增加这些字段。

`yolo_gate` 的 `config` 必须包含 `model_path`、`class_names`、`confidence_thresholds`、`min_area_ratios`、`predict_conf`，同时将图像输入映射为 `inputs.image`。三个列表按下标对齐，`predict_conf` 不得大于最小的类别阈值；`failure_policy` 只接受 `skip_sample`。`device: cpu` 为默认设置；`device: auto` 对非 `.engine` 模型优先检查 CUDA，并在推理异常时重试 CPU。明确指定 CUDA 且不可用则初始化失败；TensorRT `.engine` 不支持自动切换到 CPU。

`ultralytics` 和 `torch` 仅在 YOLO 节点初始化的相应路径中导入；非 YOLO 的 `fps` 只依赖基础安装。`onnx-cpu` / `onnx-gpu` 是可选安装组，**当前没有独立的 ONNX DAG 节点**。

## 4. 数据与文件

`ros_messages.py` 将 ROS 消息包装为图像、点云、定位、IMU、里程计或文本 frame。图像解码和点云解析可以在处理节点使用数据时延后进行。标定 JSON 通过 `app.py::load_calibration` 读取，使用顶层 `images` 和 `pointclouds`；点云变换只选取包含 `sensor2base` 的条目。

相机标定采用明确的模型标识：当前仅支持 `dist_model: equidistant`，要求 `dist_coeffs` 含四个有限数值，顺序为 OpenCV fisheye 的 `[k1, k2, k3, k4]`。模型字符串定义参数的数学含义，不按系数数量推断；未知模型明确报错。`image_undistort` 继续使用现有 OpenCV fisheye 映射算法，诊断 JSON 也记录源模型。

车型标定位于 `demo/calibration/<car-id>/cali.json`，仓库 YAML 默认指向 SA4。`tools/cali_parser.py VEHICLE_DIR -o OUTPUT` 将外部车型目录转换为当前 JSON 格式，自动创建输出父目录；省略 `-o` 时写入输入车型目录内的 `cali.json`。

一次 session 的根目录是：

```text
<output.root_dir>/<session_id>/
  debug.log
  saved_samples.json
  session_summary.json
  xtreme1/<dataset_name>/
    camera_config/<timestamp_ns>.json
    lidar_point_cloud_0/<timestamp_ns>.pcd
    camera_image_0/<timestamp_ns>.jpg
    camera_image_1/<timestamp_ns>.jpg
    ...
```

`xtreme1_structured_save` 需要已经在 `base` 坐标系下的点云、配置指定的相机图像，以及这些相机的标定数据。保存时生成 JPEG、ASCII PCD、相机配置 JSON；目录由 `dataset_name` 决定。是否产生 `xtreme1/` 目录取决于 pipeline 是否包含保存节点。

`saved_samples.json` 的顶层格式为 `{"items": [...]}`，只记录保存成功的样本。`session_summary.json` 记录 session id、pipeline、开始/结束时间、`STOPPED` / `COMPLETED` / `FAILED` 状态、原因、计数、输出路径和错误。指标和 ROS 状态字段见 [外部接口](ros_external_system_architecture.md)。

## 5. 实现边界

- 采集必须具备 ROS1 Noetic Python 包、ROS master 和配置引用的自定义消息定义；pip 安装不能替代这些系统资源。模板导出和清理不需要 ROS master。
- 仓库不包含 ROS bag、YOLO 模型权重，也不会在基础安装时自动下载模型；`demo/models/yolo26m.pt` 需自行提供。
- 代码中的节点类型为固定注册表；没有通过 YAML 动态导入任意 Python 插件的机制。
- wheel 与源码包均包含 demo YAML 和各车型的标定 JSON，通过 `--init-demo` 离线导出；模型权重和运行输出不打包，升级安装包不会修改已导出的文件。
