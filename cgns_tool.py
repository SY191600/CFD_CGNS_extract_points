#!/usr/bin/env python3
"""Fluent 瞬态 CGNS 工具：JSON 配置驱动 / 批量处理 / 信息、查询、版本转换。"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# PyInstaller 冻结后，资源与可执行文件同目录更利于找配置示例
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cgns_fluent import convert_version, open_cgns
from cgns_fluent.batch import format_console_summary, run_batch, write_report
from cgns_fluent.config import load_config
from cgns_fluent.convert import ConvertError
from cgns_fluent.info import collect_file_info, format_file_info
from cgns_fluent.query import (
    TimedHit,
    export_query_timeseries,
    format_hits_summary,
    query_points,
    resolve_query_time,
)


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    outcome = run_batch(cfg)
    report_path = write_report(cfg, outcome)
    # 终端只打总结，详细内容在报告/CSV/info 文件中
    print(format_console_summary(cfg, outcome))
    print(f"报告已写入: {report_path}")
    return 0 if all(r.ok for r in outcome.results) else 1


def cmd_info(args: argparse.Namespace) -> int:
    with open_cgns(args.file) as cgns:
        print(format_file_info(collect_file_info(cgns)))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    fields = args.fields
    if fields:
        fields = [x.strip() for x in fields.split(",") if x.strip()]
    zone: str | int | None = args.zone
    if zone is not None and str(zone).isdigit():
        zone = int(zone)
    out_dir = Path(args.csv_dir) if args.csv_dir else Path(".")
    with open_cgns(args.file) as cgns:
        tval = resolve_query_time(cgns, args.time)
        hits = query_points(
            cgns,
            xyz=(args.x, args.y, args.z),
            radius=args.radius,
            fields=fields or None,
            zone=zone,
            solution=args.solution,
            time=args.time,
            max_hits=args.max_hits,
        )
        records = [TimedHit(time=tval, hit=h, source_file=args.file) for h in hits]
        export = export_query_timeseries(
            records,
            field_names=fields or [],
            out_dir=out_dir / "query_csv",
            coord_decimals=args.coord_decimals,
            make_plots=True,
            write_fluct_table=True,
        )
    print(format_hits_summary(hits, (args.x, args.y, args.z), args.radius, export.csv_files))
    if export.fluct_table:
        print(export.fluct_table)
    if export.plot_files:
        print(f"折线图: {len(export.plot_files)} 个（与 CSV 同目录）")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    logs = convert_version(
        args.file,
        args.output,
        float(args.to),
        output_format=args.format,
    )
    print("\n".join(logs))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cgns_tool",
        description="Fluent 瞬态 CGNS 工具。推荐使用 JSON 配置批量运行：cgns_tool run --config config.json",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="按 JSON 配置执行（支持功能开关与文件夹批量）")
    p_run.add_argument(
        "-c",
        "--config",
        required=True,
        help="JSON 配置文件路径，参见 config.example.json",
    )
    p_run.set_defaults(func=cmd_run)

    p_info = sub.add_parser("info", help="（可选）单文件：打印 CGNS 基本信息")
    p_info.add_argument("file", help="CGNS 文件路径")
    p_info.set_defaults(func=cmd_info)

    p_q = sub.add_parser("query", help="（可选）单文件：按坐标查询并导出 CSV")
    p_q.add_argument("file", help="CGNS 文件路径")
    p_q.add_argument("--x", type=float, required=True, help="查询点 X")
    p_q.add_argument("--y", type=float, required=True, help="查询点 Y")
    p_q.add_argument("--z", type=float, default=0.0, help="查询点 Z，默认 0")
    p_q.add_argument("--radius", type=float, required=True, help="搜索半径")
    p_q.add_argument(
        "--fields",
        default="",
        help="物理场名，逗号分隔；缺省则打印该解的全部场",
    )
    p_q.add_argument("--zone", default=None, help="区域名称或 ID（从 1 开始）")
    p_q.add_argument("--solution", default=None, help="FlowSolution 节点名")
    p_q.add_argument("--time", type=float, default=None, help="瞬态时刻")
    p_q.add_argument("--max-hits", type=int, default=50, help="最多命中点数")
    p_q.add_argument("--csv-dir", default=".", help="CSV 输出目录，默认当前目录")
    p_q.add_argument("--coord-decimals", type=int, default=8, help="坐标去重小数位")
    p_q.set_defaults(func=cmd_query)

    p_c = sub.add_parser("convert", help="（可选）单文件：3.4 与 4.1 互转")
    p_c.add_argument("file", help="输入 CGNS（HDF5 或 ADF）")
    p_c.add_argument("-o", "--output", required=True, help="输出 CGNS 路径")
    p_c.add_argument("--to", required=True, choices=["3.4", "4.1"], help="目标版本")
    p_c.add_argument(
        "--format",
        default="auto",
        choices=["auto", "hdf5", "adf"],
        help="输出存储格式，默认 auto（与输入相同）",
    )
    p_c.set_defaults(func=cmd_convert)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConvertError as exc:
        print(f"转换失败: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError, KeyError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
