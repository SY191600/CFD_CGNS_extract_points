"""Fluent 瞬态 CGNS 读取、查询、版本转换与 JSON 批量处理。"""

from .batch import run_batch
from .config import load_config
from .convert import convert_version
from .format_io import detect_cgns_format
from .hdf5_cgns import CgnsFile, open_cgns
from .info import collect_file_info, format_file_info, format_file_info_json
from .query import export_query_timeseries, query_points

__all__ = [
    "CgnsFile",
    "open_cgns",
    "collect_file_info",
    "format_file_info",
    "format_file_info_json",
    "query_points",
    "export_query_timeseries",
    "convert_version",
    "load_config",
    "run_batch",
    "detect_cgns_format",
]
