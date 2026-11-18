"""Pinned vendor runtimes for Windows and Ubuntu. Importing opens no device."""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
import importlib.metadata
import platform
from pathlib import Path
import struct
import sys

REVISION = '36a8b3971dc35608701d128f353a32cad3ee6cdb'
WRAPPER_VERSION = '1.0.4'
NATIVE_VERSION = '1.1.0'
WHEEL = ('Python/pack/dmcan_sdk-1.0.4-py3-none-any.whl', '40aaf11c1ab39e175124f162436ba5e5d9609520')
HEADER = ('C&C++/lib/v1.1.0/dmcan.h', '6c910ab663e9d3bb8ff6084c455aca93e6d0878e')
RUNTIMES = {
    'windows-x64': {
        'files': (
            ('C&C++/lib/v1.1.0/windows/msvc/dm_device.dll', '8380a36c6c1483f834d10330596954b3c96f9532'),
            ('Python/demo/libusb-1.0.dll', '10ab5a8de5c764811ac0a7a76a81d7c8f9af84a2'),
        ),
        'library': 'dm_device.dll', 'elf_machine': None,
    },
    'linux-x64': {
        'files': (('C&C++/lib/v1.1.0/linux/x86_64/libdm_device.so', '52db55cd308002d9457e98518ac653d6d6de4937'),),
        'library': 'libdm_device.so', 'elf_machine': 62,
        'required_glibcxx': 'GLIBCXX_3.4.32',
    },
    'linux-arm64': {
        'files': (('C&C++/lib/v1.1.0/linux/arm64/libdm_device.so', 'eb4f0a26b9107d6dbe3d1eaef7ce9a4b39d0f0e0'),),
        'library': 'libdm_device.so', 'elf_machine': 183,
    },
}


def platform_key(system=None, machine=None):
    system = sys.platform if system is None else system
    machine = (platform.machine() if machine is None else machine).lower()
    if system == 'win32' and machine in ('amd64', 'x86_64'):
        return 'windows-x64'
    if system == 'linux':
        if machine in ('x86_64', 'amd64'):
            return 'linux-x64'
        if machine in ('aarch64', 'arm64'):
            return 'linux-arm64'
    raise RuntimeError(f'Unsupported platform: {system}/{machine}; use Ubuntu x86_64/aarch64 or Windows x64')


def blob_hash(data):
    return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()


def runtime_folder(base, key):
    """Keep the tested Windows paths; separate Linux architectures in the cache."""
    if key not in RUNTIMES:
        raise ValueError('Unknown Damiao runtime')
    base = Path(base)
    return base if key == 'windows-x64' else base / key


@dataclass(frozen=True)
class Runtime:
    key: str
    folder: Path
    library: Path


def checked_runtime(base, key=None, *, check_package=True):
    key = platform_key() if key is None else key
    spec = RUNTIMES[key]
    folder = runtime_folder(base, key).resolve()
    for source, expected in spec['files']:
        path = folder / Path(source).name
        if not path.is_file():
            raise RuntimeError(f'缺少达妙{key}库：{path}；请运行 scripts/setup_damiao.py')
        data = path.read_bytes()
        if blob_hash(data) != expected:
            raise RuntimeError(f'达妙驱动库版本或校验不符：{path.name}；请重新运行 scripts/setup_damiao.py')
        if spec['elf_machine'] is not None:
            if len(data) < 20 or data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != spec['elf_machine']:
                raise RuntimeError(f'Linux动态库架构不匹配：{key}')
    if check_package:
        try:
            version = importlib.metadata.version('dmcan-sdk')
        except importlib.metadata.PackageNotFoundError:
            version = None
        if version != WRAPPER_VERSION:
            raise RuntimeError('请运行 scripts/setup_damiao.py 安装固定 dmcan-sdk 1.0.4（需要Python 3.13）')
    return Runtime(key, folder, folder / spec['library'])


def load_linux_library(runtime):
    """Use the process's normal loader; launchers supply the project runtime path."""
    try:
        return ctypes.CDLL(str(runtime.library))
    except OSError as exc:
        raise RuntimeError(
            f'无法加载达妙Linux库：{exc}。Ubuntu 22.04请先运行 '
            'python3 scripts/bootstrap_ubuntu.py，再使用 bash Start_CANFD_Dashboard.sh；'
            '其项目环境提供libusb及GLIBCXX_3.4.32运行库，不需要替换系统库。'
        ) from exc


def check_abi(definitions):
    """Fail before opening hardware if vendor ctypes no longer matches dmcan.h."""
    expected = {'usb_rx_frame_head': 16, 'usb_rx_frame': 80, 'dmcan_channel_can_info': 18}
    sizes = {name: ctypes.sizeof(getattr(definitions, name)) for name in expected}
    if sizes != expected or definitions.usb_rx_frame.payload.offset != 16:
        raise RuntimeError(f'达妙Python结构与固定C接口不匹配：{sizes}')
    return sizes
