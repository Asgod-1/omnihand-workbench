"""One project-local Ubuntu 22.04 x64 environment. Run with system python3.

This prepares dependencies only. It never opens the hand, installs udev rules,
changes the system Python, updates firmware, or starts a device connection.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MICROMAMBA_VERSION = '2.9.0-0'
MICROMAMBA_URL = f'https://github.com/mamba-org/micromamba-releases/releases/download/{MICROMAMBA_VERSION}/micromamba-linux-64'
MICROMAMBA_SHA256 = '366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3'


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def check_target(system=None, machine=None):
    system = sys.platform if system is None else system
    machine = platform.machine().lower() if machine is None else machine.lower()
    if system != 'linux' or machine not in ('x86_64', 'amd64'):
        raise RuntimeError('This installer targets Ubuntu 22.04 x86_64. It cannot reuse a Windows .venv.')


def fetch_micromamba(root):
    folder = root / 'vendor/environment-tools'
    target = folder / f'micromamba-{MICROMAMBA_VERSION}-linux-64'
    if not target.is_file() or sha256(target) != MICROMAMBA_SHA256:
        folder.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.download')
        print('Downloading pinned micromamba from its official release…', flush=True)
        with urllib.request.urlopen(MICROMAMBA_URL, timeout=120) as response, temporary.open('wb') as output:
            for block in iter(lambda: response.read(1024 * 1024), b''):
                output.write(block)
        if sha256(temporary) != MICROMAMBA_SHA256:
            raise RuntimeError('Micromamba SHA-256 mismatch; it will not be executed')
        temporary.replace(target)
    target.chmod(0o755)
    return target


def runtime_environment(root):
    result = os.environ.copy()
    library = str(root / '.venv/lib')
    result['LD_LIBRARY_PATH'] = library + (':' + result['LD_LIBRARY_PATH'] if result.get('LD_LIBRARY_PATH') else '')
    result['PYTHONUTF8'] = '1'
    result.pop('PYTHONHOME', None)
    return result


def prepare_environment(root):
    env = root / '.venv'
    if env.exists() and not (env / 'conda-meta').is_dir():
        raise RuntimeError('Existing .venv is not this Ubuntu Conda environment; it will not be overwritten. Use a fresh source checkout without the Windows .venv.')
    tool = fetch_micromamba(root)
    # --no-rc prevents inherited global channels from changing this project.
    subprocess.run([
        str(tool), '--no-rc', '--root-prefix', str(root / 'vendor/environment-tools/mamba-root'),
        'create' if not (env / 'bin/python').exists() else 'install', '--yes',
        '--prefix', str(env), '--file', str(root / 'environment.ubuntu.yml'),
        '--strict-channel-priority',
    ], cwd=root, check=True)
    python = env / 'bin/python'
    if not python.is_file():
        raise RuntimeError('Ubuntu environment creation did not produce .venv/bin/python')
    return python


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-model', action='store_true', help='Prepare SDK/driver only; download 3D assets later')
    parser.add_argument('--environment-only', action='store_true', help='Only prepare Python 3.13, libusb and C++ runtime')
    args = parser.parse_args(argv)
    check_target()
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        raise RuntimeError('Run as your normal user, without sudo; only setup_udev.sh needs administrator access')
    python = prepare_environment(ROOT)
    env = runtime_environment(ROOT)
    if args.environment_only:
        print('Project runtime ready. No hardware has been opened.');return
    commands = [
        [str(python), 'scripts/bootstrap.py', '--download-sdk'],
        [str(python), 'scripts/setup_damiao.py'],
    ]
    if not args.skip_model:
        commands.append([str(python), 'scripts/setup_visualization.py'])
    for command in commands:
        subprocess.run(command, cwd=ROOT, env=env, check=True)
    config = ROOT / 'config.local.json'
    if not config.exists():
        profile = json.loads((ROOT / 'config.ubuntu.example.json').read_text(encoding='utf-8'))
        with config.open('x', encoding='utf-8') as stream:
            json.dump(profile, stream, ensure_ascii=False, indent=2)
    subprocess.run([str(python), '-m', 'omnihand_usb', 'doctor', '--transport', 'canfd'], cwd=ROOT, env=env, check=True)
    print('Ready. Next: sudo bash scripts/setup_udev.sh (once), then bash Start_CANFD_Dashboard.sh')
    print('No device connection or motor write has been performed by this installer.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Ubuntu setup failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
