"""Command handlers for WeChat bot — all return plain text strings."""
import subprocess
import time


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list, timeout: int = 30) -> str:
    """Run command safely (no shell=True). Return combined stdout+stderr."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return f"[超时 {timeout}s]"
    except Exception as e:
        return f"[执行错误: {e}]"


def _active_slot() -> str:
    """Return 'blue' or 'green' based on nginx proxy_pass port."""
    out = _run(["grep", "-oP", r"proxy_pass http://127.0.0.1:\K[0-9]+",
                "/etc/nginx/sites-enabled/default"])
    port = out.split("\n")[0].strip() if out else "8001"
    return "blue" if port == "8000" else "green"


def _supervisor_status(program: str) -> str:
    return _run(["supervisorctl", "status", program])


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_status() -> str:
    slot = _active_slot()
    lines = [f"🖥️ 服务器状态报告\n激活槽: {slot}\n"]

    sv = _run(["supervisorctl", "status"])
    lines.append("📦 进程:\n" + sv)

    disk = _run(["df", "-h", "/", "--output=source,size,used,avail,pcent"])
    lines.append("\n💾 磁盘:\n" + disk)

    mem = _run(["free", "-h"])
    lines.append("\n🧠 内存:\n" + mem)

    nginx = _run(["systemctl", "is-active", "nginx"])
    lines.append(f"\n🌐 Nginx: {nginx}")

    return "\n".join(lines)


def cmd_logs() -> str:
    slot = _active_slot()
    log = f"/var/log/ssp-backend-{slot}.err.log"
    r = subprocess.run(["tail", "-n", "50", log], capture_output=True, text=True, timeout=10)
    content = r.stdout.strip() or "(无错误日志)"
    # Truncate to 2000 chars for WeChat message limit
    if len(content) > 2000:
        content = "...(已截断)\n" + content[-2000:]
    return f"📋 错误日志 ({slot}, 最近50行):\n\n{content}"


def cmd_restart_backend() -> str:
    slot = _active_slot()
    program = f"ssp-backend-{slot}"
    out = _run(["supervisorctl", "restart", program], timeout=40)
    time.sleep(3)
    status = _supervisor_status(program)
    ok = "RUNNING" in status
    icon = "✅" if ok else "⚠️"
    return f"{icon} 后端重启完成 ({slot})\n{out}\n\n现状: {status}"


def cmd_restart_frontend() -> str:
    slot = _active_slot()
    program = f"ssp-frontend-{slot}"
    out = _run(["supervisorctl", "restart", program], timeout=40)
    time.sleep(3)
    status = _supervisor_status(program)
    ok = "RUNNING" in status
    icon = "✅" if ok else "⚠️"
    return f"{icon} 前端重启完成 ({slot})\n{out}\n\n现状: {status}"


def cmd_auto_fix() -> str:
    slot = _active_slot()
    report = [f"🔍 自动诊断中 (slot: {slot})...\n"]
    actions = []

    # ── backend ──
    backend_prog = f"ssp-backend-{slot}"
    bs = _supervisor_status(backend_prog)
    if "RUNNING" not in bs:
        report.append(f"❌ 后端异常: {bs}")
        out = _run(["supervisorctl", "restart", backend_prog], timeout=40)
        time.sleep(3)
        bs2 = _supervisor_status(backend_prog)
        icon = "✅" if "RUNNING" in bs2 else "🔴"
        actions.append(f"{icon} 重启后端 → {bs2}")
    else:
        report.append("✅ 后端进程正常")

    # ── frontend ──
    frontend_prog = f"ssp-frontend-{slot}"
    fs = _supervisor_status(frontend_prog)
    if "RUNNING" not in fs:
        report.append(f"❌ 前端异常: {fs}")
        out = _run(["supervisorctl", "restart", frontend_prog], timeout=40)
        time.sleep(3)
        fs2 = _supervisor_status(frontend_prog)
        icon = "✅" if "RUNNING" in fs2 else "🔴"
        actions.append(f"{icon} 重启前端 → {fs2}")
    else:
        report.append("✅ 前端进程正常")

    # ── nginx ──
    ng = _run(["systemctl", "is-active", "nginx"])
    if ng.strip() != "active":
        report.append("❌ Nginx 异常，尝试重启")
        _run(["systemctl", "restart", "nginx"], timeout=20)
        actions.append("✅ 已重启 Nginx")
    else:
        report.append("✅ Nginx 正常")

    # ── disk ──
    df_out = _run(["df", "/", "--output=pcent"])
    try:
        pct = int(df_out.split("\n")[-1].strip().rstrip("%"))
        if pct > 90:
            report.append(f"⚠️ 磁盘告警: 已用 {pct}%，请及时清理")
    except Exception:
        pass

    # ── recent errors ──
    log = f"/var/log/ssp-backend-{slot}.err.log"
    lr = subprocess.run(["tail", "-n", "30", log], capture_output=True, text=True, timeout=10)
    errors = [l for l in lr.stdout.split("\n") if any(k in l for k in ("ERROR", "Exception", "Traceback"))]
    if errors:
        report.append(f"\n🔴 近期发现 {len(errors)} 条错误，最新5条:")
        report.extend(errors[-5:])
    else:
        report.append("\n✅ 近期日志无严重错误")

    if actions:
        report.append("\n🔧 已执行操作:")
        report.extend(actions)
    else:
        report.append("\n🟢 所有服务正常，无需干预")

    return "\n".join(report)


def cmd_help() -> str:
    return (
        "🤖 ailixiao 服务器机器人\n\n"
        "可用指令：\n"
        "• 状态 — 服务器和进程状态\n"
        "• 日志 — 最近错误日志\n"
        "• 重启后端 — 重启后端服务\n"
        "• 重启前端 — 重启前端服务\n"
        "• 帮我处理 — 自动诊断并修复\n"
        "• 帮助 — 显示此帮助"
    )


# ── dispatcher ────────────────────────────────────────────────────────────────

COMMANDS = {
    "状态": cmd_status,
    "日志": cmd_logs,
    "重启后端": cmd_restart_backend,
    "重启前端": cmd_restart_frontend,
    "帮我处理": cmd_auto_fix,
    "帮助": cmd_help,
    "help": cmd_help,
}


def handle_command(text: str) -> str:
    text = text.strip()
    handler = COMMANDS.get(text)
    if handler:
        try:
            return handler()
        except Exception as e:
            return f"❌ 执行出错: {e}"
    return f"不认识指令「{text}」\n\n" + cmd_help()
