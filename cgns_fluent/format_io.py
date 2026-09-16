"""CGNS 存储格式检测与 ADF <-> HDF5 桥接。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


class FormatError(RuntimeError):
    pass


@dataclass
class ToolsSettings:
    cgnsconvert: str = "cgnsconvert"
    output_format: str = "auto"  # auto | hdf5 | adf


_TOOLS = ToolsSettings()


def configure_tools(
    cgnsconvert: str | None = None,
    output_format: str | None = None,
) -> ToolsSettings:
    if cgnsconvert:
        _TOOLS.cgnsconvert = str(cgnsconvert)
    if output_format:
        fmt = str(output_format).strip().lower()
        if fmt not in {"auto", "hdf5", "adf"}:
            raise ValueError("output_format 仅支持 auto / hdf5 / adf")
        _TOOLS.output_format = fmt
    return _TOOLS


def get_tools() -> ToolsSettings:
    return _TOOLS


def detect_cgns_format(path: str | Path) -> str:
    """返回 'hdf5' | 'adf' | 'unknown'。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)

    try:
        import h5py

        if h5py.is_hdf5(str(p)):
            return "hdf5"
    except OSError:
        pass

    with p.open("rb") as f:
        head = f.read(128)

    # HDF5 签名
    if head.startswith(b"\x89HDF\r\n\x1a\n"):
        return "hdf5"

    # ADF 文件头通常含 "ADF Database Version"
    upper = head.upper()
    if b"ADF DATABASE" in upper or b"ADF DATABASE VERSION" in upper:
        return "adf"
    if head[:3] in (b"ADF", b"Adf", b"adf"):
        return "adf"

    # 部分 ADF 实现头部偏移，再扫一段
    with p.open("rb") as f:
        chunk = f.read(4096)
    if b"ADF Database" in chunk or b"ADF DATABASE" in chunk.upper():
        return "adf"

    return "unknown"


def resolve_output_format(source_format: str, preferred: str | None = None) -> str:
    pref = (preferred or _TOOLS.output_format or "auto").lower()
    if pref == "auto":
        return source_format
    if pref in {"hdf5", "adf"}:
        return pref
    raise ValueError(f"不支持的输出格式: {pref}")


def find_cgnsconvert(explicit: str | None = None) -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.append(_TOOLS.cgnsconvert)
    env = os.environ.get("CGNSCONVERT")
    if env:
        candidates.append(env)

    # 常见安装位置（Windows / Linux）
    candidates.extend(
        [
            "cgnsconvert",
            "cgnsconvert.exe",
            str(Path(os.environ.get("CGNS_ROOT", "")) / "bin" / "cgnsconvert"),
            str(Path(os.environ.get("CGNS_ROOT", "")) / "bin" / "cgnsconvert.exe"),
            r"C:\Program Files\CGNS\bin\cgnsconvert.exe",
            "/usr/bin/cgnsconvert",
            "/usr/local/bin/cgnsconvert",
        ]
    )

    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        path = Path(c)
        if path.is_file():
            return str(path)
        found = shutil.which(c)
        if found:
            return found
    raise FormatError(
        "未找到 cgnsconvert。处理 ADF 需要 CGNS Tools。\n"
        "请安装后将其加入 PATH，或在 JSON 中设置 cgns_tools.cgnsconvert，"
        "或设置环境变量 CGNSCONVERT。"
    )


def run_cgnsconvert(
    src: str | Path,
    dst: str | Path,
    *,
    to: str,
    tool: str | None = None,
) -> None:
    """to: 'hdf5' | 'adf'"""
    src_p = Path(src)
    dst_p = Path(dst)
    if not src_p.is_file():
        raise FileNotFoundError(src_p)
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    exe = find_cgnsconvert(tool)
    flag = "-h" if to == "hdf5" else "-a"
    # -f：即使格式相同也写出（用于可靠落盘到目标路径）
    cmd = [exe, flag, "-f", str(src_p), str(dst_p)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise FormatError(f"无法执行 cgnsconvert: {exc}") from exc

    if proc.returncode != 0 or not dst_p.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()
        raise FormatError(
            f"cgnsconvert 转换失败 ({src_p} -> {to}):\n{detail or '无详细输出'}"
        )


def to_hdf5(src: str | Path, dst: str | Path, tool: str | None = None) -> Path:
    src_fmt = detect_cgns_format(src)
    dst_p = Path(dst)
    if src_fmt == "hdf5":
        if Path(src).resolve() != dst_p.resolve():
            shutil.copy2(src, dst_p)
        return dst_p
    if src_fmt == "adf":
        run_cgnsconvert(src, dst_p, to="hdf5", tool=tool)
        return dst_p
    raise FormatError(f"无法识别 CGNS 格式: {src}")


def to_adf(src: str | Path, dst: str | Path, tool: str | None = None) -> Path:
    src_fmt = detect_cgns_format(src)
    dst_p = Path(dst)
    if src_fmt == "adf":
        if Path(src).resolve() != dst_p.resolve():
            shutil.copy2(src, dst_p)
        return dst_p
    if src_fmt == "hdf5":
        run_cgnsconvert(src, dst_p, to="adf", tool=tool)
        return dst_p
    raise FormatError(f"无法识别 CGNS 格式: {src}")


def export_as(
    work_hdf5: str | Path,
    dst: str | Path,
    *,
    target_format: str,
    tool: str | None = None,
) -> str:
    """将工作用 HDF5 导出为 hdf5 或 adf。返回实际格式。"""
    fmt = target_format.lower()
    dst_p = Path(dst)
    work_p = Path(work_hdf5)
    if fmt == "hdf5":
        if work_p.resolve() != dst_p.resolve():
            shutil.copy2(work_p, dst_p)
        return "hdf5"
    if fmt == "adf":
        run_cgnsconvert(work_p, dst_p, to="adf", tool=tool)
        return "adf"
    raise ValueError(f"target_format 无效: {target_format}")


@dataclass
class TemporaryHDF5:
    """ADF 打开时的临时 HDF5，退出时清理。"""

    work_path: Path
    original_path: Path
    original_format: str
    _cleanup: list[Path] = field(default_factory=list)

    def cleanup(self) -> None:
        for p in self._cleanup:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def materialize_hdf5(src: str | Path, tool: str | None = None) -> TemporaryHDF5:
    src_p = Path(src).resolve()
    fmt = detect_cgns_format(src_p)
    if fmt == "hdf5":
        return TemporaryHDF5(work_path=src_p, original_path=src_p, original_format="hdf5")
    if fmt != "adf":
        raise FormatError(
            f"{src_p} 不是可识别的 CGNS（HDF5/ADF）。"
            "请确认文件完整，或安装 cgnsconvert 后再试。"
        )

    fd, tmp_name = tempfile.mkstemp(prefix="cgns_adf_", suffix=".cgns")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        run_cgnsconvert(src_p, tmp_path, to="hdf5", tool=tool)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return TemporaryHDF5(
        work_path=tmp_path,
        original_path=src_p,
        original_format="adf",
        _cleanup=[tmp_path],
    )
