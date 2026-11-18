# 达妙双路 USB2CANFD · O10接入

2026-11-18已在同一连接上新增默认锁定的位置控制、预设、停止保持和使能接口，见[CONTROL.md](CONTROL.md)。下文为前期只读接入实测；动作流程已在演示模式验证，尚未对实机发送写控制。

2026-11-17更新，Windows x64。当前实测组合为手FW1.2.14和达妙双路FW1.0.0.7；原始268点通过两轮20/20组采集和软件重连，普通130点与位置并行正常。升级过程及证据见 [RAW_TACTILE_DIAGNOSIS.md](history/RAW_TACTILE_DIAGNOSIS.md)。当前适配对象为达妙**双路**模块，不自动套用到单路、GS_USB或新版V2固件。

## 本次连接与参数

| 项目 | 实测 / 设置 |
| --- | --- |
| 电脑端设备 | DM-USB2FDCAN_Daul，VID 34B7 / PID 6632，WinUSB 接口正常 |
| 附带串口 | COM8，USB2UART；不用于传输本项目的 CAN 帧 |
| 模块固件 | dual_app v1.0.0.7（原1.0.0.3，已获准升级并回读确认） |
| 物理接口 | 用户确认第1路 CAN，对应 SDK 通道 0 |
| 手节点 | 实测 0x09，标准 11-bit ID |
| CAN 模式 | CAN FD，数据帧，BRS 开启 |
| 仲裁 / 数据段 | 1,000,000 / 5,000,000 bit/s |
| 采样点 | 仲裁 80%，数据 75%，设置后回读核对 |
| 手型号 | OmniHand 2025 O10，产品变体 T1，10 轴 |
| 手固件 / 硬件 | CAN 回复 1.2.14 / 3.0.0 |

电脑 USB → 达妙板；板子 CANH/CANL → 手原配 CAN FD 两根线；电源单独给手供电。不要把 CAN 接到板子的 UART 接口。接头应按 H/L 标记和线束定义核对，不能只凭颜色判断。

两端终端匹配按实际总线配置核对，不叠加多余电阻。板子的拨码开关用于接入其120Ω终端电阻。当前连线已收到手的真实CAN FD回复；采集程序不会自动刷固件。

## 使用

保持手供电、CAN1 和电脑 USB 已连接，双击根目录 `Start_CANFD_Dashboard.cmd`，打开 <http://127.0.0.1:8765/>。网页选择“达妙 CAN FD / CAN1 / 0x09”。重复启动会复用已有的同模式服务。

`Start_Dashboard.cmd` 默认仍为 USB 直连手。两种启动方式使用同一工作台和端口，切换时可先停止旧服务，或在页面断开后更改通信方式。`Stop_Dashboard.cmd` 适用于两种模式。

独立命令（先在网页断开，以免同时占用适配器）：

```powershell
.\.venv\Scripts\python.exe examples\06_canfd_readonly.py
.\.venv\Scripts\python.exe -m omnihand_usb sample --transport canfd --can-channel 1 --can-id 0x09 --count 20 --tactile
.\.venv\Scripts\python.exe -m omnihand_usb dashboard --transport canfd --can-channel 1 --can-id 0x09 --open
```

`--can-channel` 使用外壳/板子的物理编号 1 或 2；程序内部再转换到 0 或 1。`--can-id` 是 CAN 节点地址，与 USB 模式的 `--device-id` 分开。

## 依赖与兼容

当前已安装于项目 `.venv`：`dmcan-sdk==1.0.4`，native 1.1.0（MSVC）。库文件来自达妙官方固定提交，缓存于 `vendor/damiao_device_sdk/`。另一台 Windows x64 电脑可运行：

```powershell
python scripts/setup_damiao.py
```

该脚本只下载、校验和安装本机软件，不打开硬件，不刷固件。原 MinGW 后端还有额外运行库依赖，本项目改用同版本官方 MSVC 后端。原生上下文只销毁一次，避免先关闭设备、再销毁上下文造成驱动重复清理。

实测发现该驱动在同一进程内关闭后再次初始化可能返回设备数 0。CAN FD 连接现由独立的本机通信进程持有，每次重连创建新的驱动进程；网页主进程通过有界请求读取缓存/结果，不接触原生 USB 句柄。停止时先禁用适配器通道、销毁上下文，再退出工作进程；如果原生清理超时，只结束本项目创建的通信进程。已完成 3 次连续重连和网页按钮重连验证，无需拔插模块。

单路板、V2系列及Linux真机尚未验证。当前Python1.0.4/native1.1.0在本机双路板FW1.0.0.7已实测。2026-11-18已加入对应Linux平台库加载和Ubuntu22.04独立环境安装入口，见[UBUNTU.md](UBUNTU.md)，不能复制Windows DLL或`.venv`。

## 读取边界

CAN 分支仅允许协议中的查询命令。没有设备使能/失能、位置设置、手势、混合控制、清错、ID 修改、校准或升级接口。“启用适配器通道”不等于“使能手电机”。断开只停适配器通道和本机采集，不改手的使能状态。

已读数据：10轴位置、10路温度、10路电流原始值、整手错误码、七区域普通130点，以及独立的原始268点。原始使用公有扩展ID1，普通仍使用私有标准节点0x09。详细字段差异见 [DATA.md](DATA.md)。尚未进行抓取或任何动作测试。

## 实机验证结果

CAN 连续采集 20/20 帧有效，位置长度全部为 10，普通触觉点数全部为 130，读取错误 0；当次整手错误码为 0。新增协议白名单、DLC 补齐解析、数据截断拒绝、驱动清理、达妙 UART 排除等检查，与原 USB 检查一起共 **20 项通过**。

网页已验证真实 CAN FD 显示、开始/停止记录、断开/重连；最后一次网页记录包含 50 个采样条目，停止后保持只读连接。连续重连结果保存在 `data/canfd_reconnect_results.json`，页面预览为 `data/canfd-dashboard-preview.jpg`。

原始帧和设备状态仅留在 Git 忽略的 `data/`：`damiao_first_can_probe.json`、`damiao_adapter_info.json`、`canfd_baseline_20.jsonl`、`canfd_baseline_results.json`。SDK 二进制、设备日志和采集数据不加入 Git。

官方来源：[达妙 SDK](https://github.com/dmBots/dm-device-sdk)、[双路板说明书](https://github.com/dmBots/usb2canfd-dual)、[O10 官方手册入口](https://cn.agilink-ai.com/OmniHand/14.html)、[智元 CAN FD 位时序说明](https://github.com/AgibotTech/agilink_omnihand_sdk/blob/main/doc/zh_cn/SOCKETCAN_SETUP.md)。
# 整手三维与原始触觉（2026-11-16 更新）

当前工作台使用官方右手URDF模型和官方求解器显示实测姿态。2026-11-17已读通原始268点，与普通130点分开管理。使用方法和数据边界见 [RAW_TACTILE_AND_3D.md](history/RAW_TACTILE_AND_3D.md)。

