@echo off
rem dsh-tan: 重启 DSH 的入口。用 bat 调 ps1，绕开 PowerShell 参数传递的坑。
cd /d D:\dsh-tan
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\dsh-tan\restart-dsh.ps1" >> "D:\dsh-tan\restart-bat.log" 2>&1
