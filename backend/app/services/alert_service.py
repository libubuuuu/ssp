"""实时告警服务

- 纯 Python HTTP，不走 subprocess，不阻塞请求（后台 daemon 线程）
- 每个 alert_key 有独立冷却窗口（默认 5 分钟），防刷屏
- 支持 PushPlus / Server酱（与 push-alert.sh 读同一配置文件）
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_CONFIG_PATH = "/root/.ssp-watchdog-config"
_COOLDOWN_SECONDS = 300  # 5 分钟同一 key 不重复推送
_cooldowns: dict[str, float] = {}
_lock = threading.Lock()


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
    cooldown=0 强制每次都推（用于一次性事件，如部署回滚）。
    """
    key = alert_key or title
    now = time.time()
    if cooldown > 0:
        with _lock:
            if now - _cooldowns.get(key, 0) < cooldown:
                return
            _cooldowns[key] = now
    threading.Thread(target=_push_sync, args=(title, body), daemon=True).start()
