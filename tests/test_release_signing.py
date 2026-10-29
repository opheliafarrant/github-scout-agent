"""Behavioural checks for the optional signing hook and the release build driver."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def signing():
    return _load("packaging_sign_artifacts", "sign_artifacts.py")


@pytest.fixture(scope="module")
def release():
    return _load("packaging_build_release", "build_release.py")


def test_signing_is_skipped_without_a_certificate(signing):
    commands, reasons = signing.plan_signing(["setup.exe"], env={})

    assert commands == []
    assert "SIGN_CERT_PATH" in reasons[0]


def test_signing_reports_a_missing_signtool(signing, tmp_path):
    env = {"SIGN_CERT_PATH": str(tmp_path / "cert.pfx"), "SIGNTOOL": str(tmp_path / "nope.exe")}

    commands, reasons = signing.plan_signing(["setup.exe"], env=env)

    assert commands == []
    assert "signtool" in reasons[0]


def test_sign_command_uses_a_pfx_and_a_timestamp(signing, tmp_path):
    command = signing.build_sign_command(
        "signtool.exe",
        tmp_path / "setup.exe",
        cert_path=str(tmp_path / "cert.pfx"),
        cert_password="pw",
    )

    assert command[:2] == ["signtool.exe", "sign"]
    assert command[2:4] == ["/fd", "sha256"]
    assert "/tr" in command and "/td" in command
    assert command[command.index("/tr") + 1] == signing.DEFAULT_TIMESTAMP_URL
    assert command[command.index("/f") + 1] == str(tmp_path / "cert.pfx")
    assert command[command.index("/p") + 1] == "pw"
    assert command[-1] == str(tmp_path / "setup.exe")


def test_sign_command_uses_a_store_thumbprint(signing, tmp_path):
    command = signing.build_sign_command(
        "signtool.exe", tmp_path / "setup.exe", cert_sha1="ABC123"
    )

    assert command[command.index("/sha1") + 1] == "ABC123"
    assert "/f" not in command


def test_sign_command_requires_an_identity(signing, tmp_path):
    with pytest.raises(ValueError):
        signing.build_sign_command("signtool.exe", tmp_path / "setup.exe")


def test_verify_signature_reads_the_powershell_status(signing):
    assert signing.verify_signature("a.exe", run=lambda _command: "Valid") is True
    assert signing.verify_signature("a.exe", run=lambda _command: "NotSigned") is False
    assert signing.verify_signature("a.exe", run=lambda _command: "") is False


def test_dry_run_prints_the_command_and_exits_zero(signing, tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "setup.exe"
    artifact.write_bytes(b"MZ")
    monkeypatch.setenv("SIGN_CERT_PATH", str(tmp_path / "cert.pfx"))
    monkeypatch.setenv("SIGNTOOL", str(artifact))

    code = signing.main([str(artifact), "--dry-run"])

    assert code == 0
    printed = capsys.readouterr().out
    assert "sign" in printed and "/fd sha256" in printed


def test_dry_run_without_a_certificate_explains_the_skip(signing, tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "setup.exe"
    artifact.write_bytes(b"MZ")
    monkeypatch.delenv("SIGN_CERT_PATH", raising=False)
    monkeypatch.delenv("SIGN_CERT_SHA1", raising=False)

    code = signing.main([str(artifact), "--dry-run"])

    assert code == 0
    assert "signing skipped" in capsys.readouterr().out


def test_pyinstaller_command_pins_the_spec_and_output_directories(release, tmp_path):
    command = release.pyinstaller_command(
        python="python.exe", distpath=tmp_path / "dist", workpath=tmp_path / "build"
    )

    assert command[1:3] == ["-m", "PyInstaller"]
    assert str(release.SPEC) in command
    assert "--noconfirm" in command
    assert command[command.index("--distpath") + 1] == str(tmp_path / "dist")


def test_iscc_command_packages_the_requested_build_directory(release, tmp_path):
    command = release.iscc_command(
        iscc="ISCC.exe",
        installer_output=tmp_path / "installer",
        build_dir=tmp_path / "dist" / release.EXE_NAME,
    )

    assert command[0] == "ISCC.exe"
    assert command[1] == f"/O{tmp_path / 'installer'}"
    assert command[2] == f"/DMyBuildDir={tmp_path / 'dist' / release.EXE_NAME}"
    assert command[-1] == str(release.INSTALLER_SCRIPT)


def test_discover_iscc_prefers_the_configured_path(release, tmp_path):
    fake = tmp_path / "ISCC.exe"
    fake.write_bytes(b"MZ")

    assert release.discover_iscc({"INNO_ISCC": str(fake)}) == str(fake)
    # A configured but missing path is an explicit error, not a silent fallback.
    assert release.discover_iscc({"INNO_ISCC": str(tmp_path / "missing.exe")}) is None


def test_sha256_of_matches_hashlib(release, tmp_path):
    import hashlib

    artifact = tmp_path / "blob.bin"
    artifact.write_bytes(b"release" * 1000)

    assert release.sha256_of(artifact) == hashlib.sha256(artifact.read_bytes()).hexdigest()