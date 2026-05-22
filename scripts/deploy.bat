@echo off
chcp 65001 >nul
title 部署到线上 ailixiao.com
color 0E

echo.
echo  ╔══════════════════════════════════════╗
echo  ║    部署到线上  ailixiao.com          ║
echo  ╚══════════════════════════════════════╝
echo.

cd /d "%~dp0.."

:: ── 检查 git ──────────────────────────────────────────────────
where git >nul 2>&1
if errorlevel 1 (
    echo  ❌ 未找到 Git！请先安装：https://git-scm.com/download/win
    pause
    exit /b 1
)

:: ── 显示改动内容 ──────────────────────────────────────────────
echo  本次改动的文件：
git status --short
echo.

:: ── 询问提交说明 ──────────────────────────────────────────────
set /p COMMIT_MSG=请输入本次改动说明（例如：修复登录bug、新增生图功能）:

if "%COMMIT_MSG%"=="" (
    echo  ❌ 说明不能为空，请重新运行
    pause
    exit /b 1
)

:: ── 二次确认 ──────────────────────────────────────────────────
echo.
echo  即将部署到线上，请确认：
echo  改动说明：%COMMIT_MSG%
echo.
set /p CONFIRM=输入 y 确认部署，其他键取消:
if /i not "%CONFIRM%"=="y" (
    echo  已取消
    pause
    exit /b 0
)

:: ── 提交并推送代码 ────────────────────────────────────────────
echo.
echo [1/3] 提交代码...
git add -A
git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
    echo  没有改动需要提交，直接部署线上已有代码...
)

echo [2/3] 推送到 GitHub...
git push origin main
if errorlevel 1 (
    echo  ❌ 推送失败，请检查网络或 Git 权限
    pause
    exit /b 1
)
echo  ✅ 代码已推送

:: ── SSH 触发线上部署 ──────────────────────────────────────────
echo [3/3] 触发线上部署...
echo  （需要输入服务器密码，或已配置 SSH Key 则自动完成）
echo.
ssh root@43.134.71.189 "bash /root/ssp/deploy/deploy.sh"

if errorlevel 1 (
    echo.
    echo  ⚠️  SSH 连接失败。
    echo  代码已推送到 GitHub，可在 Claude Code 里输入"帮我部署"完成部署。
) else (
    echo.
    echo  ╔══════════════════════════════════════════════════════╗
    echo  ║  ✅ 部署成功！                                       ║
    echo  ║  请打开 https://ailixiao.com 验证                   ║
    echo  ╚══════════════════════════════════════════════════════╝
)
echo.
pause
