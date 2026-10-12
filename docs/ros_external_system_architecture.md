# ROS1 外部控制接口

DataCollectDAG 在 ROS1 Noetic 中作为一个 ROS node 运行。外部系统通过 `std_msgs/String` topic 发送 JSON 控制命令、订阅 JSON 状态；采集结果由文件系统交付。相关实现位于 `src/data_collect_dag/ros_adapter.py`、`app.py`、`session.py` 和 `status.py`。

## 接口配置

示例 `demo/cfg.yaml`、`demo/xtreme1_demo.yaml` 使用：

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
| 停止 | `{"command":"stop"}` | 停止活动 session |

`command` 必须为小写；`start` 的 `pipeline` 必须是非空字符串。非法 JSON、未知命令、缺失 pipeline 会被忽略并记录 warning，不会直接修改 `last_error`。合法命令执行失败时，错误信息写入 status 的 `last_error`。

如果收到 `start` 时已经存在活动 session，会**先结束当前 session，再验证目标 pipeline**。因此目标名称错误时，旧 session 仍然会被停止，工具随后保持 `IDLE`。

命令示例（根据实际 YAML 调整 topic 和 pipeline）：

```bash
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"start\",\"pipeline\":\"fps\"}'"
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"pause\"}'"
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"resume\"}'"
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"stop\"}'"
```

CLI 启动后也会自动提交一次 `start`。它不会因某次 start 或节点初始化失败而自动退出；可以检查状态并发送新的 `start`。收到 `SIGINT`/`SIGTERM` 时，应用执行停止与清理并退出。

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
  "current_session_id": "session-id",
  "current_pipeline_name": "fps",
  "start_time": "2026-10-12T09:00:00",
  "disk_free_gb": 120.5,
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
| `last_error` | 最近一次控制或运行错误 |
| `warnings` | 警告计数 |
| `received_messages` | 每个 `topic_key` 的接收数量 |
| `main_frame_events` | 已进入主帧队列的事件数 |
| `samples_saved`、`samples_skipped`、`samples_failed`、`samples_canceled` | 样本计数 |
| `drop_reasons`、`skip_reasons`、`fail_reasons` | 按原因归类的计数 |

`RUNNING`、`PAUSED` 是活动状态；`STOPPED`、`COMPLETED`、`FAILED` 属于最近 session 的最终结果。状态切换至 `IDLE` 后，当前 session 的三个字段被清空，计数仍保留到下一次成功启动。不能单凭消息发布成功判断命令已执行，应等待状态变化并检查 `last_error`。

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
