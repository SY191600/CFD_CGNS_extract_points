from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import h5py
import numpy as np

from cgns_fluent.convert import convert_version, read_section_connectivity
from cgns_fluent.format_io import detect_cgns_format
from cgns_fluent.hdf5_cgns import open_cgns
from cgns_fluent.info import collect_file_info, format_file_info
from cgns_fluent.query import query_points


def _node(parent, name: str, label: str, data=None):
    g = parent.create_group(name)
    g.attrs["name"] = name
    g.attrs["label"] = label
    if data is not None:
        arr = np.array(data)
        if isinstance(data, str):
            arr = np.array(data, dtype="S")
        g.create_dataset(" data", data=arr)
    return g


def write_sample_cgns(path: Path, version: float = 3.4, times: list[float] | None = None) -> None:
    """一个四面体：4 节点、4 个三角形面、1 个体单元，带壁面 BC 与 Pressure。"""
    if times is None:
        times = [0.0, 0.1]
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    # 3.x NGON: count + nodes (1-based)
    ngon_v3 = np.array(
        [3, 1, 2, 3, 3, 1, 2, 4, 3, 2, 3, 4, 3, 1, 3, 4],
        dtype=np.int32,
    )
    nface_v3 = np.array([4, 1, 2, 3, 4], dtype=np.int32)
    pressure = np.array([10.0, 20.0, 30.0, 40.0], dtype=np.float64)

    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        _node(f, "CGNSLibraryVersion", "CGNSLibraryVersion_t", np.float32([version]))
        base = _node(f, "Base", "CGNSBase_t", np.int32([3, 3]))
        zone = _node(base, "Fluid", "Zone_t", np.int32([[4, 1, 4], [0, 0, 0], [0, 0, 0]]))
        _node(zone, "ZoneType", "ZoneType_t", "Unstructured")
        gc = _node(zone, "GridCoordinates", "GridCoordinates_t")
        _node(gc, "CoordinateX", "DataArray_t", xyz[:, 0])
        _node(gc, "CoordinateY", "DataArray_t", xyz[:, 1])
        _node(gc, "CoordinateZ", "DataArray_t", xyz[:, 2])

        ngon = _node(zone, "Ngons", "Elements_t", np.int32([23, 0]))
        _node(ngon, "ElementRange", "IndexRange_t", np.int32([[1], [4]]))
        _node(ngon, "ElementConnectivity", "DataArray_t", ngon_v3)

        nface = _node(zone, "Nface", "Elements_t", np.int32([24, 0]))
        _node(nface, "ElementRange", "IndexRange_t", np.int32([[1], [1]]))
        _node(nface, "ElementConnectivity", "DataArray_t", nface_v3)

        zbc = _node(zone, "ZoneBC", "ZoneBC_t")
        wall = _node(zbc, "wall", "BC_t")
        _node(wall, "BCType", "BCTypeSimple_t", "BCWall")

        bid = _node(base, "TimeIterValues", "BaseIterativeData_t")
        _node(bid, "TimeValues", "DataArray_t", np.float64(times))

        sol = _node(zone, "FlowSolution", "FlowSolution_t")
        _node(sol, "GridLocation", "GridLocation_t", "Vertex")
        _node(sol, "Pressure", "DataArray_t", pressure)
        _node(sol, "VelocityX", "DataArray_t", np.array([1.0, 2.0, 3.0, 4.0]))


def test_info_query_convert(tmp_path: Path) -> None:
    src = tmp_path / "sample_v34.cgns"
    write_sample_cgns(src, 3.4)

    with open_cgns(str(src)) as cgns:
        info = collect_file_info(cgns)
        assert abs(info.cgns_version - 3.4) < 1e-6
        assert info.zones[0].name == "Fluid"
        assert info.zones[0].zone_id == 1
        assert any("壁面" in info.zones[0].role or "流体" in info.zones[0].role for _ in [0])
        names = {f.name for f in info.fields}
        assert "Pressure" in names
        hits = query_points(cgns, xyz=(0.0, 0.0, 0.0), radius=0.05, fields=["Pressure"])
        assert len(hits) == 1
        assert hits[0].local_index == 0
        assert abs(hits[0].values["Pressure"] - 10.0) < 1e-12
        text = format_file_info(info)
        assert "物理场列表" in text
        assert "节点(Vertex)" in text
        assert "区域列表" in text
        assert "单元数" in text

    v41 = tmp_path / "sample_v41.cgns"
    logs = convert_version(str(src), str(v41), 4.1)
    assert any("3.x 交错 -> 4.1" in x for x in logs)
    with open_cgns(str(v41)) as cgns:
        assert abs(cgns.cgns_version() - 4.1) < 1e-6
        zone = cgns.zones()[0][2]
        ngon = [s for s in cgns.element_sections(zone) if cgns.element_type_name(s) == "NGON_n"][0]
        conn, off, _ = read_section_connectivity(ngon)
        assert off is not None
        assert conn.tolist() == [1, 2, 3, 1, 2, 4, 2, 3, 4, 1, 3, 4]
        assert off.tolist() == [0, 3, 6, 9, 12]

    v34 = tmp_path / "sample_back_v34.cgns"
    convert_version(str(v41), str(v34), 3.4)
    with open_cgns(str(v34)) as cgns:
        assert abs(cgns.cgns_version() - 3.4) < 1e-6
        zone = cgns.zones()[0][2]
        ngon = [s for s in cgns.element_sections(zone) if cgns.element_type_name(s) == "NGON_n"][0]
        conn, off, _ = read_section_connectivity(ngon)
        assert off is None
        assert conn.tolist()[:4] == [3, 1, 2, 3]


def test_json_batch(tmp_path: Path) -> None:
    import json

    from cgns_fluent.batch import run_batch, write_report
    from cgns_fluent.config import load_config

    folder = tmp_path / "in"
    folder.mkdir(parents=True, exist_ok=True)
    write_sample_cgns(folder / "a.cgns", 4.1, times=[0.0])
    write_sample_cgns(folder / "b.cgns", 4.1, times=[0.1])

    out = tmp_path / "out"
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "input": {"folder": str(folder), "recursive": True},
                "output": {"dir": str(out), "report_file": "report.txt"},
                "parallel": {"workers": 2, "plot_workers": 2},
                "features": {
                    "info": {"enabled": True, "export_file": True},
                    "query": {
                        "enabled": True,
                        "x": 0.0,
                        "y": 0.0,
                        "z": 0.0,
                        "radius": 0.1,
                        "fields": ["Pressure", "VelocityX"],
                        "export_file": True,
                    },
                    "convert": {
                        "enabled": True,
                        "target_version": 3.4,
                        "suffix": "_v34",
                        "output_dir": str(out / "converted"),
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    outcome = run_batch(cfg)
    report = write_report(cfg, outcome)
    assert len(outcome.results) == 2
    assert all(r.ok for r in outcome.results)
    assert outcome.workers == 2
    assert report.is_file()
    assert (out / "a_info.txt").is_file()
    assert (out / "a_info.json").is_file()
    assert not (out / "a_query.txt").exists()
    assert (out / "converted" / "a_v34.cgns").is_file()
    assert (out / "converted" / "b_v34.cgns").is_file()
    assert outcome.query_csv_files
    csv_path = outcome.query_csv_files[0]
    assert csv_path.is_file()
    text = csv_path.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("time,Pressure,VelocityX")
    assert "0" in text
    assert "0.1" in text
    png_path = csv_path.with_suffix(".png")
    assert png_path.is_file()
    fluct_txt = out / "query_csv" / "query_fluct_summary.txt"
    fluct_csv = out / "query_csv" / "query_fluct_summary.csv"
    assert fluct_txt.is_file()
    assert fluct_csv.is_file()
    assert "脉动值" in fluct_txt.read_text(encoding="utf-8")
    assert "Pressure_fluct" in outcome.query_summary
    info_txt = (out / "a_info.txt").read_text(encoding="utf-8")
    assert "物理场列表" in info_txt
    assert "区域列表" in info_txt


def test_detect_hdf5_format(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "f.cgns"
    write_sample_cgns(src, 3.4)
    assert detect_cgns_format(src) == "hdf5"


def test_detect_adf_header(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    adf = tmp_path / "fake.adf"
    # 模拟 ADF 文本头（真实 ADF 结构更复杂，此处仅测识别）
    header = b"ADF Database Version 00.04" + b"\x00" * 200
    adf.write_bytes(header)
    assert detect_cgns_format(adf) == "adf"


def test_convert_preserves_hdf5_without_cgnsconvert(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "in.cgns"
    dst = tmp_path / "out.cgns"
    write_sample_cgns(src, 4.1)
    logs = convert_version(str(src), str(dst), 3.4, output_format="hdf5")
    assert any("HDF5" in x for x in logs)
    assert detect_cgns_format(dst) == "hdf5"
    with open_cgns(str(dst)) as cgns:
        assert abs(cgns.cgns_version() - 3.4) < 1e-6


if __name__ == "__main__":
    import multiprocessing
    import tempfile

    multiprocessing.freeze_support()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        test_info_query_convert(p)
        test_json_batch(p / "batch")
        test_detect_hdf5_format(p / "fmt")
        test_detect_adf_header(p / "adf")
        test_convert_preserves_hdf5_without_cgnsconvert(p / "cvt")
    print("ok")

