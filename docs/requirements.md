# 数据采集 DAG 工具需求文档

## 1. 项目目标

开发一个用于数据采集的 DAG 工具。该工具能够根据配置运行指定的数据采集流程，并具备与外部 ROS1 系统通讯的能力。

本工具的核心定位是数据采集 DAG 工具，不是以 ROS node 为中心设计的业务节点。当启用 ROS1 通讯能力时，工具允许在 ROS graph 中以 ROS node 形式参与通讯。

工具应支持长期运行，允许丢弃部分数据，但不得因为数据处理、推理或写盘阻塞外部数据接收。

---

## 2. 使用场景

工具主要用于自动驾驶、机器人或移动平台的数据采集任务。

典型使用场景包括：

1. 手动启动指定采集流程。
2. 通过外部 ROS1 系统启动指定采集流程。
3. 从 ROS1 topic 接入点云、图像、定位、里程计数据。
4. 根据配置执行单入口、单出口、无环的数据处理流程。
5. 将采集数据保存为结构化数据。
6. 根据处理节点结果决定是否保存数据。
7. 支持不同业务场景配置不同采集流程。

---

## 3. 系统边界

本工具第一阶段以独立前台程序形式运行。

ROS1 在本工具中的职责包括：

1. 接收外部 ROS1 指令。
2. 订阅 ROS1 topic。
3. 对 ROS message 进行轻量包装。
4. 发布状态或结果到 ROS1。
5. 提供 ROS1 service 或 topic 形式的控制接口。

ROS 相关逻辑只用于通讯、轻量数据包装和数据发布，不用于耗时数据处理流程。

以下能力属于 DAG 中具体处理节点的功能：

1. 时间同步。
2. 点云处理。
3. 图像处理。
4. 模型推理。
5. 筛选。
6. 结构化数据保存。
7. 其他业务处理逻辑。

---

## 4. 启动与控制需求

工具应支持两种启动采集流程的方式：

1. CLI 手动启动。
2. 外部 ROS1 系统指令启动。

### 4.1 CLI 启动

工具应支持通过命令行参数指定配置文件和 pipeline 并立即运行。

命令形式示例：

```bash
data_collect_dag --config config.yaml --pipeline data_collect_normal
```

如果未指定结束条件，该 pipeline 可持续运行，直到收到停止信号或发生终止条件。

CLI 启动 pipeline 后，工具仍应能够监听 ROS start / stop / status 指令。

### 4.2 ROS 指令启动

工具应支持通过 ROS topic 或 ROS service 接收外部 start 指令。

Start 指令用于启动指定 pipeline。

Start 指令至少应包含：

```text
pipeline 名称
可选运行参数
```

外部系统只指定要启动的采集流程，不维护 session 生命周期。

### 4.3 停止方式

工具以前台程序运行时，应支持通过 Ctrl+C 停止当前 session。

工具也应支持通过外部 ROS stop 指令停止当前 session。

Ctrl+C 和 ROS stop 均属于主动停止。session 成功响应主动停止后，最近 session 状态应为 STOPPED。

收到停止请求后：

1. 当前 session 应停止接收新样本。
2. 未完成样本应取消。
3. 已由保存节点明确返回成功的样本应计入已保存样本。
4. 最近 session 状态应记录为 STOPPED。

如果用户使用 nohup、systemd、supervisor、Docker 等方式将程序放到后台运行，则 Ctrl+C 不再适用，应依赖外部 stop 指令或进程管理工具停止。

### 4.4 Status 指令

Status 指令仅在工具与外部 ROS 系统交互时起作用。

工具应支持通过 ROS topic 或 ROS service 查询或发布状态。

CLI 前台运行时，可通过日志或控制台输出体现运行状态，不强制要求额外 status 指令。

---

## 5. Session 需求

一次采集任务称为一个 session。

每个 session 由工具内部生成唯一 session_id。

session_id 用于：

1. session_root 命名。
2. 状态发布。
3. summary 记录。
4. 日志记录。
5. 异常定位。

同一时刻只允许一个 session 运行。

无论当前 session 是由 CLI 启动还是 ROS 指令启动，只要收到新的 start 指令，均按 session 替换处理。

当已有 session 正在运行时收到新的 start 指令：

1. 工具应产生 warning。
2. 当前 session 应进入 stopping。
3. 当前 session 不再接收新样本。
4. 当前 session 中未完成样本应取消。
5. 新 session 应在旧 session 清理完成后启动。
6. 旧 session 的结束原因应记录为 replaced_by_new_start。
7. 旧 session 的最近 session 状态应记录为 STOPPED。

不同 session 的输出数据必须能够区分，不得发生数据归属混淆。

---

## 6. 状态需求

工具状态分为：

```text
工具运行状态
最近 session 状态
```

工具运行状态只包含：

```text
IDLE
RUNNING
```

最近 session 状态包含：

```text
STOPPED
COMPLETED
FAILED
```

含义：

```text
IDLE：当前无 session 正在运行。
RUNNING：当前有 session 正在运行。
STOPPED：最近 session 响应主动停止或被新 start 替换后成功结束。
COMPLETED：最近 session 因满足结束条件而正常结束。
FAILED：最近 session 异常结束。
```

当 session 结束后，工具运行状态应回到 IDLE，同时保留最近 session 状态。

状态信息至少应包含：

```text
工具运行状态
最近 session 状态
当前 session_id
当前 pipeline 名称
开始时间
最近错误
warning 数量
已接收数据数量
进入 DAG 的样本数量
已保存样本数量
已跳过样本数量
已失败样本数量
已取消样本数量
已丢弃数据数量
drop 原因统计
skip 原因统计
fail 原因统计
```

---

## 7. 输入数据需求

工具第一阶段应支持从 ROS1 系统接入以下数据。

### 7.1 点云

```text
sensor_msgs/PointCloud2
```

### 7.2 图像

```text
sensor_msgs/Image
sensor_msgs/CompressedImage
```

### 7.3 定位

```text
geometry_msgs/PoseStamped
nav_msgs/Odometry
```

第一阶段不要求支持自定义 localization message。

### 7.4 里程计

```text
nav_msgs/Odometry
```

### 7.5 标定与固定参数

第一阶段不要求支持 sensor_msgs/CameraInfo。

相机内参、畸变参数、外参、固定坐标变换等参数可由具体处理节点按需从本地固定参数文件读取。

---

## 8. 数据接入与缓存需求

工具应对外部输入数据进行 session 级有限缓存。

缓存只在当前 session RUNNING 期间有效。

IDLE 状态不缓存输入数据。

当前 session 不使用 start 前的数据。

数据进入 session cache 必须满足：

1. receive_timestamp >= session_start_time。
2. 如果存在 source_timestamp，则 source_timestamp >= session_start_time。

不满足时间边界的数据应丢弃，并记录 drop_reason 为 before_session_start。

缓存应按 topic_key 建立独立 ring buffer。

缓存配置应支持按 role 设置默认值，并支持按 topic_key 覆盖。

示例：

```yaml
cache:
  defaults_by_role:
    pointcloud:
      max_frames: 5
      max_age_sec: 1.0
    image:
      max_frames: 5
      max_age_sec: 1.0
    odometry:
      max_frames: 100
      max_age_sec: 5.0
    pose:
      max_frames: 100
      max_age_sec: 5.0
  topic_overrides:
    top_lidar:
      max_frames: 10
      max_age_sec: 1.0
```

max_frames 始终表示单个 topic_key 的缓存帧数上限。

缓存满时应丢弃最旧数据，并记录丢弃原因。

外部数据接收路径只应完成：

```text
接收数据
提取必要 metadata
包装为内部 FrameData
写入 session cache
必要时投递主帧事件
快速返回
```

外部数据接收路径不得执行：

```text
模型推理
点云解析为 numpy
图像解码或压缩
复杂同步逻辑
大文件写盘
```

---

## 9. DAG 流程需求

工具应支持通过配置文件定义不同的数据采集 DAG 流程。

一个采集流程应由以下元素组成：

```text
主帧来源
输入数据
处理节点
节点依赖关系
结束条件
输出方式
运行参数
```

采集流程应为单入口、单出口、无环处理图。

处理图应满足：

```text
只允许一个起点
只允许一个终点
允许分支
允许汇合
允许异步执行
不允许环
不允许多个入口
不允许多个最终出口
```

DAG 应保留显式 start 节点和 end 节点。

结构化保存节点、metadata 保存节点、调试保存节点等可以同时存在，但它们不等同于 DAG 的最终终点。DAG 最终仍应汇合到唯一 end 节点。

工具应支持由 pipeline 配置定义主帧来源。第一阶段至少应支持图像主帧。

一个主帧事件对应一个 SampleContext。

样本数据可在 DAG 执行过程中由同步节点逐步补齐。

---

## 10. DAG 执行需求

DAG 执行器应使用 ready queue 调度节点。

节点只有在所有前置节点都返回 OK 后才允许执行。

第一阶段应支持样本内节点并发执行。

并发参数至少包括：

```yaml
runtime:
  sample_workers: 1
  node_workers: 4
  main_frame_queue_size: 20
  stop_timeout_sec: 5.0
```

含义：

```text
sample_workers：样本间并发数。
node_workers：当前 session 的节点执行线程池大小。
main_frame_queue_size：主帧事件队列容量。
stop_timeout_sec：协作式取消等待告警阈值。
```

第一版默认：

```text
sample_workers = 1
node_workers = 4
```

第一版支持样本内并发，默认不启用样本间并发。

NodeWorkerPool 属于当前 SessionRuntime，session 结束时释放，不同 session 不共享 NodeWorkerPool。

保存节点必须通过 edges 显式依赖所有会影响保存决策的节点。

---

## 11. 节点依赖需求

处理流程中的每个节点应具有唯一 node_id。

node_id 用于：

```text
配置引用
依赖关系表达
日志记录
错误定位
状态追踪
```

节点依赖关系应通过显式 edges 表达。

edges 只表达执行依赖和 gating 依赖，不表达具体数据内容。

如果某个节点依赖多个前置节点，则该节点必须等待所有前置节点返回 OK 后才能执行。

任意节点返回非 OK 后，当前 sample 不再调度新节点，并通知当前 sample 中其他运行中节点协作式取消。

---

## 12. 数据传递需求

DAG 内部应使用工具内部数据对象，不直接依赖 ROS message 类型。

内部数据对象至少包括：

```text
FrameMeta
ImageFrame
PointCloudFrame
OdometryFrame
PoseFrame
SampleContext
```

ImageFrame 和 PointCloudFrame 应保留 raw_msg_ref。

图像解码和点云解析由具体节点按需执行。

处理节点之间应通过 SampleContext 传递数据。

SampleContext 应满足：

1. 只作为样本级数据容器。
2. 不管理生命周期状态。
3. 不承载 cache、output_root、logger、metrics 等 session 级资源。
4. 支持 context key + producer 的数据读写。
5. 只保存数据引用，避免无意义复制点云、图像等大对象。
6. 在样本内并发执行时保证 data / metadata 容器结构线程安全。
7. 不保证 value 对象内部状态线程安全。

节点写入 context 后，不应原地修改已写入的大对象。需要修改时，应生成新的对象或新的引用，并写入新的 context key。

---

## 13. 节点接口需求

工具应提供基础节点接口。

基础节点接口应包含：

```text
setup()
run(sample)
teardown()
```

BaseNode 构造时应注入：

```text
node_id
node_type
inputs
outputs
config
SessionRuntime
```

node.run() 只接收 SampleContext。

节点通过 self.session 访问 session 级资源。

第一阶段不设计 NodeRuntimeContext。

节点返回值应包含：

```text
OK
SKIP_SAMPLE
FAIL_SAMPLE
FAIL_SESSION
CANCEL_SESSION
```

语义：

```text
OK：节点成功，继续执行后继节点。
SKIP_SAMPLE：当前样本跳过，session 继续。
FAIL_SAMPLE：当前样本失败，session 继续。
FAIL_SESSION：当前 session 失败。
CANCEL_SESSION：当前 session 或当前 sample 被取消。
```

---

## 14. 同步节点需求

工具应允许在 DAG 中配置时间同步类节点。

时间同步类节点可用于：

```text
以图像为主帧匹配点云
以点云为主帧匹配图像
匹配定位信息
匹配里程计信息
筛选时间差满足要求的数据
```

同步节点应从 SessionInputCache 中查询或短暂等待其他 topic 数据。

同步节点至少应支持：

```text
nearest
latest_before
```

第一阶段不要求支持定位或里程计插值。

sync node 的 required 表示该同步节点匹配失败时是否跳过当前样本。

required=true 且匹配失败时，节点应返回 SKIP_SAMPLE。

required=false 且匹配失败时，节点应继续执行，并记录 warning 或 metadata。

---

## 15. 处理节点能力需求

工具应允许通过配置选择不同类型的处理节点。

处理节点可用于完成以下类别的功能：

```text
数据解析
时间同步
点云处理
图像处理
模型推理
数据筛选
数据保存
状态统计
结果发布
```

每类功能的详细行为由具体处理节点定义。

第一阶段不要求实现完整的点云处理、图像处理、模型推理节点体系。

---

## 16. 筛选类节点需求

工具应允许在 DAG 中配置筛选类节点。

筛选类节点可根据以下信息产生筛选结果：

```text
图像质量
点云点数
点云距离范围
检测类别
检测分数
检测数量
保存频率
正样本保存策略
负样本抽样保存策略
其他自定义条件
```

筛选结果应能被后续节点使用。

第一阶段不要求实现复杂规则引擎。

筛选节点可通过返回 SKIP_SAMPLE 终止当前样本后续执行。

---

## 17. 保存节点需求

SaveNode 是保存能力扩展点。

第一版 MVP 只实现样本级结构化保存节点。

第一版不实现 rosbag_save 节点。

rosbag_save 可作为后续 SaveNode 类型接入。

保存节点应能够根据上游筛选结果决定是否保存当前样本。

保存节点应支持配置 required_inputs 与 optional_inputs。

save node 的 required_inputs 表示保存节点生成有效样本所需的最小输入集合。

当 required_inputs 缺失时，保存节点应返回 SKIP_SAMPLE，不应计入 samples_saved。

保存节点职责：

1. 从 SampleContext 读取配置指定的输入。
2. 校验 required_inputs。
3. optional_inputs 缺失时继续执行。
4. 决定保存目录结构。
5. 保存 bin / jpg / json 等样本文件。
6. 写 sample metadata。
7. 通过 sample.metadata["save_result"] 上报保存结果。
8. 上报 save_outputs 到 session_summary.json。

保存节点不负责写 session_summary.json。

---

## 18. 结构化数据保存需求

结构化数据格式为：

```text
bin + jpg + json
```

结构化数据保存节点应支持按样本组织数据。

具体目录结构由保存节点定义。

框架为每个 session 创建 session_root。

session_summary.json 固定写入 session_root。

保存节点在 session_root 下创建自身业务输出目录。

结构化数据可保存：

```text
点云
图像
时间戳
定位信息
里程计信息
推理结果
筛选结果
样本 metadata
```

结构化样本 metadata 可记录：

```text
传感器名称
topic 名称
时间戳
frame_id
样本主时间
数据文件路径
定位或里程计信息
筛选结果
推理结果
```

第一版保存节点直接写正式输出目录。

第一版不保证样本级原子提交。

第一版不强制清理保存过程中残留的未完成文件。

samples_saved 只统计保存节点明确返回 OK 的样本。

下游判断有效样本时，应依据保存节点 metadata、索引或 session_summary.json，而不是仅扫描文件是否存在。

---

## 19. 配置文件需求

工具只使用一个 YAML 配置文件。

配置文件应至少包含：

```text
基础参数
ROS 通讯配置
缓存配置
pipeline 配置
输出配置
状态发布配置
```

Pipeline 配置应能表达：

```text
main_source
nodes
edges
end_condition
```

工具启动或加载配置时，应对配置文件进行校验。

配置校验至少包括：

```text
pipeline 名称存在
node_id 唯一
edges 引用的节点存在
处理图无环
处理图只有一个 start 和一个 end
所有节点从 start 可达
所有节点可到达 end
节点 type 可识别
节点 inputs / outputs 格式合法
```

第一阶段要求启动时完整校验配置文件中的所有 pipeline；任一 pipeline 配置结构错误，工具启动失败。

资源可用性错误在启动具体 pipeline 时检查，失败则当前 session FAILED。

资源可用性错误包括：

```text
模型文件不存在
输出目录不可写
session_root 创建失败
保存节点初始化失败
依赖库或推理 runtime 初始化失败
```

---

## 20. 结束条件需求

工具应支持以下 session 结束条件：

```text
外部 stop 指令
Ctrl+C
运行时长达到配置值
样本数量达到配置值
采集流程主动结束
发生致命错误
被新的 start 指令替换
```

结束状态规则：

```text
外部 stop 指令结束：STOPPED
Ctrl+C 结束：STOPPED
被新的 start 指令替换：STOPPED
运行时长达到配置值：COMPLETED
样本数量达到配置值：COMPLETED
采集流程主动正常结束：COMPLETED
发生致命错误：FAILED
setup 失败：FAILED
保存致命错误：FAILED
```

---

## 21. Summary 需求

每个 session 结束后应生成 session_summary.json。

session_summary.json 固定写入 session_root。

session_summary.json 至少包含：

```text
session_id
pipeline 名称
开始时间
结束时间
结束状态
结束原因
配置文件路径
session_root
pipeline 参数
save_outputs
last_error
warnings
```

还应包含：

```text
接收数据数量
缓存丢弃数量
主帧事件数量
主帧事件丢弃数量
进入 DAG 的样本数量
保存样本数量
失败样本数量
跳过样本数量
取消样本数量
warning 数量
error 数量
drop 原因统计
skip 原因统计
fail 原因统计
```

如包含推理或筛选流程，应支持记录：

```text
筛选前样本数量
筛选后样本数量
各类筛选原因统计
推理命中数量
主要类别命中统计
```

---

## 22. 异常处理需求

处理失败应由对应节点或流程定义处理策略。

工具应支持以下失败处理结果：

```text
忽略错误并继续
跳过当前样本
当前样本失败
当前 session 失败
取消当前 session
```

基本要求：

```text
单帧失败不应导致工具退出
模型加载失败可导致当前 session 失败
保存致命错误可导致当前 session 失败
session 被替换时未完成样本应取消
长期运行时异常不应导致工具无控制退出
```

默认策略：

```text
required input 缺失：SKIP_SAMPLE
optional input 缺失：继续执行，记录 warning 或 metadata
普通处理节点异常：FAIL_SAMPLE
保存节点异常：FAIL_SESSION
node.setup 失败：session FAILED
stop / replace：CANCEL_SESSION
```

stop / replace 使用协作式取消，不强杀线程。

stop_timeout_sec 是协作式取消等待告警阈值，不用于强杀线程，也不用于释放仍被运行节点使用的资源。

---

## 23. 长期运行需求

工具应满足长期运行要求：

```text
外部数据接收不被处理流程阻塞
缓存容量有上限
队列容量有上限
处理失败可控
状态可查询或发布
session 结束后资源可释放
数据保存失败可统计
允许丢帧
不要求严格实时性
```

工具应具备基本可观测性，至少包括：

```text
运行日志
状态查询或发布
session_summary.json
错误原因记录
drop / skip / fail 原因统计
每个 session 的配置记录
```

---

## 24. 技术约束

工具主体使用 Python 实现。

当前外部通讯环境：

```text
Ubuntu 20.04
ROS1
Python
```

未来目标外部通讯环境：

```text
Ubuntu 22.04
ROS2
Python
```

工具应允许在性能不足时接入 C++ 实现的处理节点。

---

## 25. 非目标

第一阶段不要求支持：

```text
rosbag_save 节点
ROS2 通讯适配
多个 session 并发运行
多个 source
多个 sink
有环处理图
运行时修改处理图
分布式调度
跨机器任务编排
复杂规则引擎
完整任务管理平台
外部 task 生命周期管理
sensor_msgs/CameraInfo 接入
自定义 localization message 接入
完整模型推理节点体系
C++ 节点接入
```

---

## 26. 最小可用版本需求

最小可用版本应满足：

```text
独立前台程序运行
支持 CLI 指定 pipeline 启动
支持 CLI 启动后监听 ROS 指令
支持 ROS1 通讯
支持 ROS start / stop / status 交互
支持 Ctrl+C 停止
支持一个 YAML 配置文件
支持配置启动校验
支持 session_id 自动生成
支持 session_root 自动创建
支持 session 替换
支持工具运行状态与最近 session 状态
支持点云、图像、定位、里程计缓存
支持按 topic_key 建立 session 级 ring buffer
支持主帧触发样本级 DAG
支持样本内并发 DAG
支持显式 edges 定义 DAG 流程
支持单入口、单出口、无环处理图
支持 SampleContext 数据传递
支持多个节点写入同一个 context key
支持结构化数据保存节点
支持按样本组织结构化数据
支持 session_summary.json
支持 drop / skip / fail 原因统计
支持协作式取消
支持长期运行
```
