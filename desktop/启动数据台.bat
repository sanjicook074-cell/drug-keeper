@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ★ ELECTRON_RUN_AS_NODE 会让 electron.exe 退化成纯 node，启动直接报
rem   "Cannot read properties of undefined (reading 'getPath')"。自己双击时一般没有，
rem   但从某些开发工具里拉起来的窗口会带着这个变量，所以先清掉。
set ELECTRON_RUN_AS_NODE=

if not exist "node_modules\electron\dist\electron.exe" (
  echo.
  echo   还没装依赖。先在这个文件夹里运行一次：
  echo       npm install
  echo.
  pause
  exit /b 1
)

start "" "node_modules\electron\dist\electron.exe" .
exit /b 0
