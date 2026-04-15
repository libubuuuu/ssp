"""
飞书 Webhook 同步
- 提示词修改记录
- 可写入多维表格
"""
import httpx
from app.config import get_settings


async def sync_prompt_edit(
    task_id: str,
    shot_index: int,
    original_prompt: str,
    user_modified_prompt: str,
):
    """同步镜头提示词修改到飞书"""
    settings = get_settings()
    if not settings.FEISHU_WEBHOOK_URL:
        return

    text = (
        f"【视频提示词修改】\n"
        f"任务ID: {task_id}\n"
        f"镜头: {shot_index}\n"
        f"原提示词: {original_prompt[:200]}{'...' if len(original_prompt) > 200 else ''}\n"
        f"修改后: {user_modified_prompt[:200]}{'...' if len(user_modified_prompt) > 200 else ''}"
    )
    payload = {"msg_type": "text", "content": {"text": text}}

    async with httpx.AsyncClient() as client:
        await client.post(settings.FEISHU_WEBHOOK_URL, json=payload)
