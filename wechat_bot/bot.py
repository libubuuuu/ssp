"""
ailixiao WeChat Work (企业微信) Bot
Receives messages → executes server commands → replies via API
"""
import os
import sys
import time
import logging
import threading
import xml.etree.ElementTree as ET
from flask import Flask, request, abort

# Path for local imports
sys.path.insert(0, os.path.dirname(__file__))
from wechat_crypto import WxCrypto, WxCryptoError
from commands import handle_command

# ── config (required env vars) ────────────────────────────────────────────────
def _require(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v

TOKEN           = _require("WECHAT_TOKEN")
ENCODING_AES_KEY= _require("WECHAT_ENCODING_AES_KEY")
CORP_ID         = _require("WECHAT_CORP_ID")
AGENT_ID        = int(_require("WECHAT_AGENT_ID"))
CORP_SECRET     = _require("WECHAT_CORP_SECRET")
ADMIN_USER_ID   = _require("WECHAT_ADMIN_USER_ID")

crypto = WxCrypto(TOKEN, ENCODING_AES_KEY, CORP_ID)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wechat-bot")

app = Flask(__name__)

# ── access token cache ────────────────────────────────────────────────────────
_token_cache: dict = {"token": None, "expires": 0}

def _get_access_token() -> str:
    import requests as req
    now = time.time()
    if _token_cache["token"] and _token_cache["expires"] > now + 120:
        return _token_cache["token"]
    r = req.get(
        "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
        params={"corpid": CORP_ID, "corpsecret": CORP_SECRET},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"get_access_token error: {data}")
    _token_cache["token"] = data["access_token"]
    _token_cache["expires"] = now + data["expires_in"]
    return _token_cache["token"]


def _send_message(user_id: str, content: str) -> None:
    import requests as req
    # Truncate to WeChat's 2048-char text limit
    if len(content) > 2000:
        content = content[:1950] + "\n...(内容过长已截断)"
    try:
        token = _get_access_token()
        r = req.post(
            f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
            json={
                "touser": user_id,
                "msgtype": "text",
                "agentid": AGENT_ID,
                "text": {"content": content},
                "safe": 0,
            },
            timeout=15,
        )
        data = r.json()
        if data.get("errcode", 0) != 0:
            log.error("send_message errcode=%s errmsg=%s", data.get("errcode"), data.get("errmsg"))
    except Exception as e:
        log.error("send_message exception: %s", e)


def _execute_and_reply(user_id: str, text: str) -> None:
    """Run in background thread: execute command, send result via API."""
    log.info("cmd from %s: %r", user_id, text)
    result = handle_command(text)
    _send_message(user_id, result)

# ── webhook ───────────────────────────────────────────────────────────────────

@app.route("/wechat-bot", methods=["GET"])
def verify_url():
    """WeCom URL verification — returns decrypted echostr."""
    msg_sig  = request.args.get("msg_signature", "")
    timestamp= request.args.get("timestamp", "")
    nonce    = request.args.get("nonce", "")
    echostr  = request.args.get("echostr", "")
    try:
        plain = crypto.verify_url(msg_sig, timestamp, nonce, echostr)
        return plain, 200
    except WxCryptoError as e:
        log.warning("verify_url failed: %s", e)
        abort(403)


@app.route("/wechat-bot", methods=["POST"])
def receive_message():
    """Receive and dispatch WeCom message."""
    msg_sig  = request.args.get("msg_signature", "")
    timestamp= request.args.get("timestamp", "")
    nonce    = request.args.get("nonce", "")

    try:
        plain_xml = crypto.decrypt_message(request.data, msg_sig, timestamp, nonce)
    except WxCryptoError as e:
        log.warning("decrypt failed: %s", e)
        return "ok"  # always return ok to WeCom

    root = ET.fromstring(plain_xml)
    msg_type  = root.findtext("MsgType", "")
    from_user = root.findtext("FromUserName", "")
    content   = root.findtext("Content", "").strip()

    # Security: only the configured admin can issue commands
    if from_user != ADMIN_USER_ID:
        log.warning("rejected message from non-admin: %s", from_user)
        return "ok"

    if msg_type != "text":
        return "ok"

    # Dispatch asynchronously so we return 'ok' within WeCom's 5-second window
    threading.Thread(target=_execute_and_reply, args=(from_user, content), daemon=True).start()
    return "ok"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
