"""One-command release build: frozen executable, optional signing, installer.

Run from the repository root:

    .venv/Scripts/python.exe packaging/build_release.py

The build is identical to the documented manual steps (PyInstaller, then
Inno Setup) but wires the optional signing hook in between, so a machine with
a certificate produces signed artifacts and a machine without one produces
exactly the unsigned artifacts it always did.

Signing configuration lives in packaging/sign_artifacts.py (SIGN_CERT_PATH /
SIGN_CERT_SHA1 / SIGN_CERT_PASSWORD / SIGN_TIMESTAMP_URL / SIGNTOOL). Pass
--no-sign to force an unsigned build, or --dry-run to print the commands.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "github_scout_agent.spec"
INSTALLER_SCRIPT = ROOT / "packaging" / "installer.iss"
DEFAULT_DIST = ROOT / "dist"
DEFAULT_WORK = ROOT / "build"
EXE_NAME = "GitHubScoutAgent"

_ISCC_CANDIDATES = (
    r"D:\Inno Setup 7\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
)


def discover_iscc(env: Mapping[str, str] | None = None) -> str | None:
    """Return ISCC.exe, honouring INNO_ISCC before the usual install paths."""
    env = os.environ if env is None else env
    configured = env.get("INNO_ISCC")
    if configured:
        candidate = Path(configured)
        return str(candidate) if candidate.is_file() else None
    found = shutil.which("iscc")
    if found:
        return found
    for candidate in _ISCC_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return None


def pyinstaller_command(
    *,
    python: str,
    distpath: Path,
    workpath: Path,
    spec: Path = SPEC,
) -> list[str]:
    return [
        python, "-m", "PyInstaller", str(spec),
        "--noconfirm",
        "--distpath", str(distpath),
        "--workpath", str(workpath),
    ]


def iscc_command(
    *,
    iscc: str,
    installer_output: Path,
    build_dir: Path,
    script: Path = INSTALLER_SCRIPT,
) -> list[str]:
    """Compile the installer, packaging build_dir and writing into installer_output."""
    return [iscc, f"/O{installer_output}", f"/DMyBuildDir={build_dir}", str(script)]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: Sequence[str], *, dry_run: bool) -> int:
    print("$ " + " ".join(command))
    if dry_run:
        return 0
    return subprocess.run(command, check=False, cwd=str(ROOT)).returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the frozen executable and the installer.")
    parser.add_argument("--clean", action="store_true", help="remove build/ and dist/GitHubScoutAgent first")
    parser.add_argument("--skip-exe", action="store_true", help="reuse the existing frozen build")
    parser.add_argument("--skip-installer", action="store_true", help="stop after the executable")
    parser.add_argument("--no-sign", action="store_true", help="do not attempt Authenticode signing")
    parser.add_argument("--dry-run", action="store_true", help="print commands without running them")
    parser.add_argument("--distpath", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--workpath", type=Path, default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    distpath = args.distpath.resolve()
    workpath = args.workpath.resolve()
    exe_path = distpath / EXE_NAME / f"{EXE_NAME}.exe"
    installer_output = distpath / "installer"
    setup_glob = "GitHubScoutAgent-*-setup.exe"

    if args.clean and not args.dry_run:
        shutil.rmtree(workpath, ignore_errors=True)
        shutil.rmtree(distpath / EXE_NAME, ignore_errors=True)
        print(f"cleaned {workpath} and {distpath / EXE_NAME}")

    if not args.skip_exe:
        code = _run(
            pyinstaller_command(python=sys.executable, distpath=distpath, workpath=workpath),
            dry_run=args.dry_run,
        )
        if code != 0:
            print("ERROR PyInstaller failed", file=sys.stderr)
            return code

    artifacts = [exe_path]
    if not args.skip_installer:
        iscc = discover_iscc()
        if iscc is None:
            print("ERROR ISCC.exe not found; set INNO_ISCC to the Inno Setup compiler", file=sys.stderr)
            return 4
        installer_output.mkdir(parents=True, exist_ok=True)
        code = _run(
            iscc_command(iscc=iscc, installer_output=installer_output, build_dir=distpath / EXE_NAME),
            dry_run=args.dry_run,
        )
        if code != 0:
            print("ERROR ISCC failed", file=sys.stderr)
            return code
        built_setups = sorted(installer_output.glob(setup_glob), key=lambda item: item.stat().st_mtime)
        if not built_setups:
            print(f"ERROR no installer produced in {installer_output}", file=sys.stderr)
            return 5
        artifacts.append(built_setups[-1])

    if not args.no_sign:
        from sign_artifacts import main as sign_main

        sign_args = [str(artifact) for artifact in artifacts]
        if args.dry_run:
            sign_args.append("--dry-run")
        code = sign_main(sign_args)
        if code != 0:
            print("ERROR signing failed", file=sys.stderr)
            return code

    if args.dry_run:
        print("dry run complete: nothing was built")
        return 0

    print("")
    for artifact in artifacts:
        if artifact.is_file():
            size = artifact.stat().st_size
            print(f"{artifact.relative_to(ROOT)}: {size:,} bytes sha256={sha256_of(artifact)}")
        else:
            print(f"{artifact.relative_to(ROOT)}: MISSING", file=sys.stderr)
            return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())