"""Cache pinned official Damiao Windows/Linux libraries and install its wrapper."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from omnihand_usb.damiao_runtime import (  # noqa: E402
    REVISION, RUNTIMES, WHEEL, HEADER, platform_key, runtime_folder, blob_hash,
)


def fetch_file(source, expected, target, *, offline=False):
    data = target.read_bytes() if target.is_file() else b''
    if blob_hash(data) != expected:
        if offline:
            raise RuntimeError(f'离线缓存缺失或校验失败：{target}')
        url = f'https://raw.githubusercontent.com/dmBots/dm-device-sdk/{REVISION}/' + urllib.parse.quote(source, safe='/')
        with urllib.request.urlopen(url, timeout=45) as response:
            data = response.read()
        if blob_hash(data) != expected:
            raise RuntimeError(f'Official file hash mismatch: {source}')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.download')
        temporary.write_bytes(data)
        temporary.replace(target)
    return {'path': source, 'local': target.name, 'git_blob_sha': expected,
            'sha256': hashlib.sha256(data).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=tuple(RUNTIMES), help='Only with --download-only; cache another OS without executing it')
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Use only verified existing vendor files')
    args = parser.parse_args(argv)
    if args.platform and not args.download_only:
        parser.error('--platform requires --download-only; never install a foreign OS runtime')
    key = args.platform or platform_key()
    if not args.download_only and sys.version_info < (3, 13):
        raise SystemExit('dmcan-sdk 1.0.4 requires Python 3.13+. On Ubuntu use python3 scripts/bootstrap_ubuntu.py')
    if not args.download_only:
        expected_python = ROOT / ('.venv/Scripts/python.exe' if sys.platform == 'win32' else '.venv/bin/python')
        if not expected_python.exists() or Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
            raise SystemExit('Run this script using this project .venv interpreter, not the system/base Python')
    base = ROOT / 'vendor/damiao_device_sdk'
    folder = runtime_folder(base, key)
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    for source, expected in (*RUNTIMES[key]['files'], HEADER):
        records.append(fetch_file(source, expected, folder / Path(source).name, offline=args.offline))
    wheel = base / Path(WHEEL[0]).name
    records.append(fetch_file(*WHEEL, wheel, offline=args.offline))
    (folder / 'manifest.json').write_text(json.dumps({
        'repository': 'dmBots/dm-device-sdk', 'revision': REVISION, 'platform': key,
        'native_version': '1.1.0', 'python_package_version': '1.0.4', 'files': records,
    }, indent=2), encoding='utf-8')
    if not args.download_only:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)], check=True)
    print(f'Damiao {key} ready. No device was opened and no firmware was changed.')


if __name__ == '__main__':
    main()
