# 数据采集 DAG 工具架构设计文档

## 1. 文档范围

本文档只描述当前代码已经实现的架构边界、核心组件、运行时语义和扩展点，不描述具体业务 pipeline 的节点编排细节，也不把未来候选设计写成既成事实。

本文档以当前代码为准。若后续实现发生变化，应优先更新本文档中的架构约束，而不是在本文档中保留过期行为说明。

---

## 2. 设计目标与原则

目标：

1. 作为长期运行的数据采集 DAG 工具工作，而不是以 ROS node 业务逻辑为中心。
2. 支持通过配置声明 ROS topic、缓存策略和样本级 DAG。
3. 支持单 session 运行、主帧触发、样本级并发处理和结构化保存。
4. 保证 ROS 接收路径不被推理、处理和写盘阻塞。

原则：

1. ROS 通讯层与 DAG 执行层解耦。
2. ROS callback 只做消息包装和事件投递，不做耗时业务处理。
3. 只有 `ros.topics` 中声明的 topic 会被订阅；未声明 topic 不参与运行时统计。
4. DAG 内部统一使用工具内部数据对象，不把 ROS message 类型直接暴露给业务节点。
5. 一个主帧事件对应一个 `SampleContext`。
6. `SampleContext` 是线程安全的数据容器，不承载 session 级资源。
7. 保存目录结构由保存节点决定，但 `session_summary.json` 由框架统一写入。

---

## 3. 总体架构

```text
CLI / ROS Control
        ↓
ControlCommandQueue
        ↓
AppRuntime
        ↓
SessionManager
        ↓
SessionRuntime
    ├── RosAdapter 绑定当前 session
    ├── SessionInputCache
    ├── MainFrameEventQueue
    ├── SampleWorker Threads
    ├── DagExecutor
    ├── Shared ThreadPoolExecutor
    ├── DAG Nodes
    ├── MetricsRecorder
    ├── StatusManager
    └── SummaryWriter
```

组件职责：

| 组件 | 当前职责 |
|---|---|
| `AppRuntime` | 加载配置和标定、启动 ROS、处理信号、串行分发控制命令 |
| `ControlCommandQueue` | 在控制线程前串行化 start/stop/pause/resume/complete/shutdown |
| `SessionManager` | 维护唯一 active session，处理 start/replace/stop |
| `SessionRuntime` | 持有单 session 的缓存、队列、节点、线程池、统计和输出目录 |
| `RosAdapter` | 订阅配置 topic、包装 ROS message、发布状态、接收 ROS 控制指令 |
| `SessionInputCache` | 维护按 `topic_key` 分桶的 session 级缓存 |
| `DagExecutor` | 按拓扑依赖调度 ready nodes，并收集节点结果 |
| `BaseNode` 子类 | 具体同步、筛选、点云处理、保存等业务逻辑 |
| `StatusManager` | 对外暴露运行时状态快照 |
| `SummaryWriter` | 在 session 结束或 setup 失败后写 `session_summary.json` |

当前代码中的节点注册通过 `session.py` 模块内的 `NODE_TYPES` 注册表完成，而不是独立的 `NodeFactory` 类。

---

## 4. 控制模型与状态模型

### 4.1 外部控制入口

当前实现支持：

1. CLI 通过 `--config` 和 `--pipeline` 启动默认 pipeline。
2. ROS start topic 发送 pipeline 名称字符串；空字符串回退为 CLI 指定的默认 pipeline。
3. ROS stop topic 请求停止当前 session。
4. ROS pause/resume topic 请求暂停或恢复当前 session。
5. ROS status topic 周期发布状态快照。
6. ROS status service 返回状态快照字符串。
7. ROS start/stop service 使用 `std_srvs/Trigger`；其中 start service 不接收 pipeline 参数，只会启动默认 pipeline。
8. `SIGINT`/`SIGTERM` 触发 shutdown，并以 `STOPPED` 结束当前 session。

所有控制命令都会先进入 `ControlCommandQueue`，再由 `AppRuntime` 中的控制线程串行处理。

### 4.2 工具状态

`tool_state` 当前包含：

```text
IDLE
RUNNING
PAUSED
```

`recent_session_status` 当前包含：

```text
STOPPED
COMPLETED
FAILED
```

说明：

1. `IDLE` 表示当前没有 active session。
2. `RUNNING` 表示当前 session 正在接收和处理数据。
3. `PAUSED` 表示当前 session 仍存在，但暂停接收新帧，也暂停运行时长计数。
4. `STOPPED`、`COMPLETED`、`FAILED` 是最近一个 session 的结束状态，不表示当前是否仍有 active session。

---

## 5. Session 生命周期

### 5.1 start / replace

当前 start 流程：

1. 校验 pipeline 名称存在。
2. 若已有 active session，先以 `replaced_by_new_start` 和 `STOPPED` 停止旧 session。
3. 创建新的 `SessionRuntime`，为其分配 `session_id`。
4. 在 `SessionRuntime.start()` 中创建 `session_root`、节点线程池，并按配置顺序实例化节点和执行 `node.setup()`。
5. setup 全部成功后，绑定 `RosAdapter` 到当前 session，启动 sample worker 和可选的 duration monitor。

若 setup 失败：

1. 已成功 setup 的节点按逆序 `teardown()`。
2. 写入失败的 `session_summary.json`。
3. 最近 session 状态记为 `FAILED`。

### 5.2 stop / complete / pause

当前 stop 流程：

1. 停止接收新帧。
2. 设置 session 级 `cancel_event`。
3. 解除 `RosAdapter` 与当前 session 的绑定。
4. 向主帧队列投递 sentinel，通知 sample worker 退出。
5. 等待 sample worker `join(timeout=stop_timeout_sec)`。
6. 逆序执行 `node.teardown()`。
7. 调用线程池 `shutdown(wait=True)`。
8. 写 `saved_samples.json` 和 `session_summary.json`。

注意：

1. `stop_timeout_sec` 当前只作用于 sample worker 的 `join` 超时。
2. 线程池 `shutdown(wait=True)` 仍会等待已提交节点自然结束，因此当前实现不保证 stop 一定在 `stop_timeout_sec` 内返回。
3. 当前实现依赖节点自身避免不可中断的长时间阻塞。

pause / resume 语义：

1. pause 后当前 session 保留，但不再接收新帧。
2. pause 期间 `max_duration_sec` 计时暂停。
3. resume 后恢复收帧与时长计数。

### 5.3 自动完成条件

当前代码支持两类 stop condition：

1. `max_duration_sec`
2. `max_saved_samples`

满足条件时，`SessionRuntime` 会发送内部 `complete` 命令，并以 `COMPLETED` 结束当前 session。

---

## 6. ROS 数据接入与内部数据对象

### 6.1 配置驱动订阅

`RosAdapter` 只对 `ros.topics` 中声明的数据 topic 建立数据订阅。每个 topic 由以下字段定义：

```yaml
topic:
msg_type:
role:
sensor_name:
```

当前代码已经实现的 `role` 包括：

```text
image
pointcloud
imu
odometry
nmea
localization
```

### 6.2 内部数据对象

ROS message 会被包装为内部对象：

```text
FrameMeta
ImageFrame
PointCloudFrame
ImuFrame
OdometryFrame
LocalizationFrame
TextFrame
```

说明：

1. `nmea` 当前映射为 `TextFrame`。
2. 图像和点云采用延迟解析：包装阶段仅保留必要元信息和 `raw_msg_ref`，真正解码在节点按需执行时发生。
3. `localization` 使用独立包装逻辑，不与 `odometry` 混用。
4. 当前实现没有 `NmeaFrame` 类型，也没有单独的 `Frame wrapper` 类层次。

当前代码路径按 `sensor_msgs/Image` 解析图像消息；`CompressedImage` 不属于已落地的通用输入契约，除非未来代码补齐对应包装逻辑。

---

## 7. Session 级缓存

`SessionInputCache` 是按 `topic_key` 维护的 session 级缓存。

当前行为：

1. 只有 `RosAdapter` 绑定了 active session 且 session 处于可接收状态时，消息才会进入缓存。
2. cache policy 由 `max_frames` 和 `max_age_sec` 控制。
3. 超龄淘汰记为 `cache_age_expired`。
4. 超帧数淘汰记为 `cache_max_frames`。

当前公开的查询语义：

```text
append(topic_key, frame)
query_nearest(topic_key, timestamp_ns, max_time_diff_ms)
query_latest_before(topic_key, timestamp_ns, max_age_sec)
query_range(topic_key, start_time_ns, end_time_ns)
wait_nearest(topic_key, timestamp_ns, max_time_diff_ms, timeout_ms)
```

当前 session 归属是通过“是否绑定到 active session 且是否继续收帧”来确定的，不存在单独的 `before_session_start` 时间边界过滤逻辑。

---

## 8. 主帧触发与 DAG 调度

### 8.1 主帧事件

每个 pipeline 必须声明一个 `main_source`。

当 `main_source` 对应消息被当前 session 接收后：

1. 该帧先写入 `SessionInputCache`。
2. `SessionRuntime` 创建 `MainFrameEvent` 并投递到主帧队列。
3. 若配置了 `main_frame_delay_ms`，sample worker 会在该延迟后再启动样本。

当主帧队列已满时：

1. 当前实现会丢弃队列中最旧的主帧事件。
2. 新事件继续入队。
3. 统计 `main_frame_events_dropped += 1`，原因记为 `main_frame_queue_drop_oldest`。

### 8.2 SampleContext

sample worker 取到主帧事件后会：

1. 创建 `SampleContext(sample_id=主帧 source_timestamp_ns)`。
2. 预先写入 `main_frame`，producer 为 `main_source`。
3. 调用 `DagExecutor.run_sample(sample)`。

`StartNode` 和 `EndNode` 当前是空节点；主帧写入不是由 `StartNode` 完成，而是由 `SessionRuntime` 在调度前完成。

### 8.3 DAG 执行语义

当前 `DagExecutor` 的规则：

1. `start_node_id` 首先进入 ready 集合。
2. 节点返回 `OK` 后，只有其所有前驱均为 `OK` 的后继节点才会进入 ready 集合。
3. ready nodes 提交到 session 共享的 `ThreadPoolExecutor` 并发执行。
4. 任一节点返回非 `OK` 时，不再提交新的 ready node，并设置 `sample.cancel_event`。
5. 已经提交的 futures 不会被强制取消；执行器会等待它们自然返回。

当前实现实际使用的 `NodeResult` 为：

```text
OK
SKIP_SAMPLE
FAIL_SAMPLE
FAIL_SESSION
CANCEL_SESSION
```

但当前通用执行路径只会稳定产生：

1. `OK`
2. `SKIP_SAMPLE`
3. `FAIL_SAMPLE`

`FAIL_SESSION`、`CANCEL_SESSION` 目前只保留在枚举和统计类型中，没有形成通用的架构级控制流。

节点异常当前统一按 `FAIL_SAMPLE` 处理；当前实现没有通用 `on_error` 配置机制。

---

## 9. 节点接口与扩展点

所有节点继承 `BaseNode`，构造时注入：

```text
node_id
node_type
inputs
outputs
config
session
```

节点生命周期接口：

```text
setup()
run(sample)
teardown()
```

节点约束：

1. `run()` 只接收 `SampleContext`。
2. 节点通过 `self.session` 访问 session 级资源，例如 cache、标定、统计和输出目录。
3. `SampleContext` 通过 `key + producer` 组合区分多路同名数据；未指定 producer 且存在多个 producer 时会报歧义错误。
4. `metadata` 是开放字典，当前被框架和节点用于记录 `node_timings_ms`、`node_warnings`、`save_result` 等运行结果。

当前注册的节点类型包括同步、点云处理、YOLO 筛选、聚合和 Xtreme1 结构化保存。具体业务节点列表属于实现层，不作为本文档的稳定接口承诺。

---

## 10. 保存、标定与输出

### 10.1 session 输出

每个成功创建 `session_root` 的 session 都使用独立输出目录。当前框架级输出包括：

1. `debug.log`
2. `session_summary.json`
3. `saved_samples.json`（仅在正常 stop/complete 路径写出）

业务保存节点在 `session_root` 下创建自己的目录结构。

### 10.2 保存语义

当前代码中的保存语义是：

1. 保存节点直接写正式输出目录。
2. 保存节点通过 `sample.metadata["save_result"]` 回传保存结果。
3. 只要样本执行结束时 `save_result.saved == true`，框架就会将该样本计入 `samples_saved`，并把该样本加入待写出的 `saved_samples.json` 记录集合。

因此，当前架构文档不额外承诺“只有到达 end node 才计入 saved”；是否把保存节点放在 end 之前的最后关键路径，应由 pipeline 设计自行保证。

### 10.3 标定依赖

应用启动时会加载标定文件：

1. 图像标定按 ROS topic 字符串索引。
2. 点云外参会被规范化为 `sensor2base`，按传感器 topic 字符串索引。

当前代码中的典型依赖关系：

1. 点云运动补偿和坐标变换节点在 `setup()` 中校验对应点云外参存在。
2. Xtreme1 保存节点在 `setup()` 中校验相机标定存在。

---

## 11. 状态统计与摘要文件

`session_summary.json` 当前会写出完整统计；`StatusSnapshot` 只暴露其中的运行时子集。完整统计口径主要包括：

```text
received_messages
cache_dropped_messages
main_frame_events
main_frame_events_dropped
samples_started
samples_saved
samples_skipped
samples_failed
samples_canceled
warnings
errors
drop_reasons
skip_reasons
fail_reasons
```

当前 `StatusSnapshot` 对外字段包括：

```text
tool_state
recent_session_status
current_session_id
current_pipeline_name
start_time
last_error
warnings
received_messages
main_frame_events
samples_saved
samples_skipped
samples_failed
samples_canceled
drop_reasons
skip_reasons
fail_reasons
```

注意：当前状态快照不包含 `session_root`，也不是 `session_summary.json` 的逐字段子集，而是一个运行时裁剪视图。

---

## 12. 当前未纳入架构承诺的事项

以下内容不应被视为当前已实现的通用架构能力：

1. 通用 `on_error` 配置机制。
2. 节点异常自动转换为 `FAIL_SESSION` 的框架语义。
3. stop 时对正在执行的节点进行强制打断或有界等待保证。
4. `CompressedImage` 的通用输入支持。
5. 文档级固定业务 pipeline 图、固定 topic 清单和节点超时参数。

这些内容如需进入架构文档，应以后续代码已经落地的行为为依据单独补充。
