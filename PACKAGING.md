# 打包与跨电脑 DLL 问题

## 你这种报错（最常见）

```text
Failed to load Python DLL '...\cgns_tool\_internal\python313.dll'
LoadLibrary: ...
```

路径已是英文时，通常不是路径问题，而是下面之一：

1. **目标机缺 VC++ x64 运行库**（python313.dll 的依赖加载失败，表面却报 python313.dll）  
   安装：https://aka.ms/vs/17/release/vc_redist.x64.exe
2. **从网盘/U盘拷贝后文件被锁定**  
   在 `cgns_tool` 目录运行随包的 `diagnose_target.bat`，或 PowerShell：  
   `Get-ChildItem -Recurse | Unblock-File`
3. **`_internal\python313.dll` 根本不存在 / 拷贝不完整**  
   在目标机检查该文件是否存在；不存在就重新拷整个目录。
4. **打包时没把 python DLL 打全**  
   请用最新 `build_windows.bat` 在英文路径下重打包后再拷贝。

### 目标机立刻做

```bat
cd /d D:\NVH\sy\04_Tools\cgns_tool
dir _internal\python*.dll
diagnose_target.bat
```

若没有 `diagnose_target.bat`，先装 VC++，再执行：

```powershell
cd D:\NVH\sy\04_Tools\cgns_tool
Get-ChildItem -Recurse | Unblock-File
.\cgns_tool.exe -h
```

必须拷贝整个 `dist\cgns_tool\`，至少包含：

```text
cgns_tool.exe
_internal\          ← 不能缺
使用说明.txt
```

只拷贝一个 `exe` 一定会缺 DLL。

### 2. 目标机安装 VC++ 运行库（最常见）

下载安装（x64）：

https://aka.ms/vs/17/release/vc_redist.x64.exe

装完再运行 `cgns_tool.exe -h`。

### 3. 路径不要有中文

不要放在例如 `D:\资料\...`、`桌面\中文目录\`。  
建议：`C:\tools\cgns_tool\`

### 4. 本机验证时不要开着 conda

在打包机上：

1. 新开 **普通 CMD**（不要 `conda activate`）
2. 运行：

```bat
C:\path\to\dist\cgns_tool\cgns_tool.exe -h
```

若这里就缺 DLL，说明打包没收全，需要重新打包（不要在已激活的 CGNS 环境里测，会“看起来正常”）。

### 5. 重新打包（已加强收集 conda DLL）

```bat
xcopy /E /I "D:\08_AI问答\cgns_fluent_tool" "D:\cgns_fluent_tool"
cd /d D:\cgns_fluent_tool
conda activate CGNS
build_windows.bat
```

新版本会额外收集 `Library\bin` 里的 `hdf5/zlib/openblas/...` DLL，并在运行时把 `_internal` 加入 DLL 搜索路径。

---

## 正确打包步骤

```bat
cd /d D:\cgns_fluent_tool
conda activate CGNS
build_windows.bat
```

输出：`dist\cgns_tool\`

## 正确运行

```bat
cd /d D:\cgns_fluent_tool\dist\cgns_tool
cgns_tool.exe -h
cgns_tool.exe run --config config.json
```

不要运行 `build\` 里的文件。

## 仍失败时请提供

把目标机完整报错贴出来，尤其是缺的是哪一个 DLL 文件名，例如：

- `VCRUNTIME140.dll` / `MSVCP140.dll` → 装 VC++ 运行库  
- `hdf5.dll` / `zlib.dll` → 重新用新脚本打包  
- `python313.dll` → 路径中文或拷贝不完整  
