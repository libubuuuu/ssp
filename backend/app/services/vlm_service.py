"""
VLM 视觉服务 - AI 带货视频专用

替代 v2 的 claude_vision.py:
- 不再依赖 anthropic SDK + ANTHROPIC_API_KEY
- 改用 fal-ai 的 OpenRouter Vision 端点(openrouter/router/vision)
- 复用现有 FAL_KEY,零新成本

模型选型:
- 默认 qwen/qwen3-vl-235b-a22b-instruct (中文理解最强,带货脚本撰写优势明显)
- 可降级到 google/gemini-2.5-flash (便宜快,质量稍弱)
- 可升级到 anthropic/claude-sonnet-4.5 (质量最高,价格高)

调用方式:
- 用 fal_client.run_async(已有依赖)
- 入参 image_urls (list of URL 字符串)
- 出参 result["output"] 是 LLM 返回的纯文本
- 我们 prompt 让它输出 JSON,然后在 Python 层解析
"""
from __future__ import annotations

import json
import re
from typing import Optional
import fal_client

from .circuit_breaker import get_circuit_breaker
from .logger import log_info, log_error


# ============== 配置 ==============

# 默认模型 — 中文带货场景首选 Qwen3-VL,中文理解 + 中文输出双优
DEFAULT_MODEL = "qwen/qwen3-vl-235b-a22b-instruct"

# 备选模型(熔断时降级用)
FALLBACK_MODEL = "google/gemini-2.5-flash"

VISION_ENDPOINT = "openrouter/router/vision"


# ============== Prompt 模板 ==============

_ANALYSIS_PROMPT = """你是一个电商带货视频脚本编剧。请分析用户上传的产品图,完成两件事:

【任务一:审核】
- 图片质量(清晰度/光线/白底)
- 是否有违规内容(侵权 logo / 违禁品 / 不雅内容 / 政治敏感)
- 识别产品品类、颜色、材质、目标人群

【任务二:生成 15 秒带货视频脚本】
按 Seedance 2.0 的标准格式输出三段分镜(每段 5 秒):
- 镜头一(0-5s):开场吸引
- 镜头二(5-10s):产品展示
- 镜头三(10-15s):促单 CTA

每段必须有:shot_language(镜头语言) / content(场景内容) / visual_prompt(视觉提示词,英文,给视频模型) / speech(说话内容,英文,口语化带货)

【输出格式】
严格按以下 JSON 返回,不要任何 markdown 标记或额外说明:

{
  "audit": {
    "is_valid": true,
    "category": "产品品类",
    "color": "主色",
    "material": "材质",
    "quality_score": 8.5,
    "issues": [],
    "violations": [],
    "target_audience": "目标人群"
  },
  "script": {
    "overall_setting": "整体设定(拍摄风格/模特特征,中文)",
    "model_description": "推荐模特特征,英文,给视频模型用",
    "scenes": [
      {
        "id": 1,
        "time_range": "0-5s",
        "purpose": "开场吸引",
        "shot_language": "中文描述拍摄角度+动作",
        "content": "中文场景描述",
        "visual_prompt": "English visual prompt for video model",
        "speech": "English speech line"
      },
      {"id": 2, "time_range": "5-10s", "purpose": "产品展示", ...},
      {"id": 3, "time_range": "10-15s", "purpose": "促单", ...}
    ]
  }
}

如果图片有严重违规(色情 / 暴力 / 政治敏感),audit.is_valid 设为 false,violations 列出原因,scene 数组返回空 []。

不要输出 ```json 或任何 markdown 标记,直接输出纯 JSON。"""


_SYSTEM_PROMPT = (
    "You are a JSON-only API. Output strict valid JSON without any markdown "
    "fences, prose, or explanation. The JSON must match the schema requested in the user prompt."
)


class VLMService:
    """VLM 视觉服务 - 单例,通过 fal OpenRouter 端点调用"""

    SERVICE_KEY = "fal/openrouter-vision"  # 熔断器 key

    def __init__(self):
        # 不需要单独 key,fal_client 会从环境变量 FAL_KEY 读
        pass

    async def analyze_product(
        self,
        image_url: str,
        model: Optional[str] = None,
    ) -> dict:
        """
        分析产品图 + 生成脚本

        参数:
            image_url: 产品图的 fal storage URL(由 /api/ad-video/upload/image 返回)
            model: 可选,指定 VLM 模型;默认走 DEFAULT_MODEL

        返回:
            {"audit": {...}, "script": {...}}  成功
            {"error": "..."}                    失败
        """
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(self.SERVICE_KEY):
            return {"error": "VLM 视觉服务暂时不可用,请稍后再试"}

        chosen_model = model or DEFAULT_MODEL

        # 调用 fal OpenRouter Vision
        try:
            result = await fal_client.run_async(
                VISION_ENDPOINT,
                arguments={
                    "image_urls": [image_url],
                    "prompt": _ANALYSIS_PROMPT,
                    "system_prompt": _SYSTEM_PROMPT,
                    "model": chosen_model,
                },
            )
        except Exception as e:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            log_error(f"VLM 调用失败 (model={chosen_model}): {e}")
            # 主模型失败时尝试降级模型一次
            if chosen_model != FALLBACK_MODEL:
                log_info(f"尝试降级到 {FALLBACK_MODEL}")
                try:
                    result = await fal_client.run_async(
                        VISION_ENDPOINT,
                        arguments={
                            "image_urls": [image_url],
                            "prompt": _ANALYSIS_PROMPT,
                            "system_prompt": _SYSTEM_PROMPT,
                            "model": FALLBACK_MODEL,
                        },
                    )
                except Exception as e2:
                    return {"error": f"VLM 主备模型均失败: {str(e2)[:200]}"}
            else:
                return {"error": f"VLM 调用失败: {str(e)[:200]}"}

        # 解析响应
        text = result.get("output", "")
        if not text:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            return {"error": "VLM 返回为空"}

        try:
            # 去 markdown 标记(以防模型不听话)
            cleaned = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                text.strip(),
                flags=re.MULTILINE,
            )
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            log_error(f"VLM 响应解析失败: {e}, 原文前 500 字: {text[:500]}")
            return {"error": "AI 输出格式异常,请重试"}

        # 校验结构
        if "audit" not in data or "script" not in data:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            return {"error": "AI 输出结构不完整,请重试"}

        await circuit_breaker.record_success(self.SERVICE_KEY)
        log_info(
            f"VLM 分析完成: model={chosen_model} "
            f"category={data['audit'].get('category')} "
            f"valid={data['audit'].get('is_valid')}"
        )
        return data

    async def regenerate_scene(
        self,
        original_scene: dict,
        instruction: str,
        model: Optional[str] = None,
    ) -> dict:
        """
        重新生成单个分镜(用户在编辑器里点'重新生成此镜头'时调用)

        这个不需要图片,纯文本对话。但还是走 OpenRouter Vision 端点(它也兼容纯文本)。
        实际上 fal 还有个纯文本的 openrouter/router 端点,但为了简化代码我们都走同一个。

        参数:
            original_scene: 原 scene dict
            instruction: 用户给的修改指令(中文)
            model: 可选

        返回:
            新 scene dict 或 {"error": "..."}
        """
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(self.SERVICE_KEY):
            return {"error": "VLM 服务暂时不可用"}

        chosen_model = model or DEFAULT_MODEL

        prompt = f"""根据用户指令修改以下分镜,严格按原 JSON 格式输出(不要 markdown,直接输出纯 JSON):

原分镜:
{json.dumps(original_scene, ensure_ascii=False, indent=2)}

用户修改指令:
{instruction}

输出修改后的 JSON,字段保持一致(id / time_range / purpose / shot_language / content / visual_prompt / speech)。"""

        try:
            # 即便没图片,这个端点也要 image_urls 字段。
            # 我们传一个 1x1 透明占位图(fal 自家 CDN)规避 schema 校验。
            # 模型会忽略这张图,只看 prompt。
            placeholder = "https://fal.media/files/placeholder/blank-1x1.png"

            result = await fal_client.run_async(
                VISION_ENDPOINT,
                arguments={
                    "image_urls": [placeholder],
                    "prompt": prompt,
                    "system_prompt": _SYSTEM_PROMPT,
                    "model": chosen_model,
                },
            )
            text = result.get("output", "")
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
            new_scene = json.loads(cleaned)
            await circuit_breaker.record_success(self.SERVICE_KEY)
            return new_scene
        except Exception as e:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            return {"error": f"重新生成失败: {str(e)[:200]}"}


# ============== 单例 ==============

_vlm_service: Optional[VLMService] = None


def init_vlm_service():
    """在 main.py 启动时调用(无参,从 fal_client 拿 FAL_KEY)"""
    global _vlm_service
    _vlm_service = VLMService()
    log_info(f"VLM 视觉服务已初始化(默认模型: {DEFAULT_MODEL},端点: {VISION_ENDPOINT})")


def get_vlm_service() -> Optional[VLMService]:
    return _vlm_service
