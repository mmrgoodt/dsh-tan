@echo off
chcp 65001 >nul
title Tan 记忆控制台
cd /d D:\dsh-tan

rem 读 token 并打开带 token 的控制台
set /p TK=<"%USERPROFILE%\.dsh\tan\token.txt"
if "%TK%"=="" (
  echo 找不到 token，先启动记忆服务：python api.py
  pause
  exit /b
)
start "" "http://127.0.0.1:18765/?token=%TK%"
