@echo off
chcp 65001 >nul
title ailixiao 本地开发环境
color 0A

echo.
echo  ╔══════════════════════════════════════╗
echo  ║    ailixiao.com  本地开发环境        ║
echo  ╚══════════════════════════════════════╝
echo.

:: 切换到项目根目录（bat 文件在 scripts\ 目录下，根目录是上一级）
cd /d "%~dp0.."
set "ROOT=%CD%"

:: ── 1. 检查 Python ────────────────────────────────────────────
echo [检查] Python...
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ❌ 未找到 Python！
    echo  请先下载安装：https://www.python.org/downloads/
    echo  安装时勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  ✅ Python %PYVER%

:: ── 2. 检查 Node.js ───────────────────────────────────────────
echo [检查] Node.js...
where node >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ❌ 未找到 Node.js！
    echo  请先下载安装：https://nodejs.org/
    echo  选择 LTS 版本（长期支持版）
    echo.
    pause
    exit /b 1
)
for /f "tokens=1" %%v in ('node --version 2^>^&1') do set NODEVER=%%v
echo  ✅ Node.js %NODEVER%

:: ── 3. 检查后端 .env 配置文件 ────────────────────────────────
echo [检查] 后端配置...
if not exist "%ROOT%\backend\.env" (
    echo.
    echo  ⚠️  首次运行：正在创建后端配置文件...
    copy "%ROOT%\backend\.env.local.example" "%ROOT%\backend\.env" >nul
    echo.
    echo  ╔══════════════════════════════════════════════════════╗
    echo  ║  ⚠️  请先填写配置文件，再重新运行本脚本              ║
    echo  ║                                                      ║
    echo  ║  配置文件位置：backend\.env                          ║
    echo  ║  用记事本打开，把 FAL_KEY= 后面填入真实值            ║
    echo  ╚══════════════════════════════════════════════════════╝
    echo.
    start notepad "%ROOT%\backend\.env"
    pause
    exit /b 0
)
echo  ✅ 配置文件存在

:: ── 4. 初始化后端虚拟环境 ─────────────────────────────────────
echo [初始化] 后端依赖...
cd "%ROOT%\backend"
if not exist venv (
    echo  首次运行：正在创建 Python 虚拟环境（约 1 分钟）...
    python -m venv venv
    venv\Scripts\pip install -r requirements.txt -q
    echo  ✅ 后端依赖安装完成
) else (
    echo  ✅ 后端虚拟环境已存在
)

:: ── 5. 初始化前端配置和依赖 ──────────────────────────────────
echo [初始化] 前端配置...
cd "%ROOT%\frontend"
if not exist .env.local (
    copy "%ROOT%\frontend\.env.local.example" "%ROOT%\frontend\.env.local" >nul
    echo  ✅ 前端配置已创建
)

if not exist node_modules (
    echo  首次运行：正在安装前端依赖（约 3-5 分钟，请耐心等待）...
    npm install --legacy-peer-deps
    echo  ✅ 前端依赖安装完成
) else (
    echo  ✅ 前端依赖已存在
)

:: ── 6. 启动后端 ───────────────────────────────────────────────
echo.
echo [启动] 后端 API 服务（端口 8001）...
start "后端 API :8001" cmd /k "cd /d "%ROOT%\backend" && venv\Scripts\uvicorn app.main:app --reload --port 8001 --host 127.0.0.1 && pause"

:: 等待后端启动
echo  等待后端就绪...
timeout /t 4 /nobreak >nul

:: ── 7. 启动前端 ───────────────────────────────────────────────
echo [启动] 前端界面（端口 3000）...
start "前端界面 :3000" cmd /k "cd /d "%ROOT%\frontend" && npm run dev -- --port 3000 && pause"

:: 等待前端编译
echo  等待前端编译（约 15 秒）...
timeout /t 15 /nobreak >nul

:: ── 8. 打开浏览器 ─────────────────────────────────────────────
echo [打开] 浏览器...
start http://localhost:3000

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║  ✅ 本地开发环境已启动！                             ║
echo  ║                                                      ║
echo  ║  前端界面：http://localhost:3000                     ║
echo  ║  后端 API：http://localhost:8001                     ║
echo  ║  API 文档：http://localhost:8001/docs                ║
echo  ║                                                      ║
echo  ║  关闭方法：关闭"后端 API"和"前端界面"两个黑窗口     ║
echo  ╚══════════════════════════════════════════════════════╝
echo.
pause
