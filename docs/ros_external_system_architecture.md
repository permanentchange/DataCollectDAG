# ROS1 外部控制接口

DataCollectDAG 在 ROS1 Noetic 中作为一个 ROS node 运行。外部系统通过 `std_msgs/String` topic 发送 JSON 控制命令、订阅 JSON 状态；采集结果由文件系统交付。服务端由 `ros_adapter.py`、`app.py`、`session.py` 和 `status.py` 实现；CLI 客户端位于 `ros_control_client.py`。

## 接口配置

`demo/cfg.yaml` 及 `init` 导出的配置使用：

```yaml
ros:
  node_name: data_collect_dag
  control:
    control_topic: /data_collect/control
    status_topic: /data_collect/status
```

| 方向 | 接口 | 类型 | 用途 |
| --- | --- | --- | --- |
| 输入 | `/data_collect/control` | `std_msgs/String` | 接收控制命令（JSON 位于 `data`） |
| 输出 | `/data_collect/status` | `std_msgs/String` | 周期发布状态（JSON 位于 `data`） |

服务端 topic 名称由 YAML 指定；省略接口字段时不注册对应接口。CLI 的默认名称与 demo 一致，自定义名称通过 CLI 参数指定。`ros.control` 还可选填 `start_service`、`stop_service`、`status_service`（均为 `std_srvs/Trigger`）；未配置则不会注册。未知控制配置字段会被拒绝。

`start_service` 没有参数，用于启动程序启动时选择的 pipeline；如果要切换到其他 pipeline，应使用控制 topic。`stop_service` 的 `message` 为 `stopping`，`start_service` 的 `message` 为 `starting`；它们只表示命令已进入控制队列，不代表执行成功。`status_service` 的 `message` 为当前状态 JSON。

## 控制命令

发布到配置的 `control_topic`，`data` 必须是 JSON 对象字符串：

| 命令 | JSON 内容 | 行为 |
| --- | --- | --- |
| 启动/替换 | `{"command":"start","pipeline":"fps"}` | 先验证名称，再停止活动 session 并启动目标 pipeline |
| 暂停 | `{"command":"pause"}` | 暂停接收新帧及新样本执行 |
| 恢复 | `{"command":"resume"}` | 恢复暂停的 session |
| 停止 | `{"command":"stop"}` | 停止活动 session，程序继续运行，可再次 start |
| 退出 | `{"command":"shutdown"}` | 停止活动 session、保存摘要、释放资源并退出整个程序 |

`command` 必须为小写；`start` 的 `pipeline` 必须是非空字符串。非法 JSON、未知命令、缺失 pipeline 会被忽略并记录 warning，不会直接修改 `last_error`。合法命令执行失败时，错误信息写入 status 的 `last_error`。

收到 `start` 时先验证目标 pipeline 名称，存在才结束当前 session 并启动新 session。目标不存在时记录 `last_error`，保留活动 session 的运行或暂停状态及 session id，计数不重置。目标存在但初始化失败时，原 session 已结束，工具进入 `IDLE` 并报告错误。

CLI 可直接复用这些 topic，无需配置文件；默认 `/data_collect/control` 和 `/data_collect/status`。`run start`、`run pause`、`run resume`、`run stop` 及根级 `shutdown` 接受 `--control-topic TOPIC`；根级 `status` 接受 `--status-topic TOPIC`。四个 pipeline 控制命令只发送叶子动作 `start/pause/resume/stop`，JSON 协议保持不变：

```bash
data_collect_dag run start fps
data_collect_dag run pause
data_collect_dag run resume
data_collect_dag run stop
data_collect_dag status
data_collect_dag shutdown
```

`run launch --config CFG [--pipeline NAME] [--background]` 创建采集系统进程，默认 pipeline 为 `init`；`run start NAME` 仅向已有实例发送指令。单独执行 `run` 显示命令组帮助并返回 0。参数放在叶子命令之后。不存在运行实例时，控制客户端等待超时并返回失败，不会创建采集进程。自定义 topic 示例：

```bash
data_collect_dag run start fps --control-topic /vehicle/control --timeout 5
data_collect_dag status --status-topic /vehicle/status --timeout 5
```

客户端使用独立匿名 ROS 节点，不加载采集配置、标定、DAG 或推理模型。发送命令前检查 Master 并等待控制订阅者；`status` 等待状态消息及活动发布连接。`--timeout SEC` 为有限正数，默认 5 秒，分别用于 Master 检查和 topic 连接/消息等待。成功返回 0，超时、无目标接口或检测到发送失败返回 1，参数错误返回 2。

控制命令成功退出表示消息已发布；应用通过控制队列异步执行。`status` 读取一条现有状态 JSON，展示状态、pipeline、session、保存/跳过/失败计数、磁盘空闲空间及最近错误。例如下方 JSON 对应：

```text
State:       RUNNING
Pipeline:    fps
Session:     20261014_120000_abcd1234
Saved:       20
Skipped:     2
Failed:      0
Disk Free:   120.5 GB
Last Error:  None
```

也可直接使用 ROS 工具发送相同 JSON：

```bash
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"start\",\"pipeline\":\"fps\"}'"
```

## 生命周期与退出

采集程序获取本机实例锁后，加载配置和标定并启动 ROS 接口，再自动提交一次 `start`，默认 pipeline 为 `init`。锁按完整 `ros.node_name` 生成，前后台共用，`IDLE` 时也被持有；进程退出后释放。不同配置或输出目录不能绕过同名锁；完整流程见 [运行架构](architecture.md#1-运行路径)。

`pause`、`resume` 和 `stop` 在没有活动 session 时不启动采集。暂停阻止接收新帧及启动新样本；在途节点继续执行。`stop` 结束 session，保留程序及 ROS 控制接口。完成条件触发或 Pipeline 启动失败也保留程序，允许查询状态或重新 `start`。

`shutdown` 停止活动 session、保存摘要并退出整个程序。SIGINT/SIGTERM 通过同一控制队列收尾。正常收尾的摘要为 `STOPPED`，`end_reason` 分别为 `external_shutdown` 或 `signal_<信号编号>`；无活动 session 时直接退出。停止过程的节点线程池等待已提交任务，`runtime.stop_timeout_sec` 用于部分线程等待，并非整个退出流程的总时限。收尾失败记录 `FAILED` 和具体错误。退出后停止发布 status。

## 状态消息

`status_topic` 每约 **0.5 秒** 发布一次，`latch=True`；可通过以下命令订阅：

```bash
rostopic echo /data_collect/status
```

完整状态 JSON 示例：

```json
{
  "tool_state": "RUNNING",
  "recent_session_status": null,
  "current_session_id": "20261014_120000_abcd1234",
  "current_pipeline_name": "fps",
  "start_time": "2026-10-14T12:00:00.123456789",
  "disk_free_gb": 120.5,
  "output_size_gb": 25.3,
  "disk_storage_limit_gb": 100.0,
  "disk_storage_need_gb": 100.0,
  "last_error": null,
  "warnings": 0,
  "received_messages": {"front_ext_camera": 100},
  "main_frame_events": 100,
  "samples_saved": 20,
  "samples_skipped": 2,
  "samples_failed": 0,
  "samples_canceled": 0,
  "drop_reasons": {},
  "skip_reasons": {},
  "fail_reasons": {}
}
```

`StatusSnapshot.to_dict()` 的字段说明：

| 字段 | 含义 |
| --- | --- |
| `tool_state` | 当前状态：`IDLE`、`RUNNING` 或 `PAUSED` |
| `recent_session_status` | 最近结束 session 的结果：`STOPPED`、`COMPLETED`、`FAILED` 或 `null` |
| `current_session_id`、`current_pipeline_name`、`start_time` | 活动 session 信息；`IDLE` 时为 `null` |
| `disk_free_gb` | 输出目录所在文件系统的可用空间，查询失败时为 `null` |
| `output_size_gb` | 输出目录普通文件总大小的缓存值；未启用限额统计或查询失败时为 `null` |
| `disk_storage_limit_gb`、`disk_storage_need_gb` | 配置的目录容量上限和启动空闲空间门槛；关闭时为 `null` |
| `last_error` | 最近一次控制或运行错误 |
| `warnings` | 警告计数 |
| `received_messages` | 活动 session 接入的每个 `topic_key` 的消息数量；暂停或未绑定 session 时不计入 |
| `main_frame_events` | 已进入主帧队列的事件数 |
| `samples_saved`、`samples_skipped`、`samples_failed`、`samples_canceled` | 样本计数 |
| `drop_reasons`、`skip_reasons`、`fail_reasons` | 按原因归类的计数 |

`RUNNING`、`PAUSED` 是活动状态；`STOPPED`、`COMPLETED`、`FAILED` 属于最近 session 的最终结果。状态切换至 `IDLE` 后，当前 session 的三个字段被清空，计数仍保留到下一次成功启动。成功启动重置计数和 `last_error`，但 `recent_session_status` 保留最近结束结果；不能据此字段单独判断当前是否运行。`start_time` 为 session 构造时的本机时间。状态 topic 会向新订阅者发送最近一条 latched 消息；不能单凭发布命令成功或一条缓存状态判断命令已执行，应等待活动状态/session id 变化并检查 `last_error`。

容量字段使用十进制 GB；`disk_free_gb` 和 `output_size_gb` 保留两位小数，配置门槛保留配置数值。`output_size_gb` 使用缓存，状态查询不扫描输出目录。容量检查规则见 [YAML 配置](architecture.md#2-yaml-配置)。

容量拒绝启动时保持 `IDLE`，最近结果为 `FAILED`，不创建该会话目录。运行中达到容量上限或检查失败时，停止新样本并收尾，进入 `IDLE` / `FAILED`。错误使用 `storage_limit_reached`、`insufficient_disk_space` 或 `storage_check_failed` 标识；运行中失败的摘要也记录对应 `end_reason`。释放空间后可显式 `start` 重试。

## 启动前置检查与错误边界

配置的每个 `ros.topics` 在 adapter 启动时都要能解析成 ROS message class，包括自定义消息包。先安装 ROS Noetic 和对应 catkin package，并正确 `source` ROS 工作空间；ROS master 地址由 `ROS_MASTER_URI` 指定，默认 `http://localhost:11311`，采集启动时的 Master 探测等待约 5 秒。

当 pipeline 同时配置 `topics` 和 `time_delay_ms` 时，每次 session 启动前都会检查这些 topic 是否在指定毫秒内各收到一条新消息。超时后本次 session 启动失败，`last_error` 会包含缺失的 topic。没有配置这两个字段时，跳过该检查。

实例锁获取、YAML 解析、标定加载、Master 连接和 message class 解析发生在状态接口就绪之前；失败时启动进程返回非零，通过终端输出或后台日志诊断。后台父进程的零退出码及 PID 只表示子进程已创建。

ROS 接口就绪后的 Pipeline 容量检查、topic 预检查和节点初始化错误通过 `last_error` 及进程日志报告；进程继续接受控制。`init`、`inspect` 和 `clean` 不启动 ROS 接口。

`inspect --config CFG [--pipeline NAME]` 只读取并静态校验整个配置，不查询运行实例，也不检查 topic 在线情况、消息包、标定/模型加载或 CUDA。查看当前实例使用 `status`；离线配置摘要及错误边界见 [配置检查](../README.md#inspect-configuration-offline)。

## 读取输出

会话输出目录：

```text
<output.root_dir>/<session_id>/
  debug.log
  saved_samples.json
  session_summary.json
  xtreme1/<dataset_name>/...  # 仅在相应保存节点运行时生成
```

`session_summary.json` 包含 `end_status`（`STOPPED`、`COMPLETED` 或 `FAILED`）、`end_reason`、`metrics`、`last_error`、`save_outputs` 等字段。`saved_samples.json` 顶层为 `{"items": [...]}`。读取完整采集结果应等待工具进入 `IDLE`，再依据已记录的 session id 查找输出目录。

关于采集 DAG、节点和 YAML 的完整字段参见 [运行架构与配置](architecture.md)。
