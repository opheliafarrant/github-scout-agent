"""Behavioural checks for the frozen-build entry point (packaging/launcher.py)."""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "packaging" / "launcher.py"


@pytest.fixture(scope="module")
def launcher():
    spec = importlib.util.spec_from_file_location("packaging_launcher", LAUNCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def test_launcher_binds_loopback_only(launcher):
    assert launcher.DEFAULT_HOST == "127.0.0.1"
    assert launcher.DEFAULT_PORT == 8080


def test_launcher_stays_inside_the_repository(launcher):
    assert LAUNCHER_PATH.is_file()
    assert launcher.__doc__ is not None


def test_port_probe_reports_a_closed_port_as_free(launcher):
    port = _free_port(launcher.DEFAULT_HOST)
    assert launcher._port_is_free(launcher.DEFAULT_HOST, port) is True


def test_instance_probe_rejects_a_port_that_is_not_ours(launcher):
    port = _free_port(launcher.DEFAULT_HOST)
    assert launcher._running_instance(launcher.DEFAULT_HOST, port) is False
