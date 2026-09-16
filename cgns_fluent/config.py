"""JSON 配置加载与校验。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "input": {
        "file": None,
        "folder": None,
        "recursive": True,
        "patterns": ["*.cgns", "*.hdf", "*.hdf5", "*.adf"],
    },
    "output": {
        "dir": "./cgns_output",
        "keep_relative_structure": True,
        "report_file": "batch_report.txt",
    },
    "cgns_tools": {
        "cgnsconvert": "cgnsconvert",
        "output_format": "auto"
    },
    "parallel": {
        "workers": 0,
        "query_workers": None,
        "convert_workers": None,
        "plot_workers": None,
    },
    "features": {
        "info": {
            "enabled": True,
            "export_file": True,
        },
        "query": {
            "enabled": False,
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "radius": 0.01,
            "fields": [],
            "zone": None,
            "solution": None,
            "time": None,
            "max_hits": 50,
            "export_file": True,
            "coord_decimals": 8,
        },
        "convert": {
            "enabled": False,
            "target_version": 3.4,
            "suffix": "_v34",
            "output_dir": None,
        },
    },
}


@dataclass
class InputConfig:
    file: Path | None
    folder: Path | None
    recursive: bool
    patterns: list[str]


@dataclass
class OutputConfig:
    dir: Path
    keep_relative_structure: bool
    report_file: str


@dataclass
class InfoFeature:
    enabled: bool
    export_file: bool


@dataclass
class QueryFeature:
    enabled: bool
    x: float
    y: float
    z: float
    radius: float
    fields: list[str]
    zone: str | int | None
    solution: str | None
    time: float | None
    max_hits: int
    export_file: bool
    coord_decimals: int


@dataclass
class ConvertFeature:
    enabled: bool
    target_version: float
    suffix: str
    output_dir: Path | None


@dataclass
class ToolsConfig:
    cgnsconvert: str
    output_format: str


@dataclass
class ParallelConfig:
    workers: int
    query_workers: int | None
    convert_workers: int | None
    plot_workers: int | None


@dataclass
class AppConfig:
    source: Path
    input: InputConfig
    output: OutputConfig
    info: InfoFeature
    query: QueryFeature
    convert: ConvertFeature
    tools: ToolsConfig
    parallel: ParallelConfig
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _as_path(value: Any, base_dir: Path) -> Path | None:
    if value is None or value == "":
        return None
    p = Path(str(value))
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return p


def _parse_zone(value: Any) -> str | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text


def load_config(path: str | Path) -> AppConfig:
    cfg_path = Path(path).resolve()
    if not cfg_path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        user = json.load(f)
    if not isinstance(user, dict):
        raise ValueError("配置文件根节点必须是 JSON 对象")

    merged = _deep_merge(DEFAULT_CONFIG, user)
    base_dir = cfg_path.parent
    inp = merged["input"]
    out = merged["output"]
    feats = merged["features"]

    input_cfg = InputConfig(
        file=_as_path(inp.get("file"), base_dir),
        folder=_as_path(inp.get("folder"), base_dir),
        recursive=bool(inp.get("recursive", True)),
        patterns=list(inp.get("patterns") or ["*.cgns"]),
    )
    if input_cfg.file is None and input_cfg.folder is None:
        raise ValueError("input.file 与 input.folder 至少配置一项")

    output_cfg = OutputConfig(
        dir=_as_path(out.get("dir"), base_dir) or (base_dir / "cgns_output"),
        keep_relative_structure=bool(out.get("keep_relative_structure", True)),
        report_file=str(out.get("report_file") or "batch_report.txt"),
    )

    info_raw = feats.get("info", {})
    query_raw = feats.get("query", {})
    convert_raw = feats.get("convert", {})

    target = float(convert_raw.get("target_version", 3.4))
    if target not in (3.4, 4.1):
        raise ValueError("features.convert.target_version 仅支持 3.4 或 4.1")

    fields = query_raw.get("fields") or []
    if isinstance(fields, str):
        fields = [x.strip() for x in fields.split(",") if x.strip()]

    time_val = query_raw.get("time", None)
    if time_val == "":
        time_val = None
    elif time_val is not None:
        time_val = float(time_val)

    enabled_any = any(
        [
            bool(info_raw.get("enabled", False)),
            bool(query_raw.get("enabled", False)),
            bool(convert_raw.get("enabled", False)),
        ]
    )
    if not enabled_any:
        raise ValueError("features 中至少开启一项：info / query / convert")

    if bool(query_raw.get("enabled", False)) and float(query_raw.get("radius", -1)) < 0:
        raise ValueError("features.query.radius 必须 >= 0")

    tools_raw = merged.get("cgns_tools", {})
    out_fmt = str(tools_raw.get("output_format", "auto")).strip().lower()
    if out_fmt not in {"auto", "hdf5", "adf"}:
        raise ValueError("cgns_tools.output_format 仅支持 auto / hdf5 / adf")
    tools_cfg = ToolsConfig(
        cgnsconvert=str(tools_raw.get("cgnsconvert") or "cgnsconvert"),
        output_format=out_fmt,
    )

    from .format_io import configure_tools

    configure_tools(cgnsconvert=tools_cfg.cgnsconvert, output_format=tools_cfg.output_format)

    par_raw = merged.get("parallel", {})

    def _workers(value: Any) -> int | None:
        if value is None or value == "":
            return None
        return int(value)

    parallel_cfg = ParallelConfig(
        workers=int(par_raw.get("workers", 0) or 0),
        query_workers=_workers(par_raw.get("query_workers")),
        convert_workers=_workers(par_raw.get("convert_workers")),
        plot_workers=_workers(par_raw.get("plot_workers")),
    )

    return AppConfig(
        source=cfg_path,
        input=input_cfg,
        output=output_cfg,
        info=InfoFeature(
            enabled=bool(info_raw.get("enabled", False)),
            export_file=bool(info_raw.get("export_file", True)),
        ),
        query=QueryFeature(
            enabled=bool(query_raw.get("enabled", False)),
            x=float(query_raw.get("x", 0.0)),
            y=float(query_raw.get("y", 0.0)),
            z=float(query_raw.get("z", 0.0)),
            radius=float(query_raw.get("radius", 0.01)),
            fields=list(fields),
            zone=_parse_zone(query_raw.get("zone")),
            solution=query_raw.get("solution") or None,
            time=time_val,
            max_hits=int(query_raw.get("max_hits", 50)),
            export_file=bool(query_raw.get("export_file", True)),
            coord_decimals=int(query_raw.get("coord_decimals", 8)),
        ),
        convert=ConvertFeature(
            enabled=bool(convert_raw.get("enabled", False)),
            target_version=target,
            suffix=str(convert_raw.get("suffix", "_v34")),
            output_dir=_as_path(convert_raw.get("output_dir"), base_dir),
        ),
        tools=tools_cfg,
        parallel=parallel_cfg,
        raw=merged,
    )


def discover_cgns_files(cfg: AppConfig) -> list[Path]:
    files: list[Path] = []
    if cfg.input.file is not None:
        if not cfg.input.file.is_file():
            raise FileNotFoundError(f"输入文件不存在: {cfg.input.file}")
        files.append(cfg.input.file.resolve())

    if cfg.input.folder is not None:
        folder = cfg.input.folder
        if not folder.is_dir():
            raise FileNotFoundError(f"输入文件夹不存在: {folder}")
        found: list[Path] = []
        for pattern in cfg.input.patterns:
            if cfg.input.recursive:
                found.extend(folder.rglob(pattern))
            else:
                found.extend(folder.glob(pattern))
        for p in found:
            if p.is_file():
                files.append(p.resolve())

    # 去重并稳定排序
    uniq = sorted(set(files), key=lambda p: str(p).lower())
    # 转换输出文件若落在同目录，可能被二次扫到；按后缀粗略跳过已转换产物可在 runner 处理
    return uniq
