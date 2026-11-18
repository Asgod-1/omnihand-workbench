"""Environment checks only: never create a device context or send CAN frames."""
from __future__ import annotations
import ctypes
import importlib
import importlib.metadata
import os
from pathlib import Path
import platform
import sys

from . import SDK_VERSION
from .damiao_runtime import checked_runtime, check_abi, load_linux_library


def linux_usb_devices(sysfs=Path('/sys/bus/usb/devices'), devices=Path('/dev/bus/usb')):
    found = []
    for entry in Path(sysfs).glob('*'):
        try:
            vendor = (entry / 'idVendor').read_text().strip().lower()
            product = (entry / 'idProduct').read_text().strip().lower()
            if (vendor, product) != ('34b7', '6632'):
                continue
            bus = int((entry / 'busnum').read_text())
            device = int((entry / 'devnum').read_text())
        except (OSError, ValueError):
            continue
        node = Path(devices) / f'{bus:03d}' / f'{device:03d}'
        found.append({'vid': vendor, 'pid': product, 'node': str(node),
                      'read_write_access': os.access(node, os.R_OK | os.W_OK)})
    return found


def check_native(root):
    runtime = checked_runtime(Path(root) / 'vendor/damiao_device_sdk')
    directory = None
    usb = library = None
    try:
        if runtime.key == 'windows-x64':
            directory = os.add_dll_directory(str(runtime.folder))
            usb = ctypes.CDLL(str(runtime.folder / 'libusb-1.0.dll'))
            library = ctypes.CDLL(str(runtime.library))
        else:
            library = load_linux_library(runtime)
        abi = check_abi(importlib.import_module('dmcan.dmcan_def'))
        # Inspect exports, without calling context-create or any device function.
        for symbol in ('dmcan_context_create', 'dmcan_device_send_can', 'dmcan_device_set_channel_baudrate'):
            getattr(library, symbol)
        return {'platform': runtime.key, 'library': str(runtime.library),
                'native_version': '1.1.0', 'wrapper_version': '1.0.4', 'abi_bytes': abi,
                'device_opened': False}
    finally:
        library = usb = None
        if directory is not None:
            directory.close()


def diagnose(root, transport='canfd'):
    root = Path(root)
    checks = []
    def check(name, function, required=True):
        try:
            detail = function()
            checks.append({'name': name, 'ok': True, 'required': required, 'detail': detail})
        except Exception as exc:
            checks.append({'name': name, 'ok': False, 'required': required, 'error': str(exc)})

    def python_check():
        minimum = (3, 13) if transport == 'canfd' else (3, 10)
        if sys.version_info[:2] < minimum:
            raise RuntimeError(f'需要Python {minimum[0]}.{minimum[1]}+；Ubuntu请用scripts/bootstrap_ubuntu.py')
        return {'executable': sys.executable, 'version': platform.python_version(), 'prefix': sys.prefix}
    check('python', python_check)

    def sdk_check():
        version = importlib.metadata.version('omnihand')
        if version != SDK_VERSION:
            raise RuntimeError(f'需要omnihand=={SDK_VERSION}，当前{version}')
        module = importlib.import_module('omnihand')
        return {'version': version, 'module': str(module.__file__), 'device_opened': False}
    check('official_sdk', sdk_check)
    if transport == 'canfd':
        check('damiao_runtime', lambda: check_native(root))

    def assets_check():
        files = ['model/model.json', 'three/three.module.js', 'three/OrbitControls.js', 'three/STLLoader.js']
        missing = [name for name in files if not (root / 'vendor/web_assets' / name).is_file()]
        if missing:
            raise RuntimeError('三维资源缺失；运行scripts/setup_visualization.py：' + ', '.join(missing))
        return '3D resources cached locally'
    check('visualization', assets_check, required=False)
    usb = linux_usb_devices() if sys.platform == 'linux' and transport == 'canfd' else []
    return {
        'ok': all(c['ok'] for c in checks if c['required']),
        'platform': sys.platform, 'machine': platform.machine(), 'transport': transport,
        'checks': checks, 'usb_adapters': usb,
        'device_opened': False, 'can_frames_sent': 0, 'real_hardware_verified': False,
        'usb_permission_help': 'sudo bash scripts/setup_udev.sh；重新登录后再接入达妙USB' if usb and any(not d['read_write_access'] for d in usb) else None,
        'note': '检查通过只确认软件环境。未连接设备时不会失败；实机通讯仍需启动工作台验证。',
    }
