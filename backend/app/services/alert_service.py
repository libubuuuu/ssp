"""实时告警服务

- 纯 Python HTTP，不走 subprocess，不阻塞请求（后台 daemon 线程）
- 每个 alert_key 有独立冷却窗口（默认 5 分钟），防刷屏
- 支持 PushPlus / Server酱（与 push-alert.sh 读同一配置文件）
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

_CONFIG_PATH = "/root/.ssp-watchdog-config"
_COOLDOWN_SECONDS = 300  # 5 分钟同一 key 不重复推送
_cooldowns: dict[str, float] = {}
_lock = threading.Lock()

# 模块名 → 中文功能名（用于告警正文）
_MODULE_LABELS: dict[str, str] = {
    "image/style":            "AI 图片生成",
    "image/multi-reference":  "多图融合生成",
    "video/clone":            "视频复刻",
    "video/image-to-video":   "图生视频",
    "video/replace/element":  "视频元素替换",
    "ad_video/generate":      "AI 爆款视频生成",
    "ad_video/analyze":       "视频智能分析",
    "ad_video/preview":       "视频预览生成",
    "ad_video/scene_regen":   "场景重生成",
    "frame_extract":          "视频拆帧分镜",
    "admin/adjust-credits":   "管理员积分调整",
    "task_refund":            "积分退款",
}


def module_label(module: str, fallback: str = "") -> str:
    """把内部 module 名转成用户能看懂的中文功能名。"""
    if module in _MODULE_LABELS:
        return _MODULE_LABELS[module]
    # 前缀匹配
    for k, v in _MODULE_LABELS.items():
        if module.startswith(k):
            return v
    return fallback or module or "未知功能"


def format_alert(problem: str, feature: str, details: str) -> str:
    """生成格式统一的告警正文（微信推送可读）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"🕐 时间: {ts}\n"
        f"❌ 问题: {problem}\n"
        f"🎯 功能: {feature}\n"
        f"📋 详情: {details}"
    )


def _load_config() -> dict[str, str]:
    config: dict[str, str] = {}
    if not os.path.exists(_CONFIG_PATH):
        return config
    try:
        with open(_CONFIG_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    config[k.strip()] = v.strip()
    except Exception:
        pass
    return config


def _push_sync(title: str, body: str) -> None:
    config = _load_config()
    token = config.get("PUSHPLUS_TOKEN", "")
    if token:
        try:
            payload = json.dumps({
                "token": token, "title": title,
                "content": body, "template": "txt",
            }).encode()
            req = urllib.request.Request(
                "http://www.pushplus.plus/send", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass

    sc_key = config.get("SERVERCHAN_KEY", "")
    if sc_key:
        try:
            payload = urllib.parse.urlencode({"title": title, "desp": body}).encode()
            req = urllib.request.Request(
                f"https://sctapi.ftqq.com/{sc_key}.send",
                data=payload, method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass


def push_alert(title: str, body: str, *, alert_key: Optional[str] = None, cooldown: int = _COOLDOWN_SECONDS) -> None:
    """非阻塞推送告警。

    alert_key: 冷却窗口 key，默认用 title。同一 key 在 cooldown 秒内只推一次。
    cooldown=0 强制每次都推（用于一次性事件，如部署回滚、单个任务退款失败）。
    """
    key = alert_key or title
    now = time.time()
    if cooldown > 0:
        with _lock:
            if now - _cooldowns.get(key, 0) < cooldown:
                return
            _cooldowns[key] = now
    threading.Thread(target=_push_sync, args=(title, body), daemon=True).start()
