@echo off
rem Sync plugin/lib to the path DSH actually loads.
rem NOTE: keep this file ASCII-only. cmd.exe reads .cmd as GBK on this machine,
rem and Chinese comments get mangled into bogus commands (earned this the hard way).
setlocal

set SRC=D:\dsh-tan\plugin\lib
set DST=C:\Users\admin\.dsh\profiles\desktop\node_modules\dsh-tan\lib

if not exist "%DST%" (
  echo [ERROR] target not found: %DST%
  echo Is the plugin installed into this profile?
  exit /b 1
)

copy /Y "%SRC%\index.js" "%DST%\index.js" >nul || goto :fail
copy /Y "%SRC%\memory-tools.js" "%DST%\memory-tools.js" >nul || goto :fail
copy /Y "%SRC%\im-tools.js" "%DST%\im-tools.js" >nul || goto :fail

copy /Y "%SRC%\im-native.js" "%DST%\im-native.js" >nul || goto :fail
copy /Y "%SRC%\qr.js" "%DST%\qr.js" >nul || goto :fail
copy /Y "%SRC%\wx-loop.js" "%DST%\wx-loop.js" >nul || goto :fail
copy /Y "%SRC%\..\optional\client.js" "%DST%\client.js" >nul || goto :fail

echo synced: index.js memory-tools.js im-tools.js im-native.js qr.js wx-loop.js client.js
exit /b 0

:fail
echo [ERROR] copy failed
exit /b 1
