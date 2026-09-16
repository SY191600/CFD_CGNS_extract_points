# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：尽量打全 conda/CGNS 环境中的 DLL，便于拷到其它电脑。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

block_cipher = None

datas = []
binaries = []
hiddenimports = [
    "cgns_fluent",
    "cgns_fluent.batch",
    "cgns_fluent.config",
    "cgns_fluent.convert",
    "cgns_fluent.format_io",
    "cgns_fluent.hdf5_cgns",
    "cgns_fluent.info",
    "cgns_fluent.query",
    "h5py",
    "h5py.defs",
    "h5py.utils",
    "h5py.h5ac",
    "h5py._proxy",
    "numpy",
    "matplotlib",
    "matplotlib.backends.backend_agg",
    "PIL",
]


def _add_binary(path: Path, dest: str = ".") -> None:
    if path.is_file():
        binaries.append((str(path), dest))


# 显式带上当前解释器的 python3xx.dll / python3.dll（跨机最常缺这个）
for base in {Path(sys.prefix), Path(sys.base_prefix), Path(sys.exec_prefix)}:
    for name in (
        f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        f"python{sys.version_info.major}.dll",
        "python3.dll",
        "python313.dll",
        "python312.dll",
        "python311.dll",
        "python310.dll",
    ):
        _add_binary(base / name)
        _add_binary(base / "Library" / "bin" / name)

# 常规包收集
for pkg in ("h5py", "matplotlib", "numpy", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

try:
    datas += collect_data_files("matplotlib")
except Exception:
    pass

for pkg in ("h5py", "numpy"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

# conda 专用：按元数据收集依赖 DLL
try:
    from PyInstaller.utils.hooks import conda_support

    for name in (
        "python",
        "h5py",
        "numpy",
        "hdf5",
        "zlib",
        "libaec",
        "libcurl",
        "matplotlib",
        "libpng",
        "freetype",
        "vc",
        "vs2015_runtime",
    ):
        try:
            binaries += conda_support.collect_dynamic_libs(name, dependencies=True)
        except Exception:
            pass
except Exception:
    pass

# 兜底：CONDA_PREFIX\\Library\\bin 常见 DLL + Python DLL
conda_prefix = os.environ.get("CONDA_PREFIX") or ""
search_dirs = []
if conda_prefix:
    search_dirs.extend(
        [
            Path(conda_prefix),
            Path(conda_prefix) / "Library" / "bin",
            Path(conda_prefix) / "DLLs",
        ]
    )
search_dirs.extend([Path(sys.prefix), Path(sys.prefix) / "Library" / "bin"])

patterns = [
    "python3*.dll",
    "hdf5*.dll",
    "zlib*.dll",
    "libzlib*.dll",
    "szip*.dll",
    "libaec*.dll",
    "libcurl*.dll",
    "liblzma*.dll",
    "libpng*.dll",
    "freetype*.dll",
    "jpeg*.dll",
    "libjpeg*.dll",
    "tiff*.dll",
    "libtiff*.dll",
    "libwebp*.dll",
    "zstd*.dll",
    "libzstd*.dll",
    "libiomp*.dll",
    "libopenblas*.dll",
    "openblas*.dll",
    "mkl_rt*.dll",
    "mkl_core*.dll",
    "mkl_intel_thread*.dll",
    "mkl_def*.dll",
    "mkl_avx*.dll",
    "mkl_vml*.dll",
    "VCRUNTIME140*.dll",
    "MSVCP140*.dll",
    "concrt140.dll",
    "vcruntime140_1.dll",
]

seen = {Path(src).resolve().as_posix().lower() for src, _ in binaries}
for folder in search_dirs:
    if not folder.is_dir():
        continue
    for pat in patterns:
        for f in folder.glob(pat):
            key = f.resolve().as_posix().lower()
            if key in seen:
                continue
            binaries.append((str(f), "."))
            seen.add(key)

a = Analysis(
    ["cgns_tool.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["hooks/pyi_rth_cgns_dllpath.py"],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cgns_tool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="cgns_tool",
)
