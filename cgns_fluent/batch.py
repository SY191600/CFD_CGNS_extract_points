"""按 JSON 配置批量处理 CGNS。"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import AppConfig, discover_cgns_files
from .convert import ConvertError, convert_version
from .format_io import FormatError, configure_tools
from .hdf5_cgns import open_cgns
from .info import collect_file_info, format_file_info, format_file_info_json
from .query import (
    TimedHit,
    export_query_timeseries,
    format_query_batch_summary,
    query_points,
    resolve_query_time,
)


@dataclass
class FileResult:
    path: Path
    ok: bool
    messages: list[str] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)
    error: str | None = None
    query_records: list[TimedHit] = field(default_factory=list)


@dataclass
class BatchOutcome:
    results: list[FileResult]
    query_csv_files: list[Path] = field(default_factory=list)
    query_summary: str = ""
    workers: int = 1


def _rel_or_name(src: Path, root: Path | None) -> Path:
    if root is not None:
        try:
            return src.relative_to(root)
        except ValueError:
            pass
    return Path(src.name)


def _output_stem_dir(cfg: AppConfig, src: Path, base_dir: Path | None = None) -> Path:
    """在 base_dir（默认全局 output.dir）下生成与输入相对结构对应的输出目录。"""
    root_out = base_dir if base_dir is not None else cfg.output.dir
    root = cfg.input.folder
    rel = _rel_or_name(src, root if cfg.output.keep_relative_structure else None)
    out_dir = root_out / rel.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _convert_output_dir(cfg: AppConfig, src: Path) -> Path:
    """版本转换专用导出目录：优先 features.convert.output_dir，否则用全局 output.dir。"""
    base = cfg.convert.output_dir if cfg.convert.output_dir is not None else cfg.output.dir
    return _output_stem_dir(cfg, src, base_dir=base)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def resolve_worker_count(requested: int | None, n_jobs: int) -> int:
    """0/None 表示自动：使用逻辑 CPU 数，且不超过任务数。"""
    if n_jobs <= 1:
        return 1
    cpu = os.cpu_count() or 4
    auto = max(1, cpu)
    if requested is None or int(requested) <= 0:
        return min(auto, n_jobs)
    return max(1, min(int(requested), n_jobs, auto * 2))


def _batch_workers(cfg: AppConfig, n_files: int) -> int:
    """查询/转换共用的文件级并行度：取二者较大值。"""
    q = cfg.parallel.query_workers if cfg.query.enabled else None
    c = cfg.parallel.convert_workers if cfg.convert.enabled else None
    requested = cfg.parallel.workers
    if cfg.query.enabled and cfg.convert.enabled:
        parts = [x for x in (q, c) if x is not None and x > 0]
        if parts:
            requested = max(parts)
    elif cfg.query.enabled and q is not None:
        requested = q
    elif cfg.convert.enabled and c is not None:
        requested = c
    return resolve_worker_count(requested, n_files)


def _process_files(cfg: AppConfig, files: list[Path]) -> tuple[list[FileResult], int]:
    workers = _batch_workers(cfg, len(files))
    if workers <= 1 or len(files) <= 1:
        return [process_one(cfg, src) for src in files], 1

    results: list[FileResult | None] = [None] * len(files)
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_one, cfg, src): i for i, src in enumerate(files)}
            for fut in as_completed(futures):
                idx = futures[fut]
                results[idx] = fut.result()
        return [r for r in results if r is not None], workers
    except (OSError, PermissionError, RuntimeError):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_one, cfg, src): i for i, src in enumerate(files)}
            for fut in as_completed(futures):
                idx = futures[fut]
                results[idx] = fut.result()
        return [r for r in results if r is not None], workers


def process_one(cfg: AppConfig, src: Path) -> FileResult:
    result = FileResult(path=src, ok=True)
    out_dir = _output_stem_dir(cfg, src)
    stem = src.stem
    configure_tools(
        cgnsconvert=cfg.tools.cgnsconvert,
        output_format=cfg.tools.output_format,
    )

    try:
        if cfg.info.enabled:
            with open_cgns(str(src)) as cgns:
                finfo = collect_file_info(cgns)
                text = format_file_info(finfo)
                text_json = format_file_info_json(finfo)
            result.messages.append(text)
            if cfg.info.export_file:
                out = out_dir / f"{stem}_info.txt"
                _write_text(out, text)
                result.outputs.append(out)
                out_json = out_dir / f"{stem}_info.json"
                _write_text(out_json, text_json)
                result.outputs.append(out_json)

        if cfg.query.enabled:
            with open_cgns(str(src)) as cgns:
                tval = resolve_query_time(cgns, cfg.query.time)
                hits = query_points(
                    cgns,
                    xyz=(cfg.query.x, cfg.query.y, cfg.query.z),
                    radius=cfg.query.radius,
                    fields=cfg.query.fields or None,
                    zone=cfg.query.zone,
                    solution=cfg.query.solution,
                    time=cfg.query.time,
                    max_hits=cfg.query.max_hits,
                )
            for h in hits:
                result.query_records.append(
                    TimedHit(time=tval, hit=h, source_file=str(src))
                )
            # 终端/报告不写逐文件明细，只记简要条数
            result.messages.append(f"query_hits={len(hits)} time={tval}")

        if cfg.convert.enabled:
            convert_dir = _convert_output_dir(cfg, src)
            out = convert_dir / f"{stem}{cfg.convert.suffix}.cgns"
            logs = convert_version(
                str(src),
                str(out),
                cfg.convert.target_version,
                output_format=cfg.tools.output_format,
                cgnsconvert=cfg.tools.cgnsconvert,
            )
            result.messages.append("\n".join(logs))
            result.outputs.append(out)

    except (ConvertError, FormatError, FileNotFoundError, ValueError, KeyError, OSError) as exc:
        result.ok = False
        result.error = str(exc)
        result.messages.append(f"失败: {exc}")
    return result


def run_batch(cfg: AppConfig) -> BatchOutcome:
    cfg.output.dir.mkdir(parents=True, exist_ok=True)
    files = discover_cgns_files(cfg)
    if not files:
        raise FileNotFoundError("未找到任何 CGNS 文件，请检查 input.file / input.folder / patterns")

    skip_suffix = f"{cfg.convert.suffix}.cgns".lower() if cfg.convert.enabled else None
    convert_roots: list[Path] = []
    if cfg.convert.enabled:
        convert_roots.append(
            (cfg.convert.output_dir if cfg.convert.output_dir is not None else cfg.output.dir).resolve()
        )
    filtered = []
    for p in files:
        if skip_suffix and p.name.lower().endswith(skip_suffix):
            skip = False
            for root in convert_roots:
                try:
                    p.resolve().relative_to(root)
                    skip = True
                    break
                except ValueError:
                    continue
            if skip:
                continue
        try:
            p.relative_to(cfg.output.dir)
            continue
        except ValueError:
            filtered.append(p)

    results, workers = _process_files(cfg, filtered)

    csv_files: list[Path] = []
    query_summary = ""
    if cfg.query.enabled:
        all_records: list[TimedHit] = []
        for r in results:
            all_records.extend(r.query_records)
        export = None
        if cfg.query.export_file:
            csv_dir = cfg.output.dir / "query_csv"
            plot_workers = resolve_worker_count(
                cfg.parallel.plot_workers
                if cfg.parallel.plot_workers is not None
                else cfg.parallel.workers,
                max(1, len(all_records)),
            )
            export = export_query_timeseries(
                all_records,
                field_names=list(cfg.query.fields),
                out_dir=csv_dir,
                coord_decimals=cfg.query.coord_decimals,
                make_plots=True,
                write_fluct_table=True,
                plot_workers=plot_workers,
            )
            csv_files = export.csv_files

        n_nodes = len(
            {
                (
                    round(rec.hit.xyz[0], cfg.query.coord_decimals),
                    round(rec.hit.xyz[1], cfg.query.coord_decimals),
                    round(rec.hit.xyz[2], cfg.query.coord_decimals),
                )
                for rec in all_records
            }
        )
        fluct_files = []
        if export is not None:
            if export.fluct_table_txt:
                fluct_files.append(export.fluct_table_txt)
            if export.fluct_table_csv:
                fluct_files.append(export.fluct_table_csv)
        query_summary = format_query_batch_summary(
            n_files=len(results),
            n_hits_raw=len(all_records),
            n_nodes=n_nodes,
            csv_files=csv_files,
            plot_files=export.plot_files if export else [],
            fluct_table=export.fluct_table if export else "",
            fluct_table_files=fluct_files,
        )

    return BatchOutcome(
        results=results,
        query_csv_files=csv_files,
        query_summary=query_summary,
        workers=workers,
    )


def format_report(cfg: AppConfig, outcome: BatchOutcome) -> str:
    results = outcome.results
    lines = [
        f"配置文件: {cfg.source}",
        f"开始时间: {datetime.now().isoformat(timespec='seconds')}",
        f"输入文件数: {len(results)}",
        f"并行度: {outcome.workers}",
        f"开启功能: "
        + ", ".join(
            name
            for name, on in (
                ("info", cfg.info.enabled),
                ("query", cfg.query.enabled),
                ("convert", cfg.convert.enabled),
            )
            if on
        ),
        "",
    ]
    ok_n = sum(1 for r in results if r.ok)
    lines.append(f"成功: {ok_n}  失败: {len(results) - ok_n}")
    lines.append("")

    if cfg.query.enabled and outcome.query_summary:
        lines.append(outcome.query_summary)
        lines.append("")

    for r in results:
        status = "OK" if r.ok else "FAIL"
        lines.append(f"[{status}] {r.path}")
        if r.error:
            lines.append(f"  错误: {r.error}")
        # 仅列出该文件专属输出（info/convert），不重复刷屏查询明细
        for out in r.outputs:
            lines.append(f"  输出: {out}")
        if cfg.query.enabled:
            qmsg = [m for m in r.messages if m.startswith("query_hits=")]
            if qmsg:
                lines.append(f"  {qmsg[0]}")
        if cfg.info.enabled:
            info_msgs = [m for m in r.messages if m.startswith("======== CGNS 文件数据结构")]
            for msg in info_msgs:
                lines.append("")
                lines.append(msg)
        lines.append("")
        lines.append("-" * 72)
        lines.append("")
    return "\n".join(lines)


def format_console_summary(cfg: AppConfig, outcome: BatchOutcome) -> str:
    """终端只打印总结，不逐文件刷查询表。"""
    results = outcome.results
    ok_n = sum(1 for r in results if r.ok)
    lines = [
        f"批处理完成: 成功 {ok_n}/{len(results)}",
        f"并行工作进程/线程: {outcome.workers}",
    ]
    if cfg.query.enabled:
        lines.append(outcome.query_summary or "查询: 无命中")
    fails = [r for r in results if not r.ok]
    if fails:
        lines.append("失败文件:")
        for r in fails:
            lines.append(f"  - {r.path}: {r.error}")
    if cfg.info.enabled:
        n_info = sum(1 for r in results if any(str(o).endswith("_info.txt") for o in r.outputs))
        lines.append(f"info 已导出: {n_info} 个文件的 txt/json（详见输出目录与报告）")
    if cfg.convert.enabled:
        n_cvt = sum(1 for r in results for o in r.outputs if str(o).endswith(".cgns"))
        dest = cfg.convert.output_dir if cfg.convert.output_dir is not None else cfg.output.dir
        lines.append(f"convert 已导出: {n_cvt} 个 CGNS -> {dest}")
    return "\n".join(lines)


def write_report(cfg: AppConfig, outcome: BatchOutcome) -> Path:
    text = format_report(cfg, outcome)
    report_path = Path(cfg.output.report_file)
    if not report_path.is_absolute():
        report_path = cfg.output.dir / report_path
    _write_text(report_path, text)
    return report_path
