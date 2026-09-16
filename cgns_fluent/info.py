"""汇总并格式化 CGNS 基本信息 / 数据结构。"""

from __future__ import annotations

import json
from typing import Any

from .hdf5_cgns import (
    CgnsFile,
    FileInfo,
    FieldInfo,
    ZoneInfo,
    classify_zone_role,
    node_name,
    read_array,
    read_text,
    child_by_label,
    child_by_name,
)


def collect_file_info(cgns: CgnsFile) -> FileInfo:
    bases = cgns.bases()
    cell_dim = phys_dim = None
    if bases:
        dims = read_array(bases[0])
        if dims is not None and len(dims.reshape(-1)) >= 2:
            flat = dims.reshape(-1)
            cell_dim = int(flat[0])
            phys_dim = int(flat[1])

    zones: list[ZoneInfo] = []
    fields: list[FieldInfo] = []
    solutions: list[str] = []
    notes: list[str] = []

    if not bases:
        notes.append("未找到 CGNSBase_t，文件可能不是标准 CGNS。")
    if cgns.storage_format == "adf":
        notes.append("源文件为 ADF：已自动经 cgnsconvert 转为临时 HDF5 后读取。")

    for zid, base, zone in cgns.zones():
        zname = node_name(zone)
        bname = node_name(base)
        ztype = cgns.zone_type(zone)
        size = cgns.zone_size(zone)
        n_vertex = n_cell = None
        if size:
            n_vertex = size[0]
            if len(size) > 1:
                n_cell = size[1]

        bc_nodes = cgns.bc_nodes(zone)
        bc_names = [node_name(bc) for bc in bc_nodes]
        bc_types = [cgns.bc_type(bc) for bc in bc_nodes]
        families = []
        fam = child_by_name(zone, "FamilyName")
        if fam is not None:
            text = read_text(fam)
            if text:
                families.append(text)
        for fam_node in child_by_label(zone, "FamilyName_t"):
            text = read_text(fam_node)
            if text and text not in families:
                families.append(text)

        elem_types = []
        for sec in cgns.element_sections(zone):
            et = cgns.element_type_name(sec)
            if et not in elem_types:
                elem_types.append(et)

        sol_names = []
        for sol in cgns.flow_solutions(zone):
            sname = node_name(sol)
            sol_names.append(sname)
            if sname not in solutions:
                solutions.append(sname)
            loc = cgns.grid_location(sol)
            for fname, fnode in cgns.solution_fields(sol):
                arr = read_array(fnode)
                shape = tuple(int(x) for x in arr.shape) if arr is not None else ()
                dtype = str(arr.dtype) if arr is not None else "unknown"
                fields.append(
                    FieldInfo(
                        name=fname,
                        location=loc,
                        solution=sname,
                        zone=zname,
                        zone_id=zid,
                        shape=shape,
                        dtype=dtype,
                    )
                )

        zones.append(
            ZoneInfo(
                name=zname,
                zone_id=zid,
                base=bname,
                zone_type=ztype,
                size=size,
                n_vertex=n_vertex,
                n_cell=n_cell,
                role=classify_zone_role(zname, bc_types, families),
                bc_names=bc_names,
                bc_types=bc_types,
                families=families,
                element_types=elem_types,
                solutions=sol_names,
            )
        )

    return FileInfo(
        path=cgns.path,
        cgns_version=cgns.cgns_version(),
        storage_format=cgns.format_label(),
        cell_dim=cell_dim,
        phys_dim=phys_dim,
        bases=[node_name(b) for b in bases],
        zones=zones,
        fields=fields,
        times=cgns.time_values(),
        solutions=solutions,
        notes=notes,
    )


def file_info_to_dict(info: FileInfo) -> dict[str, Any]:
    """结构化字典，便于 JSON 导出。"""
    zones = []
    for z in info.zones:
        zones.append(
            {
                "zone_id": z.zone_id,
                "name": z.name,
                "zone_type": z.zone_type,
                "role": z.role,
                "base": z.base,
                "n_vertex": z.n_vertex,
                "n_cell": z.n_cell,
                "element_types": z.element_types,
                "boundaries": [
                    {"name": n, "type": t} for n, t in zip(z.bc_names, z.bc_types)
                ],
                "families": z.families,
                "solutions": z.solutions,
            }
        )

    fields = []
    for f in info.fields:
        fields.append(
            {
                "name": f.name,
                "zone_id": f.zone_id,
                "zone_name": f.zone,
                "solution": f.solution,
                "location": f.location,
                "location_cn": f.location_cn,
                "count": f.count,
                "shape": list(f.shape),
                "dtype": f.dtype,
            }
        )

    return {
        "file": info.path,
        "cgns_version": info.cgns_version,
        "storage_format": info.storage_format,
        "bases": info.bases,
        "cell_dim": info.cell_dim,
        "phys_dim": info.phys_dim,
        "times": info.times,
        "solutions": info.solutions,
        "zones": zones,
        "fields": fields,
        "notes": info.notes,
    }


def format_file_info(info: FileInfo) -> str:
    lines = [
        "======== CGNS 文件数据结构 ========",
        f"文件: {info.path}",
        f"CGNS 版本号: {info.cgns_version if info.cgns_version is not None else '未知'}",
        f"存储格式: CGNS/{info.storage_format}",
        f"Base: {', '.join(info.bases) if info.bases else '无'}",
        f"CellDim/PhysDim: {info.cell_dim}/{info.phys_dim}",
        f"区域数量: {len(info.zones)}",
        f"物理场条目数: {len(info.fields)}",
    ]
    if info.times:
        preview = ", ".join(f"{t:.6g}" for t in info.times[:8])
        extra = f" ... 共 {len(info.times)} 步" if len(info.times) > 8 else ""
        lines.append(f"瞬态时间步: {preview}{extra}")
    else:
        lines.append("瞬态时间步: 未在 BaseIterativeData 中找到 TimeValues（可能每个文件对应一个时刻）")

    lines.append("")
    lines.append("=== 1. 区域列表（名称 / ID / 类型 / 数量）===")
    if not info.zones:
        lines.append("  （无 Zone）")
    else:
        lines.append(
            f"  {'ID':<6}{'名称':<24}{'网格类型':<16}{'区域角色':<22}{'节点数':>10}{'单元数':>10}"
        )
        lines.append("  " + "-" * 88)
        for z in info.zones:
            nv = "-" if z.n_vertex is None else str(z.n_vertex)
            nc = "-" if z.n_cell is None else str(z.n_cell)
            lines.append(
                f"  {z.zone_id:<6}{z.name:<24}{z.zone_type:<16}{z.role:<22}{nv:>10}{nc:>10}"
            )

        lines.append("")
        lines.append("=== 1.1 区域明细 ===")
        for z in info.zones:
            lines.append(f"  [Zone ID={z.zone_id}] 名称={z.name}")
            lines.append(f"    网格类型: {z.zone_type}")
            lines.append(f"    区域角色/类型: {z.role}")
            lines.append(f"    Base: {z.base}")
            lines.append(f"    节点数量: {z.n_vertex if z.n_vertex is not None else '未知'}")
            lines.append(f"    单元数量: {z.n_cell if z.n_cell is not None else '未知'}")
            if z.element_types:
                lines.append(f"    单元拓扑: {', '.join(z.element_types)}")
            if z.families:
                lines.append(f"    Family: {', '.join(z.families)}")
            if z.bc_names:
                pairs = [f"{n}({t})" for n, t in zip(z.bc_names, z.bc_types)]
                lines.append(f"    边界条件: {', '.join(pairs)}")
            else:
                lines.append("    边界条件: 无 ZoneBC（通常为体网格/流体域）")
            if z.solutions:
                lines.append(f"    解节点(FlowSolution): {', '.join(z.solutions)}")

    lines.append("")
    lines.append("=== 2. 物理场列表（名称 / 所属区域 / 节点或单元中心 / 数量）===")
    if not info.fields:
        lines.append("  （未找到 FlowSolution 场变量）")
    else:
        lines.append(
            f"  {'物理场':<22}{'区域ID':<8}{'区域名称':<18}{'解节点':<16}"
            f"{'位置':<22}{'数量':>10}{'形状':<16}"
        )
        lines.append("  " + "-" * 112)
        for f in info.fields:
            shape_s = str(f.shape)
            lines.append(
                f"  {f.name:<22}{f.zone_id:<8}{f.zone:<18}{f.solution:<16}"
                f"{f.location_cn:<22}{f.count:>10}{shape_s:<16}"
            )

        # 按位置汇总
        by_loc: dict[str, list[str]] = {}
        for f in info.fields:
            by_loc.setdefault(f.location_cn, [])
            if f.name not in by_loc[f.location_cn]:
                by_loc[f.location_cn].append(f.name)
        lines.append("")
        lines.append("=== 2.1 按存储位置汇总 ===")
        for loc, names in by_loc.items():
            lines.append(f"  {loc}: {len(names)} 个场 -> {', '.join(names)}")

    if info.notes:
        lines.append("")
        lines.append("=== 备注 ===")
        for n in info.notes:
            lines.append(f"  {n}")
    return "\n".join(lines)


def format_file_info_json(info: FileInfo) -> str:
    return json.dumps(file_info_to_dict(info), ensure_ascii=False, indent=2)
