@echo off
chcp 65001 >nul
title Tan 一键安装
cd /d "%~dp0"

echo ================================================
echo   Tan 记忆插件 - 一键安装
echo ================================================
echo.

rem ---------- 1. 找 Python（优先用内置的）----------
set PY=
if exist "%~dp0pluginendor\python\python.exe" (
    set PY="%~dp0pluginendor\python\python.exe"
    echo [1/3] 用内置 Python
    goto :found
)

for %%P in (python.exe py.exe) do (
    if not defined PY (
        where %%P >nul 2>&1 && set PY=%%P
    )
)

if not defined PY (
    echo [X] 没找到 Python
    echo.
    echo     请先去 https://www.python.org/downloads/ 下载安装
    echo     安装时记得勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

:found
%PY% -c "import sys; print('      版本:', sys.version.split()[0])"

rem ---------- 2. 检查依赖（本项目只用标准库） ----------
echo [1/3] 检查依赖
%PY% -c "import sqlite3, json, http.server, urllib.request; print('      标准库齐全，无需 pip install')"

rem ---------- 3. 启动服务 ----------
echo [2/3] 启动记忆服务
tasklist /fi "imagename eq pythonw.exe" 2>nul | find /i "pythonw" >nul
if %errorlevel%==0 (
    echo      已有 pythonw 进程，跳过
) else (
    start "" pythonw "%~dp0api.py"
    timeout /t 3 >nul
)

%PY% -c "import urllib.request,json; r=json.loads(urllib.request.urlopen('http://127.0.0.1:18765/health',timeout=5).read()); print('      OK' if r.get('ok') else '      FAIL')" 2>nul
if errorlevel 1 (
    echo [X] 服务没起来，可以手动跑：python api.py 看报错
    pause
    exit /b 1
)

rem ---------- 5. 装进 DSH ----------
echo [4/4] 装进 DSH 插件列表
%PY% "%~dp0install.py" --panel

echo.
echo ================================================
echo   装好了
echo ================================================
echo.
echo   下一步：
echo     1. 重启 DSH（托盘图标右键 - 退出 - 重新打开）
echo     2. 左侧边栏会出现「记」Tan
echo.
echo   网页控制台：双击 打开控制台.bat
echo   卸载：python install.py --uninstall
echo.
pause
