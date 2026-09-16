"""CGNS 3.4 <-> 4.1 版本转换（含 NGON/NFACE CPEX 0041 布局）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import h5py
import numpy as np

from .hdf5_cgns import (
    ELEMENT_TYPE_NAMES,
    child_by_label,
    find_data_dataset,
    iter_groups,
    node_label,
    node_name,
    read_array,
)

SUPPORTED = {3.4, 4.1}
NGON = 23
NFACE = 24
MIXED = 21


class ConvertError(RuntimeError):
    pass


def _dataset_name(group: h5py.Group) -> str:
    ds = find_data_dataset(group)
    if ds is not None:
        return ds.name.split("/")[-1]
    return " data"


def write_array(group: h5py.Group, data: np.ndarray) -> None:
    name = _dataset_name(group)
    if name in group:
        del group[name]
    group.create_dataset(name, data=data)


def read_section_connectivity(
    section: h5py.Group,
) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    conn_node = None
    off_node = None
    for child in iter_groups(section):
        n = node_name(child)
        if n == "ElementConnectivity":
            conn_node = child
        elif n == "ElementStartOffset":
            off_node = child
    conn = read_array(conn_node) if conn_node is not None else None
    off = read_array(off_node) if off_node is not None else None
    if conn is not None:
        conn = np.array(conn).reshape(-1)
    if off is not None:
        off = np.array(off).reshape(-1)
    return conn, off, conn_node


def element_type_code(section: h5py.Group) -> int | None:
    arr = read_array(section)
    if arr is None:
        return None
    return int(np.array(arr).reshape(-1)[0])


def _normalize_offsets(off: np.ndarray, conn_len: int) -> np.ndarray:
    off = np.array(off, dtype=np.int64).reshape(-1)
    if off.size < 2:
        raise ConvertError("ElementStartOffset 长度不足")
    if off[0] == 1:
        off = off - 1
    return off


def connectivity_is_interleaved(conn: np.ndarray, off: np.ndarray | None) -> bool:
    conn = np.asarray(conn).reshape(-1)
    if conn.size == 0:
        return False
    if off is not None and off.size >= 2:
        off_n = _normalize_offsets(off, conn.size)
        packed = int(off_n[-1])
        n_elem = off_n.size - 1
        if packed == conn.size:
            return False
        if packed + n_elem == conn.size:
            return True
    first = int(conn[0])
    return 1 <= first <= 256


def interleaved_to_packed(conn: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    conn = np.asarray(conn).reshape(-1)
    packed: list[int] = []
    offsets = [0]
    i = 0
    n = conn.size
    while i < n:
        count = int(conn[i])
        if count <= 0 or i + 1 + count > n:
            raise ConvertError(
                f"无法按 3.x 交错格式解析 ElementConnectivity（位置 {i}, count={count}）"
            )
        packed.extend(int(x) for x in conn[i + 1 : i + 1 + count])
        offsets.append(len(packed))
        i += 1 + count
    if i != n:
        raise ConvertError("交错格式解析后仍有剩余数据")
    return np.asarray(packed, dtype=conn.dtype), np.asarray(offsets, dtype=np.int64)


def packed_to_interleaved(conn: np.ndarray, off: np.ndarray) -> np.ndarray:
    conn = np.asarray(conn).reshape(-1)
    off = _normalize_offsets(off, conn.size)
    parts = []
    for i in range(off.size - 1):
        sl = conn[off[i] : off[i + 1]]
        parts.append([sl.size, *sl.tolist()])
    if not parts:
        return np.array([], dtype=conn.dtype)
    return np.asarray(np.concatenate(parts), dtype=conn.dtype)


def mixed_offsets_from_types(conn: np.ndarray) -> np.ndarray:
    """按 MIXED 旧布局（每单元以类型码开头）生成 0-based offset。"""
    from .hdf5_cgns import ELEMENT_TYPE_NAMES

    nodes_per_type = {
        2: 1,
        3: 2,
        4: 3,
        5: 3,
        6: 6,
        7: 4,
        8: 8,
        9: 9,
        10: 4,
        11: 10,
        12: 5,
        13: 13,
        14: 14,
        15: 6,
        16: 15,
        17: 18,
        18: 8,
        19: 20,
        20: 27,
    }
    conn = np.asarray(conn).reshape(-1)
    offsets = [0]
    i = 0
    n = conn.size
    while i < n:
        etype = int(conn[i])
        if etype in (NGON, NFACE):
            if i + 1 >= n:
                raise ConvertError("MIXED 中 NGON/NFACE 数据不完整")
            count = int(conn[i + 1])
            i += 2 + count
        else:
            npe = nodes_per_type.get(etype)
            if npe is None:
                raise ConvertError(f"MIXED 含不支持的单元类型 {etype} ({ELEMENT_TYPE_NAMES.get(etype, '?')})")
            i += 1 + npe
        offsets.append(i)
    return np.asarray(offsets, dtype=np.int64)


def _clone_array_group(
    parent: h5py.Group,
    template: h5py.Group,
    name: str,
    data: np.ndarray,
    label: str = "DataArray_t",
) -> h5py.Group:
    if name in parent:
        del parent[name]
    grp = parent.create_group(name)
    for k, v in template.attrs.items():
        grp.attrs[k] = v
    grp.attrs["name"] = np.bytes_(name)
    grp.attrs["label"] = np.bytes_(label)
    ds_name = _dataset_name(template)
    grp.create_dataset(ds_name, data=data)
    return grp


def _ensure_offset_group(section: h5py.Group, offsets: np.ndarray) -> None:
    conn_node = None
    off_node = None
    for child in iter_groups(section):
        n = node_name(child)
        if n == "ElementConnectivity":
            conn_node = child
        elif n == "ElementStartOffset":
            off_node = child
    if conn_node is None:
        raise ConvertError(f"{node_name(section)} 缺少 ElementConnectivity")
    if off_node is None:
        _clone_array_group(section, conn_node, "ElementStartOffset", offsets.astype(np.int64))
    else:
        write_array(off_node, offsets.astype(np.int64))


def _delete_offset_group(section: h5py.Group) -> None:
    for key in list(section.keys()):
        item = section[key]
        if isinstance(item, h5py.Group) and node_name(item) == "ElementStartOffset":
            del section[key]


def convert_section(section: h5py.Group, target: float) -> str | None:
    code = element_type_code(section)
    if code not in (NGON, NFACE, MIXED):
        return None
    conn, off, conn_node = read_section_connectivity(section)
    if conn is None or conn_node is None:
        return f"{node_name(section)} 无 ElementConnectivity，跳过"
    et_name = ELEMENT_TYPE_NAMES.get(code, str(code))

    if code == MIXED:
        if target == 4.1:
            if off is None:
                try:
                    new_off = mixed_offsets_from_types(conn)
                except ConvertError as exc:
                    return f"{node_name(section)} MIXED: {exc}"
                _ensure_offset_group(section, new_off)
                return f"{node_name(section)} ({et_name}) 已补充 ElementStartOffset"
            return f"{node_name(section)} ({et_name}) 已是 4.x offset 布局"
        if off is not None:
            _delete_offset_group(section)
            return f"{node_name(section)} ({et_name}) 已移除 ElementStartOffset（3.4）"
        return f"{node_name(section)} ({et_name}) 无需改连通性"

    interleaved = connectivity_is_interleaved(conn, off)
    if target == 4.1:
        if interleaved:
            packed, offsets = interleaved_to_packed(conn)
            write_array(conn_node, packed)
            _ensure_offset_group(section, offsets)
            return f"{node_name(section)} ({et_name}) 3.x 交错 -> 4.1 packed+offset"
        if off is None:
            raise ConvertError(
                f"{node_name(section)} 已是 packed 连通性但缺少 ElementStartOffset，无法安全转为 4.1"
            )
        return f"{node_name(section)} ({et_name}) 已是 4.x 布局"

    # target 3.4
    if interleaved:
        if off is not None:
            _delete_offset_group(section)
            return f"{node_name(section)} ({et_name}) 保持 3.x 交错并移除 offset"
        return f"{node_name(section)} ({et_name}) 已是 3.x 交错布局"
    if off is None:
        raise ConvertError(f"{node_name(section)} packed 连通性缺少 ElementStartOffset，无法转回 3.4")
    interleaved_conn = packed_to_interleaved(conn, off)
    write_array(conn_node, interleaved_conn)
    _delete_offset_group(section)
    return f"{node_name(section)} ({et_name}) 4.x packed -> 3.4 交错"


def _set_version(root: h5py.File, version: float) -> None:
    node = root.get("CGNSLibraryVersion")
    if node is None:
        for child in iter_groups(root):
            if node_label(child) == "CGNSLibraryVersion_t" or node_name(child) == "CGNSLibraryVersion":
                node = child
                break
    data = np.array([version], dtype=np.float32)
    if node is None:
        grp = root.create_group("CGNSLibraryVersion")
        grp.attrs["name"] = np.bytes_("CGNSLibraryVersion")
        grp.attrs["label"] = np.bytes_("CGNSLibraryVersion_t")
        grp.attrs["type"] = np.bytes_("R4")
        grp.create_dataset(" data", data=data)
        return
    if isinstance(node, h5py.Dataset):
        parent = node.parent
        name = node.name.split("/")[-1]
        del parent[name]
        parent.create_dataset(name, data=data)
        return
    write_array(node, data)


def _convert_hdf5_inplace(hdf5_path: Path, target: float) -> list[str]:
    logs: list[str] = []
    with h5py.File(hdf5_path, "r+") as f:
        n_sec = 0
        for base in child_by_label(f, "CGNSBase_t"):
            for zone in child_by_label(base, "Zone_t"):
                for section in child_by_label(zone, "Elements_t"):
                    n_sec += 1
                    msg = convert_section(section, target)
                    if msg:
                        logs.append(msg)
        _set_version(f, target)
        logs.append(f"已写入 CGNSLibraryVersion = {target}")
        if n_sec == 0:
            logs.append("未找到 Elements_t；仅更新了版本号（结构化网格常见）。")
    return logs


def convert_version(
    src: str,
    dst: str,
    target: float,
    *,
    output_format: str | None = None,
    cgnsconvert: str | None = None,
) -> list[str]:
    """版本转换；自动识别 ADF/HDF5，并按配置写出目标存储格式。"""
    from .format_io import (
        FormatError,
        detect_cgns_format,
        export_as,
        resolve_output_format,
        to_hdf5,
    )

    target = float(target)
    if target not in SUPPORTED:
        raise ConvertError(f"仅支持目标版本 {sorted(SUPPORTED)}，收到 {target}")
    src_p = Path(src)
    dst_p = Path(dst)
    if not src_p.is_file():
        raise FileNotFoundError(src)
    if src_p.resolve() == dst_p.resolve():
        raise ConvertError("输出路径不能与输入文件相同，请指定新文件")
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    try:
        src_fmt = detect_cgns_format(src_p)
    except FormatError as exc:
        raise ConvertError(str(exc)) from exc
    if src_fmt not in {"hdf5", "adf"}:
        raise ConvertError(f"无法识别输入格式（需 HDF5 或 ADF）: {src_p}")

    out_fmt = resolve_output_format(src_fmt, output_format)
    logs = [
        f"源文件: {src_p}",
        f"输出: {dst_p}",
        f"源存储格式: {src_fmt.upper()}",
        f"目标存储格式: {out_fmt.upper()}",
        f"目标 CGNS 版本: {target}",
    ]

    with tempfile.TemporaryDirectory(prefix="cgns_cvt_") as td:
        work = Path(td) / "work.cgns"
        try:
            to_hdf5(src_p, work, tool=cgnsconvert)
        except FormatError as exc:
            raise ConvertError(str(exc)) from exc
        logs.append("已准备 HDF5 工作副本用于修改")
        logs.extend(_convert_hdf5_inplace(work, target))
        try:
            export_as(work, dst_p, target_format=out_fmt, tool=cgnsconvert)
        except FormatError as exc:
            raise ConvertError(str(exc)) from exc
        logs.append(f"已导出为 CGNS/{out_fmt.upper()}")
    return logs
