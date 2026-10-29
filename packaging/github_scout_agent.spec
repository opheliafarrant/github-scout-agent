# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the GitHub Scout Agent desktop build.

Build from the repository root:

    .venv/Scripts/python.exe -m PyInstaller packaging/github_scout_agent.spec --noconfirm

Output: dist/GitHubScoutAgent/GitHubScoutAgent.exe (one directory, no console).
The spec is intentionally kept here so that packaging assets stay out of the
source tree and the application package keeps a single implementation.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
SRC = ROOT / "src"

# The Web UI template is a real file on disk, not an imported module: without an
# explicit data entry the frozen server answers / with 500.
datas = [(str(SRC / "github_scout_agent" / "templates"), "github_scout_agent/templates")]
binaries: list = []
hiddenimports = collect_submodules("github_scout_agent")

# uvicorn resolves its loop, protocol and lifespan implementations at runtime
# through import strings, so static analysis cannot see them.
for package in ("uvicorn", "fastapi", "starlette", "pydantic"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

analysis = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "IPython",
        "pytest",
        "setuptools",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="GitHubScoutAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(ROOT / "packaging" / "version_info.txt"),
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="GitHubScoutAgent",
)
