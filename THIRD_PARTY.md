# 第三方来源

- 16 点手指的相对索引排布参照固定 SDK 1.1.11 的 [O10 官方文档与配图](https://github.com/AgibotTech/agillink_omnihand_sdk/blob/v1.1.11/doc/en/API_CPP_O10.md)。原图缓存在 Git 忽略的 `vendor/web_assets/tactile/`，URL 与 SHA-256 在资源重建脚本中固定。当前没有来源明确的掌心、手背毫米坐标或逐触点面积。

- 官方 SDK 与只读示例：[AgibotTech/agilink_omnihand_sdk](https://github.com/AgibotTech/agilink_omnihand_sdk)，固定 [v1.1.11 发行版](https://github.com/AgibotTech/agilink_omnihand_sdk/releases/tag/v1.1.11)。官方包标注 Mulan PSL v2。wheel 和原始示例置于 Git 忽略的 `vendor/`，未将其二进制或第三方原始脚本纳入本项目版本管理。
- SDK 官方中文 API：[O10 Python](https://github.com/AgibotTech/agilink_omnihand_sdk/blob/main/doc/zh_cn/API_PYTHON_O10.md)、[服务接口](https://github.com/AgibotTech/agilink_omnihand_sdk/blob/main/doc/zh_cn/API_SERVER.md)。在线 main 文档可能变化，实际调用以当前固定 wheel 内 API 为准。
- 官方网页及 OpenAPI 随 SDK 安装于 `.venv/Lib/site-packages/omnihand_server/`，通过 `Start_Official_Web.cmd` 启动。其通用页面与本项目独立的只读网页源码分开管理。
- `pyserial==3.5`：通过 pip 安装，许可证 BSD-3-Clause，由其包维护原始声明。
- 达妙 CAN FD 使用官方 [dmBots/dm-device-sdk](https://github.com/dmBots/dm-device-sdk) 的固定提交 `36a8b3971dc35608701d128f353a32cad3ee6cdb`，Python 包 1.0.4、Windows MSVC 后端 1.1.0、随包 libusb；通过 `scripts/setup_damiao.py` 获取并校验，二进制均在 Git 忽略的 `vendor/damiao_device_sdk/`，保留其官方来源与声明。新增 O10 CAN FD 查询/解码层为本项目代码。
- CI 使用 GitHub 官方 [checkout](https://github.com/actions/checkout/releases/tag/v7.0.1) 与 [setup-python](https://github.com/actions/setup-python/releases/tag/v7.1.0)。
- Ubuntu加载同一达妙固定提交的Linux x64/ARM64 `libdm_device.so`，分别缓存于`vendor/damiao_device_sdk/linux-x64`和`linux-arm64`，不纳入Git。Ubuntu环境安装器使用官方[micromamba 2.9.0-0](https://github.com/mamba-org/micromamba-releases/releases/tag/2.9.0-0)，固定SHA-256，工具及包缓存均在忽略的`vendor/`内；环境包来自conda-forge，保留各包许可证。
- 整手几何使用 [智元官方右手 URDF（2026-08-27）](https://www.agibot.com/filepage/276.html)；原始 ZIP 和 STL 不纳入 Git，资源重建脚本固定其下载地址与 SHA-256。几何数据权利属于原提供方。
- 三维渲染使用 [Three.js 0.170.0](https://github.com/mrdoob/three.js/tree/r170) 及官方 OrbitControls/STLLoader，MIT 许可证随资源保存在 `vendor/web_assets/three/LICENSE`。通过固定 npm 包及 SHA-256 获取，运行时从 localhost 读取。
- 原始触觉适配使用当前已安装的官方 `omnihand==1.1.11` 解码器；达妙与 SDK 之间的本地只读帧转接代码属于本项目，实机兼容性限制见 `docs/history/RAW_TACTILE_AND_3D.md`。

本项目新增代码尚未指定对外开源许可证。准备公开仓库时再选定，并按第三方各自条款处理再分发。固定 SDK 的下载地址和校验值记录于 `scripts/sdk-release.json`。
