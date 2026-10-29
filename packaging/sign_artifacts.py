"""Optional Authenticode signing hook for the release artifacts.

Signing is opt-in. With no certificate configured the tool explains why it
skipped and exits 0, so unsigned local builds keep working unchanged; once a
certificate is available nothing in the build has to change.

Configuration (environment variables):

  SIGN_CERT_PATH       PFX/P12 bundle to sign with
  SIGN_CERT_PASSWORD   password for that bundle (optional)
  SIGN_CERT_SHA1       thumbprint of a certificate already in the store;
                       used when SIGN_CERT_PATH is not set
  SIGN_TIMESTAMP_URL   RFC 3161 timestamp server
                       (default http://timestamp.digicert.com)
  SIGNTOOL             explicit path to signtool.exe

Usage:

    python packaging/sign_artifacts.py dist/GitHubScoutAgent/GitHubScoutAgent.exe \
        dist/installer/GitHubScoutAgent-1.2.0-setup.exe

Add --dry-run to print the commands without running them, and --verify-only to
only check the Authenticode status of the given files.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"

_SIGNTOOL_SUFFIXES = (
    r"Windows Kits\10\bin\{version}\x64\signtool.exe",
    r"Windows Kits\10\bin\x64\signtool.exe",
    r"Windows Kits\8.1\bin\x64\signtool.exe",
)
_SIGNTOOL_VERSIONS = ("10.0.26100.0", "10.0.22621.0", "10.0.22000.0", "10.0.19041.0")


def _program_files_roots() -> list[Path]:
    roots = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")), Path(os.environ.get("ProgramFiles", r"C:\Program Files"))]
    return [root for root in roots if root.is_dir()]


def find_signtool(env: Mapping[str, str] | None = None) -> str | None:
    """Return the signtool.exe path, or None when the SDK is not installed."""
    env = os.environ if env is None else env
    configured = env.get("SIGNTOOL")
    if configured:
        candidate = Path(configured)
        return str(candidate) if candidate.is_file() else None
    on_path = shutil.which("signtool")
    if on_path:
        return on_path
    for root in _program_files_roots():
        for version in _SIGNTOOL_VERSIONS:
            for suffix in _SIGNTOOL_SUFFIXES:
                candidate = root / suffix.format(version=version)
                if candidate.is_file():
                    return str(candidate)
    return None


def signing_identity(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Return the configured certificate identity, or None when signing is off."""
    env = os.environ if env is None else env
    cert_path = env.get("SIGN_CERT_PATH")
    if cert_path:
        identity = {"cert_path": cert_path}
        password = env.get("SIGN_CERT_PASSWORD")
        if password:
            identity["cert_password"] = password
        return identity
    thumbprint = env.get("SIGN_CERT_SHA1")
    if thumbprint:
        return {"cert_sha1": thumbprint}
    return None


def build_sign_command(
    signtool: str,
    artifact: Path | str,
    *,
    cert_path: str | None = None,
    cert_password: str | None = None,
    cert_sha1: str | None = None,
    timestamp_url: str = DEFAULT_TIMESTAMP_URL,
) -> list[str]:
    """Build the signtool invocation for one artifact."""
    if not cert_path and not cert_sha1:
        raise ValueError("a certificate path or thumbprint is required to sign")
    command = [signtool, "sign", "/fd", "sha256", "/tr", timestamp_url, "/td", "sha256"]
    if cert_path:
        command += ["/f", str(cert_path)]
        if cert_password:
            command += ["/p", cert_password]
    else:
        command += ["/sha1", str(cert_sha1)]
    command.append(str(artifact))
    return command


def plan_signing(
    artifacts: Iterable[Path | str],
    *,
    env: Mapping[str, str] | None = None,
    signtool: str | None = None,
) -> tuple[list[list[str]], list[str]]:
    """Return (commands, skip reasons); an empty command list means "not signing"."""
    env = os.environ if env is None else env
    identity = signing_identity(env)
    if identity is None:
        return [], ["no signing certificate configured (set SIGN_CERT_PATH or SIGN_CERT_SHA1)"]
    tool = signtool or find_signtool(env)
    if tool is None:
        return [], ["signtool.exe not found (install the Windows SDK or set SIGNTOOL)"]
    timestamp_url = env.get("SIGN_TIMESTAMP_URL") or DEFAULT_TIMESTAMP_URL
    commands = [
        build_sign_command(tool, artifact, timestamp_url=timestamp_url, **identity)
        for artifact in artifacts
    ]
    return commands, []


def _run_powershell(command: str) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.stdout or "").strip()


def verify_signature(path: Path | str, *, run=_run_powershell) -> bool:
    """True when the file carries a valid Authenticode signature."""
    output = run(f"(Get-AuthenticodeSignature -FilePath '{path}').Status")
    return output.splitlines()[-1].strip().lower() == "valid" if output else False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optionally sign the release artifacts.")
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="print the commands instead of running them")
    parser.add_argument("--verify-only", action="store_true", help="only check the Authenticode status")
    args = parser.parse_args(argv)

    if args.verify_only:
        failed = False
        for artifact in args.artifacts:
            valid = verify_signature(artifact)
            print(f"{artifact}: {'valid' if valid else 'unsigned or invalid'}")
            failed = failed or not valid
        return 1 if failed else 0

    missing = [str(artifact) for artifact in args.artifacts if not Path(artifact).is_file()]
    if missing:
        print(f"ERROR missing artifacts: {', '.join(missing)}", file=sys.stderr)
        return 2

    commands, reasons = plan_signing(args.artifacts)
    if not commands:
        print(f"signing skipped: {reasons[0]}; the artifacts stay unsigned")
        return 0

    for command in commands:
        print(" ".join(command))
        if args.dry_run:
            continue
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            print(f"ERROR signtool exited with {completed.returncode}", file=sys.stderr)
            return completed.returncode

    if args.dry_run:
        return 0

    unsigned = [str(artifact) for artifact in args.artifacts if not verify_signature(artifact)]
    if unsigned:
        print(f"ERROR signature verification failed: {', '.join(unsigned)}", file=sys.stderr)
        return 3
    print("all artifacts carry a valid Authenticode signature")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())