@echo off
rem Run outside the DSH job object: Task Scheduler spawns this, so killing DSH
rem will not cascade into us.
cd /d D:\dsh-tan
schtasks /create /tn "dsh-tan-restart" /tr "cmd /c D:\dsh-tan\restart-dsh.bat" /sc once /st 23:59 /f /rl HIGHEST >nul 2>&1
schtasks /run /tn "dsh-tan-restart"
