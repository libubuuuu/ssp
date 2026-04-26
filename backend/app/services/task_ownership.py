"""任务归属注册表 — 防越权订阅 WS 进度

WS 用的 task_id 是 FAL request_id,本身没承载用户身份。前端任意登录用户拿到
别人的 task_id 就能订阅别人的进度推送。

方案:在 API 提交任务、拿到 FAL request_id 时,把 (task_id → user_id) 写入
本注册表;WS 接到连接时校验当前 token 的 user_id 与注册的 owner 一致。

为什么不入库:
- generation_history 用 record_id 做主键,task_id 字段没存
- tasks 表存在但全库无人写
- 任务最长 10 分钟,内存注册够用,backend 重启场景重新提交即可

设计点:
- TTL 30 分钟(覆盖最长任务 + 余量)
- 进程级 dict,multi-worker 时每个 worker 各自一份(当前 uvicorn 单 worker,OK)
- 写少读少,普通 dict + 单锁足够,不上 Redis
"""
import threading
import time
from typing import Optional

_TTL_SECONDS = 30 * 60  # 30 分钟

# {task_id: (user_id, registered_at_epoch)}
_owners: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def _gc_locked() -> None:
    """删过期项。调用方必须持有 _lock。惰性触发,不开后台线程。"""
    now = time.time()
    expired = [tid for tid, (_, ts) in _owners.items() if now - ts > _TTL_SECONDS]
    for tid in expired:
        _owners.pop(tid, None)


def register(task_id: str, user_id: str) -> None:
    """提交 FAL 任务后立即调,记录 task_id 归属。"""
    if not task_id or not user_id:
        return
    with _lock:
        _owners[task_id] = (str(user_id), time.time())
        # 写入时顺带 GC,避免长尾积累
        if len(_owners) % 50 == 0:
            _gc_locked()


def verify(task_id: str, user_id: str) -> bool:
    """WS 连接时调,检查归属。

    返回:
    - True:task_id 已注册且 owner 是 user_id
    - False:未注册 / 已过期 / owner 不匹配(三种统一对外不区分,防信息泄漏)
    """
    if not task_id or not user_id:
        return False
    with _lock:
        rec = _owners.get(task_id)
        if rec is None:
            return False
        owner, ts = rec
        if time.time() - ts > _TTL_SECONDS:
            _owners.pop(task_id, None)
            return False
        return owner == str(user_id)


def unregister(task_id: str) -> None:
    """任务终态(完成/失败)后可调,提前释放。不调也行,TTL 兜底。"""
    with _lock:
        _owners.pop(task_id, None)


def _clear_for_test() -> None:
    """仅供测试用,重置注册表。"""
    with _lock:
        _owners.clear()
