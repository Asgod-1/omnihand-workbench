"""Create the project-local environment and install the official binary SDK."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_EXAMPLES = ("demo_get_hardware_info.py", "demo_tactile_sensor.py", "demo_monitor_current.py", "demo_monitor_temperature.py", "demo_monitor_error.py")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def asset_key():
    arch = platform.machine().lower()
    if sys.platform == "win32" and arch in ("amd64", "x86_64"):
        return "windows-x64"
    if sys.platform.startswith("linux"):
        if arch in ('aarch64','arm64'):return 'linux-arm64'
        if arch in ('amd64','x86_64'):return 'linux-x64'
    raise RuntimeError("This project currently supports Windows x64 and vendor-supported Ubuntu platforms")


def extract_sdk(archive, destination):
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    with zipfile.ZipFile(archive) as zipped:
        wheels = [n for n in zipped.namelist() if f"-{tag}-{tag}-" in PurePosixPath(n).name and n.endswith(".whl")]
        if len(wheels) != 1:
            raise RuntimeError(f"Expected one wheel for {tag}; found {len(wheels)}. Use Python 3.10–3.14 supported by SDK 1.1.11.")
        wheel = destination / "wheels" / PurePosixPath(wheels[0]).name
        wheel.parent.mkdir(parents=True, exist_ok=True)
        wheel.write_bytes(zipped.read(wheels[0]))
        official = destination / "official_examples"
        official.mkdir(parents=True, exist_ok=True)
        for name in OFFICIAL_EXAMPLES:
            matches = [n for n in zipped.namelist() if "/python/demo/omnihand_2025/" in "/" + n and PurePosixPath(n).name == name]
            if len(matches) != 1:
                raise RuntimeError(f"Official read-only example missing from release: {name}")
            (official / name).write_bytes(zipped.read(matches[0]))
    return wheel


def run(*args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="Existing official SDK wheel matching this Python/platform")
    parser.add_argument("--download-sdk", action="store_true", help="Download the pinned official release and verify its SHA-256")
    args = parser.parse_args()
    if not (3, 10) <= sys.version_info[:2] <= (3, 14):
        parser.error("Use a vendor-supported Python version; Python 3.13 is required for this project's Damiao adapter")
    manifest = json.loads((Path(__file__).with_name("sdk-release.json")).read_text(encoding="utf-8"))
    vendor = ROOT / "vendor"
    vendor.mkdir(exist_ok=True)
    wheel = args.wheel.resolve() if args.wheel else None
    if args.download_sdk:
        asset = manifest["assets"][asset_key()]
        archive = vendor / (asset_key() + "-sdk-" + manifest["version"] + ".zip")
        if not archive.exists() or digest(archive) != asset["sha256"]:
            temporary = archive.with_suffix(".download")
            print("Downloading the official SDK release…", flush=True)
            with urllib.request.urlopen(asset["url"], timeout=120) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
            if digest(temporary) != asset["sha256"]:
                raise RuntimeError("Downloaded SDK archive checksum mismatch; it will not be installed")
            temporary.replace(archive)
        wheel = extract_sdk(archive, vendor)
    if wheel is None:
        tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
        matches = list((vendor / "wheels").glob(f"*-{tag}-{tag}-*.whl"))
        if len(matches) != 1:
            parser.error("No matching local SDK wheel. Run again with --download-sdk or --wheel PATH.")
        wheel = matches[0]
    env = ROOT / ".venv"
    python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True, symlinks=False).create(env)
    run(python, "-m", "pip", "install", "--no-index", "--no-deps", wheel)
    run(python, "-m", "pip", "install", "--index-url", "https://pypi.org/simple", "setuptools>=68")
    run(python, "-m", "pip", "install", "--no-build-isolation", "--index-url", "https://pypi.org/simple", "-e", ROOT)
    run(python, "-m", "omnihand_usb", "check")
    print("Ready. Source, environment and local data all live in this project directory.")


if __name__ == "__main__":
    main()
