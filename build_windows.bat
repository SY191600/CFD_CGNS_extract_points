@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM ============================================================
REM  在本机 conda「CGNS」环境中打包为 Windows CLI
REM
REM  【重要】
REM  1. 工程路径不能含中文。请先复制到 D:\cgns_fluent_tool 再打包。
REM  2. 运行用 dist\cgns_tool\cgns_tool.exe，不要跑 build\。
REM  3. 打包完成后请用【未激活 conda】的新 CMD 做自检，否则会掩盖缺 DLL。
REM  4. 目标机请安装 VC++ x64 运行库。
REM ============================================================

set "ENV_NAME=%~1"
if "%ENV_NAME%"=="" set "ENV_NAME=CGNS"

python -c "import os,sys; p=os.getcwd(); sys.exit(0 if p.isascii() else 1)" 2>nul
if errorlevel 1 (
  echo.
  echo [错误] 当前路径含中文或非 ASCII 字符:
  echo   %CD%
  echo.
  echo 请先复制到英文路径，例如 D:\cgns_fluent_tool 后再打包。
  exit /b 1
)

where conda >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 conda，请先打开 Anaconda Prompt。
  exit /b 1
)

echo [1/5] 激活 conda 环境: %ENV_NAME%
call conda activate %ENV_NAME%
if errorlevel 1 (
  echo [错误] 无法激活环境 %ENV_NAME%。
  exit /b 1
)

echo CONDA_PREFIX=%CONDA_PREFIX%
python -c "import sys; print(sys.executable); print(sys.version)"
python -c "import h5py,numpy,matplotlib; print('h5py', h5py.__version__); print('numpy', numpy.__version__)"

echo [2/5] 安装依赖与 PyInstaller...
python -m pip install -U pip
python -m pip install -r requirements.txt
python -m pip install -U "pyinstaller>=6.3"

echo [3/5] 清理旧构建...
if exist build rmdir /s /q build
if exist dist\cgns_tool rmdir /s /q dist\cgns_tool

echo [4/5] 打包...
python -m PyInstaller --noconfirm --clean cgns_tool.spec
if errorlevel 1 (
  echo [错误] 打包失败。
  exit /b 1
)

if not exist dist\cgns_tool\cgns_tool.exe (
  echo [错误] 未找到 dist\cgns_tool\cgns_tool.exe
  exit /b 1
)
if not exist dist\cgns_tool\_internal (
  echo [警告] 未找到 dist\cgns_tool\_internal ，拷贝到其它电脑时极易缺 DLL。
)

copy /Y config.example.json dist\cgns_tool\ >nul
copy /Y PACKAGING.md dist\cgns_tool\ >nul 2>nul
copy /Y diagnose_target.bat dist\cgns_tool\ >nul
(
  echo CGNS Fluent 工具 - 绿色运行包
  echo.
  echo 1. 必须保留整个文件夹（含 _internal），不要只拷 exe。
  echo 2. 路径请使用纯英文，例如 C:\tools\cgns_tool\
  echo 3. 目标电脑请安装 VC++ x64 Redistributable:
  echo    https://aka.ms/vs/17/release/vc_redist.x64.exe
  echo 4. 若报 python313.dll LoadLibrary 失败，先运行 diagnose_target.bat
  echo 5. 从网盘/U盘拷来后，右键文件夹-属性-解除锁定，或运行 diagnose_target.bat
  echo 6. ADF 需要额外安装 cgnsconvert。
) > dist\cgns_tool\使用说明.txt

echo [5/5] 在当前 conda 环境中快速自检...
dist\cgns_tool\cgns_tool.exe -h
if errorlevel 1 (
  echo [错误] 即使在 conda 下也无法启动，请先解决本机问题。
  exit /b 1
)

echo.
echo ========== 打包完成 ==========
echo 输出: %CD%\dist\cgns_tool\
echo.
echo 【关键】请再开一个【普通 CMD】（不要 conda activate），执行:
echo   "%CD%\dist\cgns_tool\cgns_tool.exe" -h
echo 这样能发现“目标机才会出现”的缺 DLL 问题。
echo.
echo 目标机若仍报 DLL，优先安装:
echo   https://aka.ms/vs/17/release/vc_redist.x64.exe
echo 并把完整报错文字发回来。
endlocal
