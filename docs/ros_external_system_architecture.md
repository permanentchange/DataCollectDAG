# ROS1 外部控制接口

DataCollectDAG 在 ROS1 Noetic 中作为一个 ROS node 运行。外部系统通过 `std_msgs/String` topic 发送 JSON 控制命令、订阅 JSON 状态；采集结果由文件系统交付。相关实现位于 `src/data_collect_dag/ros_adapter.py`、`app.py`、`session.py` 和 `status.py`。

## 接口配置

`demo/cfg.yaml` 及 `--init-demo` 导出的配置使用：

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

这些名称来自 YAML，不是硬编码的强制值。`ros.control` 还可选填 `start_service`、`stop_service`、`status_service`（均为 `std_srvs/Trigger`）；未配置则不会注册。未知控制配置字段会被拒绝。

`start_service` 没有参数，启动 **CLI 最初指定的 pipeline**；如果要切换到其他 pipeline，应使用控制 topic。`stop_service` 的 `message` 为 `stopping`，`start_service` 的 `message` 为 `starting`；它们只表示命令已进入控制队列，不代表执行成功。`status_service` 的 `message` 为当前状态 JSON。

## 控制命令

发布到配置的 `control_topic`，`data` 必须是 JSON 对象字符串：

| 命令 | JSON 内容 | 行为 |
| --- | --- | --- |
| 启动/替换 | `{"command":"start","pipeline":"fps"}` | 启动配置中存在的 pipeline；若已有 session，先结束旧 session |
| 暂停 | `{"command":"pause"}` | 暂停接收新帧及新样本执行 |
| 恢复 | `{"command":"resume"}` | 恢复暂停的 session |
| 停止 | `{"command":"stop"}` | 停止活动 session，程序继续运行，可再次 start |
| 退出 | `{"command":"shutdown"}` | 停止活动 session、保存摘要、释放资源并退出整个程序 |

`command` 必须为小写；`start` 的 `pipeline` 必须是非空字符串。非法 JSON、未知命令、缺失 pipeline 会被忽略并记录 warning，不会直接修改 `last_error`。合法命令执行失败时，错误信息写入 status 的 `last_error`。

如果收到 `start` 时已经存在活动 session，会**先结束当前 session，再验证目标 pipeline**。因此目标名称错误时，旧 session 仍然会被停止，工具随后保持 `IDLE`。

命令示例（根据实际 YAML 调整 topic 和 pipeline）：

```bash
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"start\",\"pipeline\":\"fps\"}'"
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"pause\"}'"
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"resume\"}'"
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"stop\"}'"
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"shutdown\"}'"
```

采集 CLI 在 ROS adapter 启动后自动提交一次 `start`，默认 pipeline 为 `init`。它不会因某次 start 或节点初始化失败而自动退出；可以检查状态并发送新的 `start`。收到 `SIGINT`/`SIGTERM` 或 ROS `shutdown` 时，应用通过控制队列执行停止与清理并退出；正常收尾时，ROS shutdown 的摘要 `end_reason` 为 `external_shutdown`，信号退出为 `signal_<信号编号>`，最终状态为 `STOPPED`。shutdown 不需要 pipeline 参数，对前台、`--background` 后台、暂停或无活动 session 的程序均有效。退出后停止发布 status。`--init-demo` 和 `--clean` 不创建这些 ROS 接口。

`pause` / `resume` / `stop` 在没有活动 session 时不启动采集。暂停不会强制中断已经执行的节点；`stop` 或 `shutdown` 会请求取消并执行收尾。节点线程池退出仍等待已提交任务，`runtime.stop_timeout_sec` 不是整个退出流程的总时限；收尾失败会报告 `FAILED` 和具体错误。

## 状态消息

`status_topic` 每约 **0.5 秒** 发布一次，`latch=True`；可通过以下命令订阅：

```bash
rostopic echo /data_collect/status
```

JSON 示例（省略部分空计数项的含义与空对象相同）：

```json
{
  "tool_state": "RUNNING",
  "recent_session_status": null,
  "current_session_id": "20261013_090000_abcdef12",
  "current_pipeline_name": "fps",
  "start_time": "2026-10-13T09:00:00.123456789",
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
| `received_messages` | 每个 `topic_key` 的接收数量 |
| `main_frame_events` | 已进入主帧队列的事件数 |
| `samples_saved`、`samples_skipped`、`samples_failed`、`samples_canceled` | 样本计数 |
| `drop_reasons`、`skip_reasons`、`fail_reasons` | 按原因归类的计数 |

`RUNNING`、`PAUSED` 是活动状态；`STOPPED`、`COMPLETED`、`FAILED` 属于最近 session 的最终结果。状态切换至 `IDLE` 后，当前 session 的三个字段被清空，计数仍保留到下一次成功启动。成功启动重置计数和 `last_error`，但 `recent_session_status` 保留最近结束结果；不能据此字段单独判断当前是否运行。`start_time` 使用本机时间。状态 topic 会向新订阅者发送最近一条 latched 消息；不能单凭发布命令成功或一条缓存状态判断命令已执行，应等待活动状态/session id 变化并检查 `last_error`。

容量字段使用十进制 GB；`disk_free_gb`、`output_size_gb` 展示值保留两位小数，配置门槛保留配置数值，限额比较使用未舍入的字节数。启动时目录达到上限或空闲空间不足，将保持 `IDLE` 并报告 `FAILED`，不创建会话目录或该会话的摘要。`last_error` 以 `storage_limit_reached`、`insufficient_disk_space` 或 `storage_check_failed` 标识原因，并包含路径和容量或查询错误。

运行中达到目录容量上限或检查失败后，停止新样本并等待在途样本完成，随后进入 `IDLE` / `FAILED`；摘要 `end_reason` 为 `storage_limit_reached` 或 `storage_check_failed`。在途数据及收尾文件可能造成少量超额。ROS 控制和 status 发布继续工作，释放空间后可显式发送 `start` 重新检查；不会自动恢复。`disk_storage_need_GB` 仅在每次新任务开始时检查，运行中和暂停恢复不作为停止条件。

## 启动前置检查与错误边界

配置的每个 `ros.topics` 在 adapter 启动时都要能解析成 ROS message class，包括自定义消息包。先安装 ROS Noetic 和对应 catkin package，并正确 `source` ROS 工作空间；默认 ROS master 地址为 `http://localhost:11311`，代码最多等待约 5 秒。

当 pipeline 同时配置 `topics` 和 `time_delay_ms` 时，每次 session 启动前都会检查这些 topic 是否在指定毫秒内各收到一条新消息。超时后本次 session 启动失败，`last_error` 会包含缺失的 topic。没有配置这两个字段时，跳过该检查。

**注意：** YAML 解析、标定 JSON 加载、ROS master 连接和 ROS message class 解析发生在状态接口就绪之前。此阶段的失败要通过进程日志诊断，不能依赖 `status_topic`。

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
