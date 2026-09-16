"""按坐标与搜索半径查询节点物理场，并按同坐标导出时间序列 CSV。"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .convert import interleaved_to_packed, read_section_connectivity
from .hdf5_cgns import CgnsFile, node_name


@dataclass
class Hit:
    zone_id: int
    zone: str
    local_index: int  # 0-based
    xyz: tuple[float, float, float]
    distance: float
    location: str
    values: dict[str, float]


@dataclass
class TimedHit:
    time: float
    hit: Hit
    source_file: str = ""


def _as_packed(conn: np.ndarray, off: np.ndarray | None) -> tuple[np.ndarray, np.ndarray] | None:
    if off is not None and off.size >= 2:
        o = np.array(off, dtype=np.int64).reshape(-1)
        if o[0] == 1:
            o = o - 1
        if int(o[-1]) == conn.size:
            return conn, o
    try:
        packed, offsets = interleaved_to_packed(conn)
        return packed, offsets
    except Exception:
        return None


def _cell_centers(xyz: np.ndarray, zone, cgns: CgnsFile) -> np.ndarray | None:
    """由 NGON/NFACE 估算单元中心；失败则返回 None。"""
    sections = {cgns.element_type_name(s): s for s in cgns.element_sections(zone)}
    ngon = sections.get("NGON_n")
    nface = sections.get("NFACE_n")
    if ngon is None or nface is None:
        return None

    ngon_conn, ngon_off, _ = read_section_connectivity(ngon)
    nface_conn, nface_off, _ = read_section_connectivity(nface)
    if ngon_conn is None or nface_conn is None:
        return None
    ngon_pack = _as_packed(ngon_conn, ngon_off)
    nface_pack = _as_packed(nface_conn, nface_off)
    if ngon_pack is None or nface_pack is None:
        return None
    ngon_conn, ngon_off = ngon_pack
    nface_conn, nface_off = nface_pack

    n_cell = nface_off.size - 1
    centers = np.zeros((n_cell, 3), dtype=np.float64)
    n_vertex = xyz.shape[0]
    for i in range(n_cell):
        faces = nface_conn[nface_off[i] : nface_off[i + 1]]
        pts = []
        for f in faces:
            fi = abs(int(f)) - 1
            if fi < 0 or fi >= ngon_off.size - 1:
                continue
            nodes = ngon_conn[ngon_off[fi] : ngon_off[fi + 1]]
            for n in nodes:
                ni = int(n) - 1
                if 0 <= ni < n_vertex:
                    pts.append(xyz[ni])
        if pts:
            centers[i] = np.mean(np.vstack(pts), axis=0)
        else:
            centers[i] = np.nan
    return centers


def _nearest_solution(cgns: CgnsFile, zone, solution_name: str | None, time: float | None):
    sols = cgns.flow_solutions(zone)
    if not sols:
        return None
    if solution_name:
        for s in sols:
            if node_name(s) == solution_name:
                return s
        raise ValueError(f"找不到解节点 {solution_name}，可选: {[node_name(s) for s in sols]}")
    if time is not None:
        times = cgns.time_values()
        if times:
            idx = int(np.argmin([abs(t - time) for t in times]))
            if idx < len(sols):
                return sols[idx]
    return sols[-1]


def _field_array(solution, field_name: str) -> np.ndarray:
    from .hdf5_cgns import child_by_name, read_array as _read

    node = child_by_name(solution, field_name)
    if node is None:
        raise KeyError(field_name)
    arr = _read(node)
    if arr is None:
        raise KeyError(field_name)
    return np.array(arr, dtype=np.float64).reshape(-1)


def resolve_query_time(cgns: CgnsFile, preferred: float | None = None) -> float:
    """确定本文件/本步对应的时间。"""
    if preferred is not None:
        return float(preferred)
    times = cgns.time_values()
    if times:
        return float(times[-1])
    return float("nan")


def query_points(
    cgns: CgnsFile,
    xyz: tuple[float, float, float],
    radius: float,
    fields: list[str] | None = None,
    zone: str | int | None = None,
    solution: str | None = None,
    time: float | None = None,
    max_hits: int = 50,
) -> list[Hit]:
    if radius < 0:
        raise ValueError("搜索半径必须 >= 0")
    target = np.array(xyz, dtype=np.float64)
    hits: list[Hit] = []

    selected = []
    for zid, _base, znode in cgns.zones():
        zname = node_name(znode)
        if zone is None:
            selected.append((zid, znode, zname))
        elif isinstance(zone, int) and zid == zone:
            selected.append((zid, znode, zname))
        elif isinstance(zone, str) and zname == zone:
            selected.append((zid, znode, zname))

    if not selected:
        raise ValueError("没有匹配的 Zone")

    for zid, znode, zname in selected:
        coords = cgns.coordinates(znode)
        sol = _nearest_solution(cgns, znode, solution, time)
        loc = cgns.grid_location(sol) if sol is not None else "Vertex"

        if loc.lower().startswith("cell"):
            centers = _cell_centers(coords, znode, cgns)
            search_xyz = centers if centers is not None else coords
            used_loc = "CellCenter" if centers is not None else "Vertex(fallback)"
        else:
            search_xyz = coords
            used_loc = "Vertex"

        dist = np.linalg.norm(search_xyz - target[None, :], axis=1)
        idx = np.where(np.isfinite(dist) & (dist <= radius))[0]
        if idx.size == 0:
            continue
        order = idx[np.argsort(dist[idx])]

        available = []
        if sol is not None:
            available = [name for name, _ in cgns.solution_fields(sol)]
        want = fields if fields else available
        cached: dict[str, np.ndarray] = {}
        for fname in want:
            try:
                cached[fname] = _field_array(sol, fname)
            except (KeyError, TypeError):
                cached[fname] = np.array([])

        for i in order:
            values = {}
            for fname in want:
                arr = cached.get(fname)
                if arr is not None and i < arr.size:
                    values[fname] = float(arr[i])
                else:
                    values[fname] = float("nan")
            hits.append(
                Hit(
                    zone_id=zid,
                    zone=zname,
                    local_index=int(i),
                    xyz=tuple(float(x) for x in search_xyz[i]),
                    distance=float(dist[i]),
                    location=used_loc,
                    values=values,
                )
            )
            if len(hits) >= max_hits:
                return hits
    hits.sort(key=lambda h: h.distance)
    return hits[:max_hits]


def format_hits_summary(
    hits: list[Hit],
    xyz: tuple[float, float, float],
    radius: float,
    csv_files: list[Path] | None = None,
) -> str:
    """终端用总结，不打印逐点明细。"""
    lines = [
        f"查询点: ({xyz[0]}, {xyz[1]}, {xyz[2]})  半径: {radius}",
        f"命中点数: {len(hits)}",
    ]
    if csv_files:
        lines.append(f"CSV 文件数: {len(csv_files)}")
        for p in csv_files:
            lines.append(f"  - {p}")
    return "\n".join(lines)


def format_hits(
    hits: list[Hit],
    xyz: tuple[float, float, float],
    radius: float,
) -> str:
    """详细表格式（写入文件时可用；终端默认不用）。"""
    lines = [
        f"查询点: ({xyz[0]}, {xyz[1]}, {xyz[2]})  半径: {radius}",
        f"命中点数: {len(hits)}",
    ]
    if not hits:
        lines.append("（半径内没有节点/单元中心）")
        return "\n".join(lines)

    field_names = []
    for h in hits:
        for k in h.values:
            if k not in field_names:
                field_names.append(k)

    header = ["zone_id", "zone", "index(1-based)", "x", "y", "z", "dist", "location"] + field_names
    lines.append("\t".join(header))
    for h in hits:
        row = [
            str(h.zone_id),
            h.zone,
            str(h.local_index + 1),
            f"{h.xyz[0]:.8g}",
            f"{h.xyz[1]:.8g}",
            f"{h.xyz[2]:.8g}",
            f"{h.distance:.8g}",
            h.location,
        ]
        for f in field_names:
            v = h.values.get(f, float("nan"))
            row.append(f"{v:.8g}" if v == v else "nan")
        lines.append("\t".join(row))
    return "\n".join(lines)


def _coord_key(xyz: tuple[float, float, float], decimals: int) -> tuple[float, float, float]:
    return (
        round(float(xyz[0]), decimals),
        round(float(xyz[1]), decimals),
        round(float(xyz[2]), decimals),
    )


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\-.]+", "_", text).strip("_") or "node"


def _resolve_field_columns(records: list[TimedHit], field_names: list[str]) -> list[str]:
    if field_names:
        return list(field_names)
    cols: list[str] = []
    for rec in records:
        for name in rec.hit.values:
            if name not in cols:
                cols.append(name)
    return cols


def _group_records(
    records: list[TimedHit],
    coord_decimals: int,
) -> tuple[
    dict[tuple[float, float, float], list[TimedHit]],
    dict[tuple[float, float, float], Hit],
]:
    grouped: dict[tuple[float, float, float], list[TimedHit]] = defaultdict(list)
    meta: dict[tuple[float, float, float], Hit] = {}
    for rec in records:
        key = _coord_key(rec.hit.xyz, coord_decimals)
        grouped[key].append(rec)
        if key not in meta:
            meta[key] = rec.hit
    return grouped, meta


def _dedupe_by_time(items: list[TimedHit]) -> list[TimedHit]:
    items_sorted = sorted(
        items, key=lambda r: (r.time if r.time == r.time else 1e300, r.source_file)
    )
    by_time: dict[float, TimedHit] = {}
    for rec in items_sorted:
        tkey = rec.time if rec.time == rec.time else float("nan")
        by_time[tkey] = rec
    return sorted(by_time.values(), key=lambda r: (r.time if r.time == r.time else 1e300))


def compute_fluctuation(values: list[float]) -> float:
    """
    脉动值（相对均值的均方根）：

        φ' = sqrt( (1/N) * Σ (φ_i - φ̄)^2 )

    其中 φ̄ = (1/N) * Σ φ_i ，N 为有效（非 NaN）采样点数。
    """
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    mean = float(np.mean(arr))
    return float(np.sqrt(np.mean((arr - mean) ** 2)))


@dataclass
class NodeSeriesResult:
    key: tuple[float, float, float]
    sample: Hit
    times: list[float]
    series: dict[str, list[float]]
    fluct: dict[str, float]
    csv_path: Path | None = None
    plot_path: Path | None = None


@dataclass
class QueryExportResult:
    node_results: list[NodeSeriesResult] = field(default_factory=list)
    csv_files: list[Path] = field(default_factory=list)
    plot_files: list[Path] = field(default_factory=list)
    fluct_table_txt: Path | None = None
    fluct_table_csv: Path | None = None
    fluct_table: str = ""


def _stem_for_node(key: tuple[float, float, float], sample: Hit) -> str:
    fname = (
        f"query_x{key[0]:.8g}_y{key[1]:.8g}_z{key[2]:.8g}"
        f"_zone{sample.zone_id}_node{sample.local_index + 1}"
    )
    return _safe_name(fname).replace("__", "_")


def export_timeseries_csv(
    records: list[TimedHit],
    field_names: list[str],
    out_dir: Path,
    *,
    coord_decimals: int = 8,
) -> list[Path]:
    """兼容旧接口：仅导出 CSV。"""
    result = export_query_timeseries(
        records,
        field_names,
        out_dir,
        coord_decimals=coord_decimals,
        make_plots=False,
        write_fluct_table=False,
    )
    return result.csv_files


def plot_node_timeseries(
    times: list[float],
    series: dict[str, list[float]],
    out_path: Path,
    *,
    title: str,
) -> Path:
    """为单个坐标的时序结果画折线图。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
    t = np.asarray(times, dtype=np.float64)
    for name, vals in series.items():
        y = np.asarray(vals, dtype=np.float64)
        mask = np.isfinite(t) & np.isfinite(y)
        if not np.any(mask):
            continue
        ax.plot(t[mask], y[mask], marker="o", markersize=3, linewidth=1.2, label=name)
    ax.set_xlabel("time")
    ax.set_ylabel("value")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    if series:
        ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def _plot_job(
    times: list[float],
    series: dict[str, list[float]],
    out_path: str,
    title: str,
) -> tuple[str, str | None]:
    try:
        plot_node_timeseries(times, series, Path(out_path), title=title)
        return out_path, None
    except Exception as exc:  # noqa: BLE001
        return out_path, str(exc)


def _run_plot_jobs(
    jobs: list[tuple[list[float], dict[str, list[float]], Path, str]],
    workers: int,
) -> tuple[int, list[str]]:
    warnings: list[str] = []
    n_ok = 0
    if workers <= 1 or len(jobs) <= 1:
        for times, series, path, title in jobs:
            _, err = _plot_job(times, series, str(path), title)
            if err:
                warnings.append(f"[警告] 折线图生成失败 {path.stem}: {err}")
            else:
                n_ok += 1
        return n_ok, warnings

    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

    payloads = [(t, s, str(p), title) for t, s, p, title in jobs]
    try:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            futs = [pool.submit(_plot_job, *item) for item in payloads]
            for fut in as_completed(futs):
                path, err = fut.result()
                if err:
                    warnings.append(f"[警告] 折线图生成失败 {Path(path).stem}: {err}")
                else:
                    n_ok += 1
    except (OSError, PermissionError, RuntimeError):
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            futs = [pool.submit(_plot_job, *item) for item in payloads]
            for fut in as_completed(futs):
                path, err = fut.result()
                if err:
                    warnings.append(f"[警告] 折线图生成失败 {Path(path).stem}: {err}")
                else:
                    n_ok += 1
    return n_ok, warnings


def format_fluct_table(node_results: list[NodeSeriesResult], field_names: list[str]) -> str:
    """终端/报告用的脉动值统计表。"""
    cols = field_names or sorted({k for n in node_results for k in n.fluct})
    lines = [
        "======== 各坐标位置 脉动值 统计 ========",
        "计算公式:",
        "  φ' = sqrt( (1/N) * Σ_i (φ_i - φ̄)^2 )",
        "  φ̄ = (1/N) * Σ_i φ_i",
        "  其中 φ_i 为时序采样值，N 为有效采样点数（不含 NaN）。",
        "",
    ]
    if not node_results:
        lines.append("（无数据）")
        return "\n".join(lines)

    header = ["x", "y", "z", "zone_id", "node", "n_steps"] + [f"{c}_fluct" for c in cols]
    widths = [12, 12, 12, 8, 8, 8] + [max(14, len(c) + 6) for c in cols]

    def fmt_row(cells: list[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            w = widths[i] if i < len(widths) else 14
            parts.append(f"{cell:<{w}}")
        return "  " + "".join(parts)

    lines.append(fmt_row(header))
    lines.append("  " + "-" * (sum(widths) + 2))
    for n in node_results:
        cells = [
            f"{n.key[0]:.8g}",
            f"{n.key[1]:.8g}",
            f"{n.key[2]:.8g}",
            str(n.sample.zone_id),
            str(n.sample.local_index + 1),
            str(len(n.times)),
        ]
        for c in cols:
            v = n.fluct.get(c, float("nan"))
            cells.append("nan" if v != v else f"{v:.8g}")
        lines.append(fmt_row(cells))
    return "\n".join(lines)


def export_query_timeseries(
    records: list[TimedHit],
    field_names: list[str],
    out_dir: Path,
    *,
    coord_decimals: int = 8,
    make_plots: bool = True,
    write_fluct_table: bool = True,
    plot_workers: int = 1,
) -> QueryExportResult:
    """
    按坐标分组：写 CSV、算脉动值表、画折线图（与 CSV 同目录）。
    """
    result = QueryExportResult()
    if not records:
        return result

    out_dir.mkdir(parents=True, exist_ok=True)
    cols = _resolve_field_columns(records, field_names)
    grouped, meta = _group_records(records, coord_decimals)
    plot_warnings: list[str] = []
    plot_jobs: list[tuple[list[float], dict[str, list[float]], Path, str]] = []

    for key, items in sorted(grouped.items(), key=lambda kv: kv[0]):
        rows = _dedupe_by_time(items)
        sample = meta[key]
        stem = _stem_for_node(key, sample)
        times = [float(r.time) for r in rows]
        series: dict[str, list[float]] = {c: [] for c in cols}
        for rec in rows:
            for c in cols:
                series[c].append(float(rec.hit.values.get(c, float("nan"))))
        fluct = {c: compute_fluctuation(series[c]) for c in cols}

        csv_path = out_dir / f"{stem}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time", *cols])
            for i, rec in enumerate(rows):
                row = ["" if times[i] != times[i] else f"{times[i]:.16g}"]
                for c in cols:
                    v = series[c][i]
                    row.append("" if v != v else f"{v:.16g}")
                writer.writerow(row)
        result.csv_files.append(csv_path)

        plot_path = None
        if make_plots:
            plot_path = out_dir / f"{stem}.png"
            title = (
                f"zone={sample.zone_id} node={sample.local_index + 1} "
                f"({key[0]:.6g}, {key[1]:.6g}, {key[2]:.6g})"
            )
            plot_jobs.append((times, series, plot_path, title))

        result.node_results.append(
            NodeSeriesResult(
                key=key,
                sample=sample,
                times=times,
                series=series,
                fluct=fluct,
                csv_path=csv_path,
                plot_path=plot_path,
            )
        )

    if plot_jobs:
        n_ok, warnings = _run_plot_jobs(plot_jobs, max(1, int(plot_workers or 1)))
        plot_warnings.extend(warnings)
        result.plot_files = [job[2] for job in plot_jobs if job[2].is_file()]
        for node, job in zip(result.node_results, plot_jobs):
            if not job[2].is_file():
                node.plot_path = None

    result.fluct_table = format_fluct_table(result.node_results, cols)
    if plot_warnings:
        result.fluct_table += "\n" + "\n".join(plot_warnings)
    if write_fluct_table and result.node_results:
        txt_path = out_dir / "query_fluct_summary.txt"
        csv_sum = out_dir / "query_fluct_summary.csv"
        txt_path.write_text(result.fluct_table + "\n", encoding="utf-8")
        with csv_sum.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["x", "y", "z", "zone_id", "node", "n_steps"] + [f"{c}_fluct" for c in cols]
            )
            for n in result.node_results:
                row = [
                    f"{n.key[0]:.16g}",
                    f"{n.key[1]:.16g}",
                    f"{n.key[2]:.16g}",
                    n.sample.zone_id,
                    n.sample.local_index + 1,
                    len(n.times),
                ]
                for c in cols:
                    v = n.fluct.get(c, float("nan"))
                    row.append("" if v != v else f"{v:.16g}")
                writer.writerow(row)
        result.fluct_table_txt = txt_path
        result.fluct_table_csv = csv_sum

    return result


def format_query_batch_summary(
    n_files: int,
    n_hits_raw: int,
    n_nodes: int,
    csv_files: list[Path],
    *,
    plot_files: list[Path] | None = None,
    fluct_table: str = "",
    fluct_table_files: list[Path] | None = None,
) -> str:
    lines = [
        "======== 节点查询汇总 ========",
        f"处理文件数: {n_files}",
        f"命中记录数: {n_hits_raw}",
        f"去重节点数(按坐标): {n_nodes}",
        f"导出 CSV 数: {len(csv_files)}",
    ]
    for p in csv_files:
        lines.append(f"  - {p}")
    if plot_files:
        lines.append(f"折线图数: {len(plot_files)}")
        for p in plot_files:
            lines.append(f"  - {p}")
    if fluct_table_files:
        lines.append("脉动值统计表文件:")
        for p in fluct_table_files:
            lines.append(f"  - {p}")
    if fluct_table:
        lines.append("")
        lines.append(fluct_table)
    return "\n".join(lines)
