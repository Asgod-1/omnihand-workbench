# 官方只读示例

本目录是 USB 限定的启动入口，原始官方脚本保存在 Git 忽略的 `vendor/official_examples/`，未改写其源码。

```powershell
.\.venv\Scripts\python.exe examples\official\run.py info --port COM5
.\.venv\Scripts\python.exe examples\official\run.py tactile --port COM5
.\.venv\Scripts\python.exe examples\official\run.py current --port COM5
.\.venv\Scripts\python.exe examples\official\run.py temperature --port COM5
.\.venv\Scripts\python.exe examples\official\run.py errors --port COM5
```

COM5 是本次台架实际端口，换电脑后应重新检测。启动入口始终传入 `-d usb -t right`，不会沿用原始脚本的 CAN 默认参数。

上面的示例与网页采集程序轮流运行，不要同时占用同一个串口。普通 USB 路径的 DeviceInfo 可能为默认空字段，设备型号/版本应查看 VendorInfo；这不是 CAN 波特率为零的实测结果。

来源：[AgibotTech/agilink_omnihand_sdk](https://github.com/AgibotTech/agilink_omnihand_sdk)，固定发行版 [v1.1.11](https://github.com/AgibotTech/agilink_omnihand_sdk/releases/tag/v1.1.11)。官方代码标注 Mulan PSL v2，归 Agibot Co., Ltd.；这些第三方代码不随本项目 Git 提交。
