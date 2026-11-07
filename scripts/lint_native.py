"""Install checksum-pinned native linters, or run one (Linux/Windows x86-64)."""

import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".lint-tools"
# SHA256 from the assets of each official tagged GitHub release.
RELEASES = {
    "actionlint": (
        "rhysd/actionlint",
        "v1.7.12",
        (
            "actionlint_1.7.12_linux_amd64.tar.gz",
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
        ),
        (
            "actionlint_1.7.12_windows_amd64.zip",
            "6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9",
        ),
    ),
    "gitleaks": (
        "gitleaks/gitleaks",
        "v8.30.1",
        (
            "gitleaks_8.30.1_linux_x64.tar.gz",
            "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
        ),
        (
            "gitleaks_8.30.1_windows_x64.zip",
            "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e",
        ),
    ),
    "hadolint": (
        "hadolint/hadolint",
        "v2.15.1",
        (
            "hadolint-linux-x86_64",
            "c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507",
        ),
        (
            "hadolint-windows-x86_64.exe",
            "01d927294962b5387f9ead4f18679158452be4f17c765ad0bdffe5264b9c7b0a",
        ),
    ),
}


def binary(name):
    return TOOLS / (name + (".exe" if sys.platform == "win32" else ""))


def install():
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Native lint installer supports x86-64 only.")
    if sys.platform not in {"win32", "linux"}:
        raise SystemExit("Use Linux/Windows x86-64 for pinned native lint tools.")
    TOOLS.mkdir(exist_ok=True)
    for name, (repo, version, linux, windows) in RELEASES.items():
        asset, checksum = windows if sys.platform == "win32" else linux
        url = f"https://github.com/{repo}/releases/download/{version}/{asset}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != checksum:
            raise SystemExit(f"Checksum mismatch: {name}")
        executable = name + (".exe" if sys.platform == "win32" else "")
        if asset.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                data = archive.read(executable)
        elif asset.endswith(".tar.gz"):
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                member = archive.extractfile(executable)
                if member is None:
                    raise SystemExit(f"Missing executable: {name}")
                data = member.read()
        binary(name).write_bytes(data)
        binary(name).chmod(0o755)
        print(f"Installed {name} {version}", flush=True)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name == "install":
        install()
        return 0
    if name not in RELEASES:
        raise SystemExit(
            "Usage: python scripts/lint_native.py install|actionlint|hadolint|gitleaks [args]"
        )
    if not binary(name).is_file():
        raise SystemExit("Run python scripts/lint_native.py install first.")
    args = sys.argv[2:]
    if name == "gitleaks" and args and args[0] == "snapshot":
        # Scan source files only, including current unstaged edits/new files.
        # Local ignored credentials, databases and environments never enter it.
        files = (
            subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
            )
            .stdout.decode("utf-8")
            .split("\0")
        )
        with tempfile.TemporaryDirectory(prefix="lernium-lint-") as directory:
            snapshot = Path(directory)
            for file in set(filter(None, files)):
                source = ROOT / file
                if not source.is_file() or source.is_symlink():
                    continue
                destination = snapshot / file
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            return subprocess.run(
                [str(binary(name)), "dir", "--redact", "--no-banner", *args[1:], str(snapshot)],
                cwd=ROOT,
            ).returncode
    if name == "actionlint":
        # ShellCheck is independently run by Hadolint for Dockerfile RUN commands.
        args = ["-shellcheck="] + args
    elif name == "hadolint":
        args = args or ["Dockerfile"]
    elif not args:
        # Pre-commit scans only the staged patch, including newly staged files.
        args = ["git", "--pre-commit", "--staged", "--redact", "--no-banner"]
    return subprocess.run([str(binary(name)), *args], cwd=ROOT, env=os.environ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
