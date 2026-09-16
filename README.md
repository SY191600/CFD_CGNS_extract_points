# Fluent 瞬态 CGNS 工具

读取 Fluent 导出的 CGNS（**自动识别 HDF5 / ADF**）。推荐用 JSON 配置控制功能开关，并支持按文件夹批量处理。

## 安装

```bash
pip install -r requirements.txt
```

处理 **ADF** 还需安装 [CGNS Tools](https://cgns.org/) 中的 `cgnsconvert`，并加入 PATH；或在 JSON / 环境变量中指定路径。

### 打包成免 Python 的 Windows CLI

若要在**另一台电脑不装 conda/Python** 直接运行，请看 [PACKAGING.md](PACKAGING.md)。  
本机在 conda `CGNS` 环境中执行：

```bat
conda activate CGNS
build_windows.bat
```

然后将 `dist\cgns_tool\` 整个目录拷贝到目标机即可。

## 推荐用法（JSON）

```bash
python cgns_tool.py run --config config.json
```

参考 [`config.example.json`](config.example.json)。

### 格式相关配置

```json
"cgns_tools": {
  "cgnsconvert": "cgnsconvert",
  "output_format": "auto"
}
```

| 字段 | 含义 |
|------|------|
| `cgns_tools.cgnsconvert` | `cgnsconvert` 可执行文件路径 |
| `cgns_tools.output_format` | `auto` 保持与输入相同；或强制 `hdf5` / `adf` |
| `input.patterns` | 可含 `*.cgns` / `*.hdf` / `*.hdf5` / `*.adf` |

程序会自动检测文件是 HDF5 还是 ADF：

- **读取**（info / query）：ADF → 临时转为 HDF5 再解析
- **修改**（convert）：在 HDF5 工作副本上改版本/NGON，再按 `output_format` 写出

也可设置环境变量 `CGNSCONVERT` 指向工具路径。

### 并行（查询 / 版本转换）

批量处理多个 CGNS 时，查询与版本转换默认按 **CPU 逻辑核心数** 开进程并行。JSON：

```json
"parallel": {
  "workers": 0,
  "query_workers": null,
  "convert_workers": null,
  "plot_workers": null
}
```

| 字段 | 含义 |
|------|------|
| `workers` | 文件级并行数。`0` = 自动（逻辑 CPU 数） |
| `query_workers` | 仅查询时覆盖 `workers` |
| `convert_workers` | 仅转换时覆盖 `workers` |
| `plot_workers` | 折线图并行数，`0`/`null` = 自动 |

进程池不可用时（如部分打包环境）会自动退回线程池。

### info 输出内容

开启 `features.info` 后，每个文件会导出：

- `{name}_info.txt`：区域 **ID / 名称 / 网格类型 / 角色**、**节点数与单元数**；物理场 **名称**、所属区域、**节点或单元中心**、**数量**
- `{name}_info.json`：同上结构化数据
- `batch_report.txt`：详细报告（含 info）；**终端只打印批处理总结**

### query 输出内容

开启 `features.query` 后：

- 终端只打印**查询汇总**（文件数、命中数、CSV 路径），不逐文件刷表
- 按**相同节点坐标**合并导出 CSV 到 `output.dir/query_csv/`
- CSV 列格式：`time,<json中的物理场1>,<物理场2>,...`
- 终端以 **表格** 显示每个坐标各物理场的 **脉动值**
- 每个坐标的时序折线图（`.png`）与 CSV 保存在同一目录
- 另存 `query_fluct_summary.txt` / `query_fluct_summary.csv`
- 脉动值公式：$\phi'=\sqrt{\frac{1}{N}\sum_{i=1}^{N}(\phi_i-\bar\phi)^2}$，其中 $\bar\phi=\frac{1}{N}\sum\phi_i$
- 时间优先取文件内 `TimeValues`（`query.time` 为空时）；可用 `coord_decimals` 控制坐标去重精度

### convert 输出位置

```json
"convert": {
  "enabled": true,
  "target_version": 3.4,
  "suffix": "_v34",
  "output_dir": "./convert_output"
}
```

- `output_dir`：版本转换结果的保存目录（可绝对路径或相对配置文件目录）
- 若为 `null` / 省略，则回退到全局 `output.dir`
- 文件名：`{原名}{suffix}.cgns`，并受 `output.keep_relative_structure` 影响

## 单文件命令

```bash
python cgns_tool.py info case.cgns
python cgns_tool.py query case.cgns --x 0.1 --y 0.2 --z 0 --radius 0.01 --fields Pressure
python cgns_tool.py convert case.cgns -o out.cgns --to 3.4 --format auto
```

## 说明

- Fluent 2024 多为 CGNS **4.3 / HDF5**；ADF 常见于旧流程或其它求解器导出。
- 版本转换目标支持 **3.4 / 4.1**。
- 输出目录内文件默认不会被再次扫描为输入。
