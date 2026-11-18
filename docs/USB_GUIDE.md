# USB 首次开发

## 环境和独立示例

直接运行项目 `.venv/Scripts/python.exe`，不必激活 base 或先执行激活脚本。VS Code 已配置这个解释器。SDK 是装在该环境中的二进制 Python 库，日常开发修改 `src/omnihand_usb/` 或新增 Python 文件即可。

本次连接参数：右手，USB CDC 串口 COM5，手设备 ID 1，SDK USB 参数 baudrate 460800。COM 号属于电脑分配值，换接口后需要重新查看，不写入公共仓库默认配置。

先停止项目网页的 USB 连接，再运行：

```powershell
.\.venv\Scripts\python.exe examples\01_check_environment.py
.\.venv\Scripts\python.exe examples\02_device_info.py --port COM5
.\.venv\Scripts\python.exe examples\03_read_positions.py --port COM5
.\.venv\Scripts\python.exe examples\04_read_tactile.py --port COM5
```

SDK的工厂、查询及明确调用的位置写入集中在`src/omnihand_usb/device.py`。新增位置控制见[CONTROL.md](CONTROL.md)，USB模式不提供电机使能按钮。官方原始示例由`examples/official/run.py`调用，统一指定USB，详情见同目录README。

## 本项目网页

`Start_Dashboard.cmd` → <http://127.0.0.1:8765/>。

页面自动连接当前唯一的 USB 串口，也可以手选串口再点连接。顶部显示设备供电反馈、最高电机温度、触觉点数和有效位置帧。下方显示十轴状态、最近位置曲线和七区域普通触觉。“开始记录/停止记录”只改变电脑本地文件记录，不向手下发控制命令。

网页源文件在 `src/omnihand_usb/web/`。改 HTML/CSS/JavaScript 后刷新浏览器即可；修改 Python 代码后需要停止并重新启动服务。

本地接口：

| 请求 | 含义 |
| --- | --- |
| `GET /api/state` | 读取采集线程缓存，不直接并发查询 SDK |
| `GET /api/ports` | 列出串口信息，不扫描手设备 ID |
| `GET /health` | 确认本项目服务与运行模式 |
| `POST /api/connect` | `{"port":"COM5","hand":"right","device_id":1}` |
| `POST /api/disconnect` | `{}`，释放 USB 连接 |
| `POST /api/recording` | `{"enabled":true}` 或 `false`，开关本机记录 |
| `POST /api/shutdown` | `{}`，结束本项目服务并释放连接 |

POST 使用 `Content-Type: application/json`。服务只监听本机 127.0.0.1，未提供运动方法。网页数据语义见 [DATA.md](DATA.md)。

## 官方 SDK 自带网页

1. 在本项目页面点“断开连接”，或者双击 `Stop_Dashboard.cmd`。
2. 双击 `Start_Official_Web.cmd` → <http://127.0.0.1:8766/>。这是 SDK 原装的通用 API 调试页，启动不会自动创建手。
3. 在创建实例处选择 `omnihand_2025`、`right`、`usb`，连接参数使用下方 JSON；或运行本项目的只读 HTTP 客户端。
4. 用完在官方页面释放实例，关闭官方服务，再重新连接本项目网页。不要同时对 COM5 打开两个连接。

```json
{
  "hand_type": "omnihand_2025",
  "hand_side": "right",
  "conn_method": "usb",
  "conn_config": {
    "uart_port": "COM5",
    "hand_device_id": 1,
    "baudrate": 460800
  }
}
```

官方页面没有创建任何实例时，可以另开项目终端运行：

```powershell
.\.venv\Scripts\python.exe examples\05_official_web_client.py --port COM5
```

这个示例通过官方 `POST /v1/hands` 创建自己的 USB 实例，调用 `get_all_joint_positions` 和七次 `get_tactile_sensor_data`，最后 `DELETE /v1/hands/{hand_id}` 释放自己的实例。不会改动已存在的官方页面会话。

官方健康地址是 `/health`；方法调用为 `POST /v1/hands/{hand_id}/methods?method=方法名`。当前 v1.1.11 实测位置 getter 直接返回数组，普通触觉返回 `{"data":[...]}`，不同于部分文档的 `{"result":...}` 示例。客户端已兼容这些形状。`finger_index` 使用实际 `Finger` 枚举值，不根据旧文档手填反向编号。

官方页面还提供手势、位置设置等写方法。本阶段只核对查询接口，不运行这些控制项。普通触觉使用 `get_tactile_sensor_data`；不要把 raw 触觉或通用 tactile 推流当成已验证的 USB 功能。

## 常见情况

- **COM5 打不开**：先在另一页面释放实例，退出官方 Toolbox 或其他串口程序；不要同时运行多个示例。
- **自动检测提示多个 USB 设备**：网页明确选择灵巧手的 COM 号，或命令行传 `--port`。
- **读取变慢或数据过期**：查看页面提示与字段时间，先降低读取频率；不把旧值当成新帧。
- **更换系统**：源码可继续用，虚拟环境和官方 wheel 必须按目标系统重建；Ubuntu USB 硬件使用仍需另行验证。
