@echo off
rem 改完 plugin/lib 后跑一次，把文件同步到 DSH 真正加载的位置。
rem 之前没这一步，两天的插件修改全部无效。
set SRC=D:\dsh-tan\plugin\lib
set DST=C:\Users\admin\.dsh\profiles\desktop\node_modules\dsh-tan\lib
copy /Y "%SRC%\index.js" "%DST%\index.js" >nul
copy /Y "%SRC%\memory-tools.js" "%DST%\memory-tools.js" >nul
echo synced: index.js memory-tools.js
