@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo ===== cgns_tool 目标机诊断 =====
echo 当前目录: %CD%
echo.

if not exist "%CD%\cgns_tool.exe" (
  echo [失败] 当前目录没有 cgns_tool.exe
  echo 请进入 dist\cgns_tool 这一层再运行本脚本。
  pause
  exit /b 1
)

if not exist "%CD%\_internal" (
  echo [失败] 缺少 _internal 文件夹。拷贝不完整，请重新拷贝整个目录。
  pause
  exit /b 1
)

echo [1] 检查 python3*.dll ...
dir /b "%CD%\_internal\python*.dll" 2>nul
if errorlevel 1 (
  echo [失败] _internal 下没有 python3xx.dll，包不完整，请重新打包。
  pause
  exit /b 1
)

echo.
echo [2] 解除“从其它电脑复制”的锁定标记...
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%CD%' -Recurse -ErrorAction SilentlyContinue | Unblock-File"
echo 完成。

echo.
echo [3] 若仍失败，请安装 VC++ x64 运行库:
echo     https://aka.ms/vs/17/release/vc_redist.x64.exe
echo.

echo [4] 尝试启动...
"%CD%\cgns_tool.exe" -h
set ERR=%ERRORLEVEL%
echo.
if not "%ERR%"=="0" (
  echo [失败] 退出码=%ERR%
  echo 常见原因:
  echo   1. 未安装 VC++ x64 Redistributable
  echo   2. _internal\python313.dll 存在但其依赖 DLL 缺失
  echo   3. 杀毒软件隔离了部分 DLL
  echo 请把上面完整英文/中文报错发回。
) else (
  echo [成功] 已能启动。
)
echo.
pause
endlocal
