# OmniHand Workbench

面向 **智元 OmniHand 2025 O10 触觉版**的非官方 Python 二次开发工作台。支持USB直连和达妙双路USB2CANFD，提供位置与触觉读取、网页三维显示及明确触发的位置控制。

主要开发环境为 **Ubuntu 22.04 x86_64**，同时保留Windows支持。

## 功能

- 读取十轴位置、温度、电流原始数据及错误状态，支持JSONL记录。
- CAN FD只读取并显示七区域原始268点；USB保留普通130点读取。
- 官方右手URDF、关节解算与逐触点三维投影；二维采用官方上位机点序。
- 九个预设动作、实时单轴滑块、自定义保存；默认直接发送目标，可选电脑渐变，支持停止保持。
- 三维始终跟随设备反馈；提供位置、位置＋电流PT及位置＋速度＋电流PVT，电流参数为mA。
- 网页和Python客户端共用一个设备访问线程；连接后默认锁定控制。

## Ubuntu快速开始

```bash
git clone https://github.com/Asgod-1/omnihand-workbench.git
cd omnihand-workbench
python3 scripts/bootstrap_ubuntu.py
sudo bash scripts/setup_udev.sh
# 注销并重新登录后：
bash Start_CANFD_Dashboard.sh
```

打开 <http://127.0.0.1:8765/>。安装器在项目`.venv`准备Python3.13、C++运行库、libusb及固定SDK，不替换系统Python或系统库。

不接硬件时可启动演示：

```bash
bash Start_Demo_Dashboard.sh
```

详细步骤及USB接入见[Ubuntu说明](docs/UBUNTU.md)。**不要从Windows复制`.venv`到Ubuntu。**

## Windows

使用Python3.13，在项目目录运行：

```powershell
python scripts/bootstrap.py --download-sdk
.\.venv\Scripts\python.exe scripts/setup_damiao.py
.\.venv\Scripts\python.exe scripts/setup_visualization.py
.\Start_CANFD_Dashboard.cmd
```

USB直连可用`Start_Dashboard.cmd`；停止服务用`Stop_Dashboard.cmd`。VS Code打开`omnihand-workbench.code-workspace`。

暂不连接实机时，双击`Start_Demo_Dashboard.cmd`，在 <http://127.0.0.1:8765/> 使用模拟位置、原始268点及预设动作。Ubuntu使用`bash Start_Demo_Dashboard.sh`；VS Code默认调试项也是无硬件演示。

## Python接口

先启动后台服务，浏览器可以不开。Python导入路径`omnihand_usb`保留，以兼容已有代码：

```python
from omnihand_usb.control_client import ControlClient

hand = ControlClient()
state = hand.state()                       # 最近一次采样快照
positions = state['positions']             # 十轴位置
raw = state['raw_tactile']
if raw.get('status') == 'ready':
    index_raw = raw['regions']['INDEX']['values']

preview = hand.preview(preset='open')       # 只预览，不运动
```

读取示例见`examples/01`至`07`；控制入口为[examples/08_position_control.py](examples/08_position_control.py)，默认仅预览，增加`--execute`才发送动作。操作流程见[控制说明](docs/CONTROL.md)。

## 结构

```text
src/omnihand_usb/     设备适配、协议、采集、控制、HTTP服务
src/omnihand_usb/web/ 网页与三维触觉显示
examples/            读取和控制示例
scripts/             环境、驱动及三维资源重建
tests/               无实机软件检查
docs/                当前使用说明与数据定义
docs/history/        历史实测和排查记录
data/                本机数据、自定义预设、日志（不提交）
vendor/              SDK、动态库、几何资源缓存（不提交）
.venv/               本机环境（不提交）
```

## 验证与限制

- 已验证Windows上O10手FW1.2.14、达妙双路板FW1.0.0.7、CAN1、私有节点`0x09`及原始触觉公有ID`1`的读取。
- 当前软件检查覆盖控制故障复核、原始触觉、传感垫投影及跨平台加载；CI配置Windows与Ubuntu22.04，Ubuntu真机通讯仍待实测。
- 位置控制已通过实机应答及保持验证，负载抓取仍待验证。停止依赖通讯应答，不能替代硬件急停；失能会释放保持力。
- 三维触点是按索引进行的表面配准，尚未做毫米位置或受力标定。

运行检查：`python -m unittest discover -s tests -v`。Ubuntu请用`bash scripts/run_python.sh -m unittest discover -s tests -v`。

[文档索引](docs/README.md) · [第三方来源](THIRD_PARTY.md) · [更新记录](CHANGELOG.md)

本仓库不包含厂商SDK二进制、原始几何模型或本机采集数据；由脚本从固定来源获取。项目尚未指定开源许可证，第三方内容遵循其各自条款。
