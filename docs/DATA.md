# 数据说明

位置动作另见[CONTROL.md](CONTROL.md)。`/api/state`现含`control`会话、软件锁、电机回读状态及动作进度；`read_only`表示本会话软件控制是否锁定，不表示电机已失能。

本项目支持 OmniHand 2025 O10 的 USB 和达妙 CAN FD 读取，不把不支持的字段填成零。以下 SDK getter 表用于 USB；CAN FD 字段差异见本页末尾。

| 字段 | SDK 方法 | 本次返回 / 含义 |
| --- | --- | --- |
| `device.model` | `get_vendor_info()` | OmniHand 2025 |
| `device.hardware_version` / `firmware_version` | `get_vendor_info()` | 硬件 / 固件版本字符串 |
| `device.voltage_mv` | 同上 | 电压 mV；连接时读取一次，不是持续电压曲线 |
| `positions` | `get_all_joint_positions()` | 10 个整数，协议位置值；不是角度 |
| `diagnostics.current_ma` | `get_all_current_reports()` | 10 路电机电流，mA；不是电源总输入电流 |
| `diagnostics.temperature_c` | `get_all_temperature_reports()` | 10 路电机温度，℃ |
| `diagnostics.motor_errors` | `get_all_error_reports()` | 10 路错误结构，分别保留五个布尔标记 |
| `tactile[区域].values` | `get_tactile_sensor_data(Finger.区域)` | 普通降采样触觉整数数组 |

错误标记为 `stalled`（堵转）、`overheat`（过热）、`over_current`（过流）、`motor_except`（电机异常）、`commu_except`（通信异常）。无错误标记只说明查询时设备未置位这些标记。

## 10 轴编号

按官方 O10 关节 I/O 表展示：1 拇指旋转、2 拇指侧摆、3 拇指屈伸、4 食指侧摆、5 食指屈伸、6 中指屈伸、7 无名指侧摆、8 无名指屈伸、9 小指侧摆、10 小指屈伸。

SDK 的单电机接口使用 1–10；Python 数组仍按 0–9 索引。网页显示 01–10，避免直接混用。协议位置值完整保留，三维角度另由官方求解器换算；没有进行力矩换算。

位置名义范围 0–4096，实测存在 4116、4125 等轻微越界反馈。本项目保留真实值；进度条只在绘图时限制显示宽度。没有将越界反馈解释为硬件错误，也没有据此改动校准参数。

## 普通触觉

| 区域 | 本次实测点数 |
| --- | ---: |
| THUMB / 拇指 | 16 |
| INDEX / 食指 | 16 |
| MIDDLE / 中指 | 16 |
| RING / 无名指 | 16 |
| LITTLE / 小指 | 16 |
| PALM / 手心 | 25 |
| DORSUM / 手背 | 25 |
| 合计 | 130 |

网页根据实际数组长度绘图，点序沿用 SDK 输出；官方相对索引图或索引表不代表已标定的物理坐标。固定色阶为 0–255，自动色阶在七区域间统一；颜色没有完成定量力值标定。

官方示例把区域求和打印为 g，但本项目尚未完成力值校准、量纲与接触载荷核对，因此界面保留“触觉值”，不把求和当作经验证的克重或牛顿值。当前未验证按压响应、空间顺序、标定精度。

普通触觉与全分辨率原始触觉是两个 API 路径。USB 直连路径读取普通触觉；达妙 CAN FD 路径现已验证原始 268 点，与普通 130 点分开保存。普通 USB `get_device_info()` 可能返回默认字段，因此界面不以其中的零值作为实测 CAN 参数。

## 达妙 CAN FD 字段差异（2026-11-16）

- CAN FD 通过达妙官方 SDK 收发标准帧、FD+BRS，使用 O10 公开协议，不依赖智元 SDK 内置的 ZLG/HCAN 后端。
- CAN 节点是实测 `0x09`，与 USB 工厂传入的设备 ID 1 不等同。物理 CAN1 对应达妙 SDK 通道 0。
- 版本 `0xCD` 回复为设备类型、产品变体 T1、当前固件 1.2.14、硬件 3.0.0、10 自由度；本查询不含供电电压，因此网页电压留空。
- `0x09` 返回 10 个 little-endian uint16 位置。协议反馈轻微超过 4096 的值继续保留。
- `0x0A` 返回 10 个 uint16 电流读数，保存在 `diagnostics.current_raw`。手册标注单位 0.01A，与 USB SDK 的 mA 字段尚未实机交叉核对，界面只标“原始值”，不直接换算。
- `0x0C` 返回 10 个 int8 温度；`0x0D` 返回一个 uint16 **整手错误码**，保存在 `diagnostics.hand_error_code`。它不等于 USB 的 10 路布尔错误结构，所以网页逐轴状态显示 `--`，不会伪造十轴均正常。
- 普通触觉由 `0x12`（拇指/食指/中指 48 点）、`0x13`（无名指/小指 32 点）、`0x14`（手心/手背 50 点）三组组成。同一轮采样复用组内数据，共 130 点。
- 原始触觉另走公有扩展协议 ID 1，以 5 个分包返回 268 个 uint8；普通标准帧节点仍是 0x09。点数为拇指16、其余四指各18、手心78、手背102。
- CAN 日志以 `canfd_` 开头。这个分支已实现全分辨率原始触觉，尚未实现动作控制或力控。

采集程序只设置达妙适配器位时序，不改手本体通信参数或固件；本次设备升级由另外运行的官方工具完成。

## 时间与缺失数据

采集由单个工作线程持有 SDK 对象，网页请求只读缓存。各字段是先后读取的，并非同一时刻的同步采样。JSON 时间是电脑读取时刻，不是设备硬件时间戳。设置间隔是调度目标，不是硬实时刷新保证。

位置失败时保留上一帧成功值和时间，页面标注等待/过期，有效帧计数不增加；触觉读取失败保留错误并降低显示亮度，不用全零数组冒充新数据。诊断字段保留各自上次成功时间。断开后停止采集，页面可能保留最后一帧供查看。

`data/*.jsonl` 的第一行是 session 元数据，后续行为 sample。未到触觉/诊断读取周期的 sample 不包含该次未读取的诊断字段或触觉值，分析时按字段时间对齐，不能假定每行都有全部字段。
# 三维姿态与原始数组字段（2026-11-16 新增）

`pose.joints_rad` 为官方求解器从 10 轴位置换算的 URDF 关节角（rad），包括 6 个计算得到的被动关节；`pose.clamped_for_display` 标记仅在模型输入端限幅的轴，`positions` 不限幅。

`raw_tactile` 与原有 `tactile`（普通数据）独立。仅当 `raw_tactile.status == "ready"` 时，`raw_tactile.regions[区域].values` 才表示完整原始阵列。`expected_counts` 是预期规格，`returned_counts` 是该次实测长度。当前实机已完整返回 268 点，两轮20组持续读取通过。`returned_frames` 为5，`region_count` 为7；`sampled_at` 为电脑读取时刻。传感器数量的独立查询曾回复7，当前直接解码路径不重复该查询，故 `sensor_count_reply` 为 null，不能把这个 null 当作区域缺失。关闭采集时显示 `disabled`，历史成功记录不会被冒充实时数据。详细说明见 [RAW_TACTILE_AND_3D.md](history/RAW_TACTILE_AND_3D.md)。

