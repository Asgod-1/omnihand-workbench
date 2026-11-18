# Ubuntu 22.04 开发入口

主要目标：Ubuntu 22.04、Intel/AMD x86_64；当前右手O10触觉版、手FW1.2.14、达妙双路板FW1.0.0.7、CAN1。读取、位置控制、原始268点和网页共用Windows版本的代码。

## 首次安装

将项目源码复制或克隆到Ubuntu。**不要复制Windows的`.venv`**。在项目根目录，以普通用户运行：

```bash
python3 scripts/bootstrap_ubuntu.py
sudo bash scripts/setup_udev.sh
```

权限配置完成后注销Ubuntu并重新登录，再启动：

```bash
bash Start_CANFD_Dashboard.sh
```

网页地址：<http://127.0.0.1:8765/>。启动后软件控制保持锁定，CAN FD读取位置和完整原始阵列，普通触觉仅在USB模式保留。预设选择目标后点击发送；开启控制并使能后滑块直接控制单轴，三维持续显示反馈，操作见[CONTROL.md](CONTROL.md)。

安装脚本下载并校验固定版micromamba，在项目`.venv`创建Python 3.13、libusb和C++运行环境，再安装官方SDK 1.1.11、达妙Python包1.0.4/Linux库1.1.0及三维资源。**不更改系统Python、不替换系统C++库、不烧录固件、不连接硬件**。首次下载需要网络；可用`--skip-model`暂时跳过三维资源。

安装时若不存在`config.local.json`，复制`config.ubuntu.example.json`中的本项目参数：私有节点`0x09`、原始触觉公有ID`1`、CAN1、原始采集开启。已有配置不会覆盖。此配置用于当前这只手，换手时应先核对ID。

## 日常开发

Ubuntu上用VS Code打开`omnihand-workbench.code-workspace`，解释器选本项目`.venv/bin/python`。已加入Linux终端与调试运行库路径。SDK环境、网页、业务代码均放在同一个项目内。

终端建议通过包装脚本运行，自动带上本项目运行库：

```bash
# 无硬件环境检查；不连接设备
bash scripts/run_python.sh -m omnihand_usb doctor --transport canfd

# 默认只预览，不运动
bash scripts/run_python.sh examples/08_position_control.py --preset open

# 读取正在运行的工作台中的完整原始阵列
bash scripts/run_python.sh examples/07_read_raw_from_dashboard.py

# 不接硬件，先检查界面和动作流程
bash Start_Demo_Dashboard.sh

# 停止8765工作台
bash Stop_Dashboard.sh
```

自己的Python代码仍通过统一接口访问本机后台，浏览器可以不开：

```python
from omnihand_usb.control_client import ControlClient

hand = ControlClient()
data = hand.state()                     # 最近一次采集快照
positions = data['positions']           # 十轴位置
ordinary = data.get('tactile', {}).get('INDEX', {}).get('values')  # USB普通触觉；CAN FD为None
raw = data['raw_tactile']
if raw.get('status') == 'ready':
    index_raw = raw['regions']['INDEX']['values']

preview = hand.preview(preset='open')    # 计算目标，不向手发送动作
```

真实运动使用同一客户端的`arm()`、`enable()`、`move()`、`stop()`、`lock()`和`disable()`，示例08加`--execute`才会执行。不要在工作台占用达妙模块时另建SDK连接。

如果后续接ROS 2 Humble，可让手的后台继续运行在项目Python 3.13环境；ROS节点在自己的Python 3.10环境通过本机HTTP接口读取和控制，不需要把达妙SDK安装进ROS环境。当前客户端源码支持Python 3.10+。

## 接口、缓存和权限

- Linux加载`vendor/damiao_device_sdk/linux-x64/libdm_device.so`，Windows继续使用原路径DLL；上层协议及动作代码共用。
- Linux库固定在[达妙官方提交36a8b397](https://github.com/dmBots/dm-device-sdk/tree/36a8b3971dc35608701d128f353a32cad3ee6cdb/C%26C%2B%2B/lib/v1.1.0/linux/x86_64)，安装与运行均校验。该x64库依赖`GLIBCXX_3.4.32`，由项目C++运行库提供；达妙官方wheel要求Python3.13起，因此没有采用Ubuntu22.04默认Python3.10作为硬件后台。
- [micromamba固定版本2.9.0-0](https://github.com/mamba-org/micromamba-releases/releases/tag/2.9.0-0)仅用于项目本地环境，校验SHA-256后执行，不自动修改shell启动文件。
- USB规则只匹配当前达妙双路模块`34b7:6632`，使用`plugdev`组和桌面会话权限；无需用root运行工作台。首次加入组后需重新登录。
- USB直连手可用`bash Start_Dashboard.sh --transport usb --port /dev/ttyACM0`，路径按实际设备选择；普通串口权限通常需加入`dialout`组。原始阵列仍使用已验证的CAN FD通路。
- 自定义预设在`data/actions.local.json`，Git默认忽略；若需要带到Ubuntu，单独复制这个文件。`.venv`和系统动态库不能从Windows复制使用。

## 验证范围

无实机Python与JavaScript检查覆盖网页单轴/混合发送及触点表面配准，同时覆盖平台选择、库校验、Linux加载分支、ABI结构、安装计划、权限检查和故障复核。官方Linux x64/ARM64库已下载并校验，Windows环境检查通过。

当前电脑没有可运行的Ubuntu环境，**本次没有在Ubuntu上执行Linux库或做真机通讯测试**。GitHub检查已配置Ubuntu22.04。迁移后先运行`doctor`和离线演示，再连接验证位置与268点原始阵列；USB模式另验证130点普通触觉。
