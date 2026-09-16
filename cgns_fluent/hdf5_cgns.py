"""HDF5 CGNS 底层读取（Fluent 默认导出格式）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterator

import h5py
import numpy as np

DATA_CANDIDATES = (" data", "data", "\x00 data")

# cgnslib.h ElementType_t
ELEMENT_TYPE_NAMES = {
    0: "ElementTypeNull",
    1: "ElementTypeUserDefined",
    2: "NODE",
    3: "BAR_2",
    4: "BAR_3",
    5: "TRI_3",
    6: "TRI_6",
    7: "QUAD_4",
    8: "QUAD_8",
    9: "QUAD_9",
    10: "TETRA_4",
    11: "TETRA_10",
    12: "PYRA_5",
    13: "PYRA_13",
    14: "PYRA_14",
    15: "PENTA_6",
    16: "PENTA_15",
    17: "PENTA_18",
    18: "HEXA_8",
    19: "HEXA_20",
    20: "HEXA_27",
    21: "MIXED",
    22: "PYRA_4",
    23: "NGON_n",
    24: "NFACE_n",
    25: "BAR_4",
    26: "TRI_9",
    27: "TRI_10",
    28: "QUAD_12",
    29: "QUAD_16",
    30: "TETRA_16",
    31: "TETRA_20",
    32: "PYRA_21",
    33: "PYRA_29",
    34: "PYRA_30",
    35: "PENTA_24",
    36: "PENTA_38",
    37: "PENTA_40",
    38: "HEXA_32",
    39: "HEXA_56",
    40: "HEXA_64",
}

WALL_BC_KEYWORDS = (
    "BCWALL",
    "BCWALLINVISCID",
    "BCWALLVISCOUS",
    "BCWALLVISCOUSHEATFLUX",
    "BCWALLVISCOUSISOTHERMAL",
    "WALL",
)

FLUID_HINTS = ("FLUID", "INTERIOR", "CELLZONE", "FLUIDZONE")


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return ""
        if value.dtype.kind in ("S", "a"):
            return _as_str(value.reshape(-1)[0])
        if value.dtype.kind == "U":
            return str(value.reshape(-1)[0]).strip()
        if value.dtype == object:
            return _as_str(value.reshape(-1)[0])
        return str(value.reshape(-1)[0]).strip()
    return str(value).strip()


def _attr(obj: h5py.HLObject, *names: str, default: str = "") -> str:
    for name in names:
        if name in obj.attrs:
            return _as_str(obj.attrs[name])
    return default


def node_label(obj: h5py.HLObject) -> str:
    return _attr(obj, "label", "Label")


def node_name(obj: h5py.HLObject, fallback: str = "") -> str:
    named = _attr(obj, "name", "Name")
    if named:
        return named
    return fallback or os.path.basename(obj.name.rstrip("/"))


def iter_groups(group: h5py.Group) -> Iterator[h5py.Group]:
    for key in group.keys():
        item = group[key]
        if isinstance(item, h5py.Group):
            yield item


def find_data_dataset(group: h5py.Group) -> h5py.Dataset | None:
    for name in DATA_CANDIDATES:
        if name in group and isinstance(group[name], h5py.Dataset):
            return group[name]
    datasets = [v for v in group.values() if isinstance(v, h5py.Dataset)]
    if len(datasets) == 1:
        return datasets[0]
    return None


def read_array(group: h5py.Group | h5py.Dataset | None) -> np.ndarray | None:
    if group is None:
        return None
    if isinstance(group, h5py.Dataset):
        return np.array(group[()])
    ds = find_data_dataset(group)
    if ds is None:
        return None
    return np.array(ds[()])


def read_text(group: h5py.Group | None) -> str:
    arr = read_array(group)
    if arr is None:
        return ""
    if arr.dtype.kind in ("S", "a", "U", "O"):
        if arr.size == 1:
            return _as_str(arr.reshape(-1)[0])
        return " ".join(_as_str(x) for x in arr.reshape(-1) if _as_str(x))
    return _as_str(arr)


def child_by_label(group: h5py.Group, label: str) -> list[h5py.Group]:
    found = []
    for child in iter_groups(group):
        if node_label(child) == label:
            found.append(child)
    return found


def child_by_name(group: h5py.Group, name: str) -> h5py.Group | None:
    if name in group and isinstance(group[name], h5py.Group):
        return group[name]
    for child in iter_groups(group):
        if node_name(child) == name:
            return child
    return None


def walk_labeled(group: h5py.Group, label: str) -> Iterator[h5py.Group]:
    for child in iter_groups(group):
        if node_label(child) == label:
            yield child
        yield from walk_labeled(child, label)


def is_hdf5_cgns(path: str) -> bool:
    try:
        return h5py.is_hdf5(path)
    except OSError:
        return False


@dataclass
class FieldInfo:
    name: str
    location: str
    solution: str
    zone: str
    zone_id: int
    shape: tuple[int, ...]
    dtype: str

    @property
    def count(self) -> int:
        if not self.shape:
            return 0
        n = 1
        for d in self.shape:
            n *= int(d)
        return int(n)

    @property
    def location_cn(self) -> str:
        loc = (self.location or "").strip().lower()
        if loc.startswith("cell"):
            return "单元中心(CellCenter)"
        if loc.startswith("vertex") or loc in {"node", "nodes"}:
            return "节点(Vertex)"
        if loc.startswith("face"):
            return "面心(FaceCenter)"
        if loc.startswith("edge"):
            return "边心(EdgeCenter)"
        return self.location or "未知"


@dataclass
class ZoneInfo:
    name: str
    zone_id: int
    base: str
    zone_type: str
    size: list[int]
    n_vertex: int | None
    n_cell: int | None
    role: str
    bc_names: list[str] = field(default_factory=list)
    bc_types: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    element_types: list[str] = field(default_factory=list)
    solutions: list[str] = field(default_factory=list)


@dataclass
class FileInfo:
    path: str
    cgns_version: float | None
    storage_format: str
    cell_dim: int | None
    phys_dim: int | None
    bases: list[str]
    zones: list[ZoneInfo]
    fields: list[FieldInfo]
    times: list[float]
    solutions: list[str]
    notes: list[str] = field(default_factory=list)


class CgnsFile:
    def __init__(
        self,
        path: str,
        mode: str = "r",
        *,
        storage_format: str = "hdf5",
        original_path: str | None = None,
        cleanup_paths: list[str] | None = None,
    ):
        work = path
        if not os.path.isfile(work):
            raise FileNotFoundError(work)
        if not is_hdf5_cgns(work):
            raise ValueError(f"内部工作文件不是 HDF5: {work}")
        self.work_path = work
        self.path = original_path or path
        self.storage_format = storage_format.lower()
        self._cleanup_paths = list(cleanup_paths or [])
        self._f = h5py.File(work, mode)

    def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = None
        for p in self._cleanup_paths:
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        self._cleanup_paths.clear()

    def __enter__(self) -> "CgnsFile":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def root(self) -> h5py.File:
        if self._f is None:
            raise RuntimeError("文件已关闭")
        return self._f

    def format_label(self) -> str:
        return "ADF" if self.storage_format == "adf" else "HDF5"

    def cgns_version(self) -> float | None:
        node = self.root.get("CGNSLibraryVersion")
        if node is None:
            for child in iter_groups(self.root):
                if node_label(child) == "CGNSLibraryVersion_t":
                    node = child
                    break
        arr = read_array(node) if node is not None else None
        if arr is None:
            return None
        try:
            raw = float(np.array(arr).reshape(-1)[0])
        except (TypeError, ValueError):
            return None
        for cand in (2.4, 2.5, 3.0, 3.1, 3.2, 3.3, 3.4, 4.0, 4.1, 4.2, 4.3, 4.4, 4.5):
            if abs(raw - cand) < 5e-4:
                return cand
        return round(raw, 4)

    def bases(self) -> list[h5py.Group]:
        return child_by_label(self.root, "CGNSBase_t")

    def zones(self, base: h5py.Group | None = None) -> list[tuple[int, h5py.Group, h5py.Group]]:
        result = []
        bases = [base] if base is not None else self.bases()
        for b in bases:
            zid = 1
            for zone in child_by_label(b, "Zone_t"):
                result.append((zid, b, zone))
                zid += 1
        return result

    def zone_type(self, zone: h5py.Group) -> str:
        node = child_by_name(zone, "ZoneType")
        if node is None:
            found = child_by_label(zone, "ZoneType_t")
            node = found[0] if found else None
        return read_text(node) or "Unknown"

    def zone_size(self, zone: h5py.Group) -> list[int]:
        arr = read_array(zone)
        if arr is None:
            return []
        return [int(x) for x in np.array(arr).reshape(-1)]

    def coordinates(self, zone: h5py.Group) -> np.ndarray:
        gcs = child_by_label(zone, "GridCoordinates_t")
        if not gcs:
            raise ValueError(f"区域 {node_name(zone)} 没有 GridCoordinates")
        gc = gcs[0]
        axes = []
        for axis in ("CoordinateX", "CoordinateY", "CoordinateZ"):
            node = child_by_name(gc, axis)
            if node is None:
                continue
            arr = read_array(node)
            if arr is None:
                continue
            axes.append(np.array(arr, dtype=np.float64).reshape(-1))
        if not axes:
            raise ValueError(f"区域 {node_name(zone)} 没有坐标数组")
        n = min(a.size for a in axes)
        xyz = np.zeros((n, 3), dtype=np.float64)
        for i, a in enumerate(axes[:3]):
            xyz[:, i] = a[:n]
        return xyz

    def flow_solutions(self, zone: h5py.Group) -> list[h5py.Group]:
        sols = child_by_label(zone, "FlowSolution_t")
        sols.sort(key=lambda g: node_name(g))
        return sols

    def solution_fields(self, solution: h5py.Group) -> list[tuple[str, h5py.Group]]:
        fields = []
        for child in iter_groups(solution):
            label = node_label(child)
            name = node_name(child)
            if label in {"DataArray_t", ""} or name not in {
                "GridLocation",
                "Rind",
                "PointList",
                "PointRange",
                "DataClass",
                "DimensionalUnits",
            }:
                if label in {"GridLocation_t", "Rind_t", "IndexArray_t", "IndexRange_t", "Descriptor_t"}:
                    continue
                if find_data_dataset(child) is not None or label == "DataArray_t":
                    fields.append((name, child))
        return fields

    def grid_location(self, solution: h5py.Group) -> str:
        node = child_by_name(solution, "GridLocation")
        if node is None:
            found = child_by_label(solution, "GridLocation_t")
            node = found[0] if found else None
        return read_text(node) or "Vertex"

    def bc_nodes(self, zone: h5py.Group) -> list[h5py.Group]:
        bcs = []
        for zbc in child_by_label(zone, "ZoneBC_t"):
            bcs.extend(child_by_label(zbc, "BC_t"))
        return bcs

    def bc_type(self, bc: h5py.Group) -> str:
        node = child_by_name(bc, "BCType")
        if node is None:
            found = child_by_label(bc, "BCTypeSimple_t") + child_by_label(bc, "BCType_t")
            node = found[0] if found else None
        text = read_text(node)
        if text:
            return text
        return node_label(bc) or "Unknown"

    def element_sections(self, zone: h5py.Group) -> list[h5py.Group]:
        return child_by_label(zone, "Elements_t")

    def element_type_name(self, section: h5py.Group) -> str:
        arr = read_array(section)
        if arr is None:
            named = child_by_name(section, "ElementType")
            text = read_text(named)
            return text or "Unknown"
        code = int(np.array(arr).reshape(-1)[0])
        return ELEMENT_TYPE_NAMES.get(code, f"Unknown({code})")

    def time_values(self) -> list[float]:
        times: list[float] = []
        for base in self.bases():
            for node in child_by_label(base, "BaseIterativeData_t"):
                tv = child_by_name(node, "TimeValues")
                arr = read_array(tv)
                if arr is None:
                    continue
                times.extend(float(x) for x in np.array(arr, dtype=np.float64).reshape(-1))
        return times


def open_cgns(path: str, mode: str = "r") -> CgnsFile:
    """自动识别 ADF/HDF5。ADF 只读打开时会临时转为 HDF5。"""
    from .format_io import FormatError, detect_cgns_format, materialize_hdf5

    path = str(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    fmt = detect_cgns_format(path)
    if fmt == "hdf5":
        return CgnsFile(path, mode=mode, storage_format="hdf5", original_path=path)

    if fmt == "adf":
        if mode != "r":
            raise ValueError(
                "ADF 文件请以只读方式打开；写入/改版本请使用 convert 功能（会自动处理格式）。"
            )
        try:
            tmp = materialize_hdf5(path)
        except FormatError as exc:
            raise ValueError(str(exc)) from exc
        return CgnsFile(
            str(tmp.work_path),
            mode="r",
            storage_format="adf",
            original_path=str(tmp.original_path),
            cleanup_paths=[str(p) for p in tmp._cleanup],
        )

    raise ValueError(
        f"{path} 无法识别为 CGNS HDF5 或 ADF。"
        "若确认为 ADF，请安装并配置 cgnsconvert。"
    )


def classify_zone_role(zone_name: str, bc_types: list[str], families: list[str]) -> str:
    joined = " ".join([zone_name, *bc_types, *families]).upper()
    if any(k in joined for k in WALL_BC_KEYWORDS) and "FLUID" not in zone_name.upper():
        if all(any(k in t.upper() for k in WALL_BC_KEYWORDS) for t in bc_types if t) and bc_types:
            return "壁面边界"
    has_wall = any(any(k in t.upper() for k in WALL_BC_KEYWORDS) for t in bc_types)
    if "FLUID" in zone_name.upper() or any(h in joined for h in FLUID_HINTS):
        return "流体域（含壁面边界）" if has_wall else "流体域"
    if has_wall:
        return "含壁面边界的计算域"
    if bc_types:
        return "计算域（含边界）"
    return "计算域"
