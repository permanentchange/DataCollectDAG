# 运行架构与配置参考

本文对应当前 `src/data_collect_dag/` 实现。安装和 CLI 示例见 [README](../README.md)；ROS 控制消息格式见 [外部接口](ros_external_system_architecture.md)。

## 1. 运行路径

```text
启动 CLI --config
    -> InstanceGuard: 获取完整 ROS 节点名对应的本机锁
    -> AppRuntime: 加载配置、标定并绑定控制队列
    -> RosAdapter: 初始化 ROS，订阅 topic，发布状态
    -> 控制队列 -> SessionManager: 管理一个活动 session
    -> SessionRuntime: 容量检查、创建会话目录、topic 预检查、节点初始化
    -> 主帧队列 -> DagExecutor -> 处理节点 -> 会话文件

控制 CLI start/pause/resume/stop/shutdown
    -> 匿名 CLI 节点 -> control_topic -> 采集程序的控制队列
查询 CLI status
    -> 匿名 CLI 节点 <- status_topic <- StatusSnapshot
离线 CLI --init-demo / --clean
    -> 导出模板 / 清理会话目录 -> 退出
```

### 实例与进程

启动要求 `--config`。CLI 先读取 `ros.node_name`：相对名称使用 `ROS_NAMESPACE`，绝对名称指定完整命名空间；未设置 `ROS_NAMESPACE` 时默认完整名称为 `/data_collect_dag`。初始化 ROS、加载标定和构造运行时之前，以完整名的 SHA256 获取非阻塞独占 `fcntl.flock`，锁路径为 `/tmp/data_collect_dag-<SHA256>.lock`。配置路径、输出路径及用户目录不影响锁的身份。

锁在 `IDLE`、`RUNNING` 和 `PAUSED` 时都被持有。重复启动返回非零并报告 `ROS node <完整名称> is already running.`。不同完整节点名可以并存；分别控制时须配置不同的 control/status topic。本机锁在进程退出后释放；保留锁文件以维持后续启动使用同一 inode。互斥范围为本机。

`--background` 由父进程先获取锁，再用当前 Python 和 `Popen(pass_fds=...)` 创建独立进程。子进程继承锁，父进程仅关闭自己的描述符。PID 表示子进程已经创建；后续配置、标定和 ROS 初始化的结果通过日志诊断。子进程继承环境及工作目录，stdout/stderr 追加到 `<output.root_dir>/background.log`。

### 控制与会话

`AppRuntime.run()` 启动 ROS adapter 和控制线程，然后提交一次 `start`，默认 pipeline 为 `init`。ROS 回调将命令放入队列，控制线程串行执行 session 操作。`start` 先验证名称，再停止活动 session 并构造新 session；名称不存在时保留活动 session、暂停状态及计数，并写入 `last_error`。名称有效但新 session 初始化失败时，原 session 已停止，工具进入 `IDLE` 并报告错误。

`pause` 停止接收新帧及启动新样本；`resume` 恢复，活动时长不累计暂停时间。`stop` 结束 session，程序继续接受控制。`shutdown`、SIGINT 和 SIGTERM 通过控制队列收尾并退出整个程序。停止过程请求取消，等待工作线程和已提交的 DAG 任务，并写入会话索引及摘要。

控制客户端按需导入 `ros_control_client.py`，使用匿名 ROS 节点，发送控制 JSON 或读取一次状态；它不创建采集运行时。Topic 覆盖、超时和消息格式见 [ROS 控制接口](ros_external_system_architecture.md)。导出和清理命令见 [README](../README.md)。

### 数据执行

`RosAdapter` 订阅所有配置的 `ros.topics`。活动 session 绑定后，帧进入 `SessionInputCache`；暂停及停止状态不接收新帧。`main_source` 消息触发主帧事件，按配置延时处理。队列满时尝试丢弃最旧事件；`sample_workers` 消费主帧，`node_workers` 执行 DAG 节点。

`DagExecutor` 只在前驱全部返回 `OK` 后提交后继。非 `OK` 结果停止该样本后续调度并请求取消；已提交任务等待返回。节点异常转为 `FAIL_SAMPLE`。数据、元信息和节点计时通过 `SampleContext` 传递。

## 2. YAML 配置

核心字段和解析行为定义于 `config.py::load_app_config`。

| 字段 | 行为 |
| --- | --- |
| `output.root_dir` | 会话输出目录，默认 `output` |
| `output.disk_storage_limit_GB` | root_dir 内普通文件总大小上限；达到上限拒绝启动，运行中达到上限则停止新样本 |
| `output.disk_storage_need_GB` | 每次新任务开始时要求所在文件系统的最小空闲空间；暂停恢复不复查 |
| `calibration.path` | 启动运行时必须存在的标定 JSON 文件路径 |
| `ros.node_name` | ROS 节点名，默认 `data_collect_dag`；相对名使用 `ROS_NAMESPACE`，绝对名指定完整命名空间；本机按完整名互斥 |
| `ros.control` | 可选 `control_topic`、`status_topic`、`start_service`、`stop_service`、`status_service`；省略的接口不注册 |
| `ros.topics.<key>` | 非空 topic 集合；每项定义 `topic`、`msg_type`、`role`；`sensor_name` 默认使用 key |
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

`demo/cfg.yaml` 使用 `debug: true`、`sample_workers: 1`、`node_workers: 4`、队列大小 `2`、主帧延时 `20` 毫秒和停止等待 `1.0` 秒。

两个容量参数使用十进制 GB（1,000,000,000 字节），接受有限的非负数；省略、`null` 或 `0` 关闭对应检查。比较使用未舍入的容量：目录大小大于等于 limit 拒绝启动，空闲空间小于 need 拒绝启动。检查在创建会话目录之前执行；root_dir 不存在时视为零大小，在最近存在的父目录查询空闲空间。

启用 limit 后，启动时扫描 root_dir，统计所有普通文件的逻辑大小（包括其他文件、诊断和日志），不跟随符号链接。写入增量与每帧检查使用容量缓存；会话 `debug.log` 和根目录 `background.log` 每秒更新，后台每 30 秒扫描校准外部改动。超限或容量查询失败后停止接收新帧及启动排队样本，等待在途样本结束，再经控制队列结束会话。允许在途数据及收尾文件造成少量超额；此限制不是磁盘配额。status 查询和 ROS 回调不扫描目录。

`--config CONFIG --clean DAY` 仅加载输出配置并清理后退出，不启动运行时；不提供配置会明确报错，不自动选择清理目录。只删除 root_dir 的直接普通会话子目录，完整格式为 `YYYYMMDD_HHMMSS_八位小写十六进制后缀`，按目录名中的有效本地日期判断。DAY=0 删除全部会话目录；DAY=N>0 删除早于今天减去 N−1 天的目录，未来日期保留。其他目录、文件及符号链接保留；root_dir 不存在时成功退出，删除失败返回非零。`--pipeline` 不影响清理。清理应在采集停止时执行。

最小配置示例：先准备标定文件、运行 ROS master，并发布相应 topic；通过 `--config CONFIG --pipeline example` 启动。此 DAG 仅接收样本，不保存传感器文件。

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
- 每个节点要求 `node_id` 和 `type`，可配置 `inputs`、`outputs` 和 `config`；`inputs` / `outputs` 的值是样本上下文中的键名。
- 可选 `topics` 必须与正整数 `time_delay_ms` **同时提供**。每次启动 session 时，系统在该时间窗口内等待所有列出的 topic 各收到至少一条新消息；超时则本次启动失败。省略这对字段时不做 topic 前置检查。
- `stop_conditions.max_duration_sec` 和 `max_saved_samples` 都可省略；达到条件后结束当前 session，状态为 `COMPLETED`。持续时间不累计暂停时间。
- 缓存必须为每个 topic 提供 `max_frames` 与 `max_age_sec`，由 `defaults_by_role` 与 `topic_overrides` 合并；同名 override 覆盖对应默认字段。

完整示例：`demo/cfg.yaml` 包含 `init`、`fps`、`fps_undist`、`fps_undist_nomotion`、`fps_with_yolo`、`compare_motion`。`init` 预检查 topic、执行同步，活动时长上限为 2 秒，不包含保存节点；其他 pipeline 的活动时长上限为 6,000 秒。`fps`、`fps_undist`、`fps_undist_nomotion` 和 `fps_with_yolo` 的目标频率为 1 FPS。`fps_undist` 按相机绑定顺序保存五路原图 JPEG，并执行 FPS 控制、同步和点云运动补偿；`compare_motion` 将两种点云写入 `collect_demo` 和 `collect_demo_no_motion`。会话完成后程序保持可控制状态，不自动退出。

`fps_undist_nomotion` 保存五路原始图像，以五个 `pointcloud_transform` 节点将雷达点云按静态外参转换到 `base` 后合并，写入 `collect_demo`；不执行图像去畸变或自车运动补偿。它保留 `fps_undist` 的同步、相机顺序和保存参数，预检查仅包含五路相机与五路雷达，窗口为 500 ms。定位、IMU、里程计及 NMEA 无需发布消息，但全局 `ros.topics` 引用的消息类型仍需可解析。运行中的程序不热加载配置；修改后须重新启动程序，使用导出配置时须单独更新或重新导出该 YAML。

## 3. 内置节点

节点注册表为 `session.py::NODE_TYPES`：

| 类别 | 节点类型 | 功能 |
| --- | --- | --- |
| 图结构 | `start`、`end` | 样本 DAG 的入口和出口 |
| 采样频率 | `fps_guard`、`fps_marker` | 根据主帧时间戳限频并更新 gate 标记 |
| 同步 | `time_sync`、`multi_time_sync` | 依据时间戳匹配缓存帧；支持 `nearest`、`latest_before` |
| 区域筛选 | `localization_range`、`pointcloud_radius_crop`、`pointcloud_roi_crop` | 按定位范围或空间范围筛选 |
| 点云 | `pointcloud_motion_compensation`、`pointcloud_transform`、`aggregate_pointclouds` | 运动补偿、坐标变换、聚合 |
| 图像 | `image_undistort` | 根据标定文件去畸变 |
| 推理 | `yolo_gate` | 用 Ultralytics 加载本地模型，按类别/置信度/面积筛选 |
| 保存 | `xtreme1_structured_save` | 保存 Xtreme1 风格的图片、PCD 和标定配置 |

`fps_guard` 根据主帧时间戳、同一 `gate_id` 的最近标记和正数 `max_fps` 决定是否通过；`fps_marker` 更新时间戳。demo 将 marker 接在保存节点之后。实现位于 `nodes/common.py`。

`xtreme1_structured_save.config.include_distortion_params` 是布尔值，默认 `false`；`fps_undist` 和 `fps_undist_nomotion` 设置为 `true`，在每个相机 JSON 的 `items[i]` 中增加与 `camera_internal` 同级的 `dist_model` 和 `dist_coeffs`。数值取自对应源标定，系数不随分辨率缩放；内参根据保存分辨率缩放。节点初始化时验证畸变参数；启用该模式但存在对应相机的去畸变元数据时拒绝保存该样本，避免参数与图像不匹配。`include_distortion_params: false` 时不写入这些字段。

`yolo_gate` 的 `config` 必须包含 `model_path`、`class_names`、`confidence_thresholds`、`min_area_ratios`、`predict_conf`，同时将图像输入映射为 `inputs.image`。三个列表非空、长度一致并按下标对齐，类别名不可重复，阈值为 0 到 1 的有限数值；`predict_conf` 不得大于最小的类别阈值；`failure_policy` 只接受 `skip_sample`。`device: cpu` 为默认设置；`gpu`、`cuda` 映射到设备 `0`，`gpu:N` 映射到 `N`；`device: auto` 对非 `.engine` 模型优先检查 CUDA，并在推理异常时重试 CPU。明确指定 CUDA 且不可用则初始化失败；TensorRT `.engine` 不支持自动切换到 CPU。

`ultralytics` 和 `torch` 在 YOLO 初始化的相应路径中导入；非 YOLO pipeline 使用基础依赖。ONNX 模型也由 `yolo_gate` 通过 Ultralytics 加载。

## 4. 数据与文件

`ros_messages.py` 将 ROS 消息包装为图像、点云、定位、IMU、里程计或文本 frame。图像解码和点云解析可以在处理节点使用数据时延后进行。标定 JSON 通过 `app.py::load_calibration` 读取，使用顶层 `images` 和 `pointclouds`；点云变换只选取包含 `sensor2base` 的条目。

相机标定支持 `dist_model: equidistant`，要求 `dist_coeffs` 含四个有限数值，顺序为 OpenCV fisheye 的 `[k1, k2, k3, k4]`。`image_undistort` 使用 OpenCV fisheye 映射；输入与标定宽高比一致时缩放内参，宽高比不一致时跳过该样本。`write_debug_artifacts: true` 将诊断图像和参数写入会话的 `diagnostics/undistort/` 目录，默认值为 `false`。

车型标定位于 `demo/calibration/<car-id>/cali.json`，仓库 YAML 默认指向 SA4。`tools/cali_parser.py VEHICLE_DIR -o OUTPUT` 读取外部车型目录并生成标定 JSON，自动创建输出父目录；省略 `-o` 时写入输入车型目录内的 `cali.json`。

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

`xtreme1_structured_save` 需要已经在 `base` 坐标系下的点云、配置指定的相机图像，以及这些相机的标定数据。节点需要 `inputs.pointcloud` 和非空 `config.cameras`，每个相机绑定包含 `input_name` 与 `topic_key`。绑定顺序决定 `camera_image_N` 和相机 JSON 的 `items[N]` 顺序；文件名取第一路绑定相机的源时间戳（纳秒）。`dataset_name` 默认 `collect_demo`。

JPEG 质量为 95。ASCII PCD 字段为 `x y z intensity has_intensity`：有效 intensity 须为 0 到 255 的整数，缺失值写为 0 并以 `has_intensity: 0` 标识。相机 JSON 顶层为 `{"items": [...]}`，每项包括缩放后的 `camera_internal`、`width`、`height`、按列展开的 `camera_external` 及 `rowMajor: false`；启用 `include_distortion_params` 时包含源畸变参数。

`saved_samples.json` 的顶层格式为 `{"items": [...]}`，只记录保存成功的样本。`session_summary.json` 记录 session id、pipeline、开始/结束时间、`STOPPED` / `COMPLETED` / `FAILED` 状态、原因、计数、输出路径和错误。指标和 ROS 状态字段见 [外部接口](ros_external_system_architecture.md)。

## 5. 资源与打包

ROS 安装、Master、自定义消息包、传感器 topic、标定源文件和模型权重由运行环境提供。节点类型使用源码中的固定注册表。

构建后端在 wheel 中生成 `data_collect_dag/_demo.zip`，收录 demo YAML 和 `calibration/` 下的 JSON；源码包通过 `MANIFEST.in` 收录同一组模板。模型权重、ROS bag 和采集输出不打包。`--init-demo` 从可编辑 checkout 或安装包资源导出模板；安装及升级不修改已导出的配置。
