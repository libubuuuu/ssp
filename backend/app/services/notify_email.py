"""口播带货任务终态邮件通知 — 用户起任务后离开页面也能收到完成/失败通知。

用 Resend(env: RESEND_API_KEY / FROM_EMAIL),无 key 时 print warning 跳过(开发模式)。
所有发送 fire-and-forget,不阻塞主流程。
"""
import os


SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://ailixiao.com")


def _send_resend(to: str, subject: str, html: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
    if not api_key:
        print(f"[WARN] notify_email: 未配置 RESEND_API_KEY,跳过 → {to} / {subject}")
        return True
    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            "from": f"AI Lixiao <{from_email}>",
            "to": [to],
            "subject": subject,
            "html": html,
        })
        print(f"[OK] notify_email 已发送: {to} / {subject}")
        return True
    except Exception as e:
        print(f"[ERR] notify_email 发送失败 {to} / {subject}: {e}")
        return False


_TIER_LABEL = {"economy": "经济档", "standard": "标准档", "premium": "顶级档"}


def send_oral_completion(email: str, sid: str, tier: str, duration_seconds: float, final_url: str = "") -> bool:
    """口播带货任务完成。"""
    workbench_url = f"{SITE_BASE_URL}/video/oral-broadcast/{sid}"
    tier_zh = _TIER_LABEL.get(tier or "", tier or "")
    duration_str = f"{duration_seconds:.0f} 秒" if duration_seconds else "—"
    html = f"""
<div style="font-family:sans-serif;max-width:520px;margin:40px auto;padding:36px;background:#fff;border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,0.08);">
  <h2 style="color:#0d0d0d;margin:0 0 20px 0;font-weight:500;">口播带货任务已完成 ✅</h2>
  <p style="color:#666;line-height:1.7;">你的 AI 口播带货视频已生成完毕,可以查看 / 下载了。</p>
  <div style="background:#f5f3ed;border-radius:12px;padding:16px 20px;margin:20px 0;color:#333;font-size:0.95rem;line-height:1.8;">
    <div>任务 ID:<span style="font-family:monospace;color:#666;">{sid}</span></div>
    <div>档位:{tier_zh}</div>
    <div>时长:{duration_str}</div>
  </div>
  <a href="{workbench_url}" style="display:inline-block;padding:12px 28px;background:#0d0d0d;color:#fff;border-radius:10px;text-decoration:none;font-size:0.95rem;">打开工作台查看</a>
  <p style="color:#aaa;font-size:0.8rem;margin-top:30px;">若链接打不开,直接复制粘贴:<br>{workbench_url}</p>
</div>
"""
    return _send_resend(email, "【AI Lixiao】口播带货任务已完成", html)


_STEP_ZH = {
    "step1": "Step 1 提取/转写音频",
    "step2": "Step 2 文案编辑",
    "step3": "Step 3 语音合成",
    "step4": "Step 4 换装(wan-vace)",
    "step5": "Step 5 口型对齐 + 水印",
}


def send_oral_failure(email: str, sid: str, error_step: str, error_message: str, refunded_credits: int = 0) -> bool:
    """口播带货任务失败 — 含已退积分信息。"""
    workbench_url = f"{SITE_BASE_URL}/video/oral-broadcast/{sid}"
    step_zh = _STEP_ZH.get(error_step or "", error_step or "—")
    msg_truncated = (error_message or "—")[:300]
    refund_line = f"<div>已退积分:{refunded_credits}</div>" if refunded_credits > 0 else ""
    html = f"""
<div style="font-family:sans-serif;max-width:520px;margin:40px auto;padding:36px;background:#fff;border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,0.08);">
  <h2 style="color:#0d0d0d;margin:0 0 20px 0;font-weight:500;">口播带货任务失败 ⚠️</h2>
  <p style="color:#666;line-height:1.7;">很抱歉,你的口播带货任务在 <b>{step_zh}</b> 失败了。失败步骤之前的扣费已按比例退还。</p>
  <div style="background:#fef3f2;border-radius:12px;padding:16px 20px;margin:20px 0;color:#7a1d1d;font-size:0.9rem;line-height:1.7;">
    <div>任务 ID:<span style="font-family:monospace;">{sid}</span></div>
    <div>失败步骤:{step_zh}</div>
    <div>原因:{msg_truncated}</div>
    {refund_line}
  </div>
  <a href="{workbench_url}" style="display:inline-block;padding:12px 28px;background:#0d0d0d;color:#fff;border-radius:10px;text-decoration:none;font-size:0.95rem;">打开工作台查看详情</a>
  <p style="color:#aaa;font-size:0.8rem;margin-top:30px;">若问题反复出现,可以联系客服反馈。<br>{workbench_url}</p>
</div>
"""
    return _send_resend(email, "【AI Lixiao】口播带货任务失败", html)
