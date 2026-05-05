"""storyboard probe:VLM + 并发 Kontext 真出差异化 N 张分镜图

memory fal_probe_first 教训:
- 不只看 submit OK,看真视频/图质量
- 抽 N 张图实测,对比是否真有镜头/景别差异

测试输入:复用 task 36d49f96 的 base_image(模特+产品+背景合成首帧),
  description="女性穿戴塑身衣的多镜头爆款带货,5 段分镜从钩子到 CTA",
  n_frames=5

verify:
- VLM 输出 5 段不同 visual_prompt(每段景别/角度/动作不同)
- 5 张 Kontext 图差异化(景别真不同,不是 5 张几乎一样)
- 模特身份 + 产品款式跨段一致(参考图主体保留)
"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/ssp/backend")  # 用源目录(包含新写的 storyboard_service)
from app.services.storyboard_service import generate_storyboard


REFERENCE_IMAGE = "https://v3b.fal.media/files/b/0a98f5fb/hCx3vr24RgQbFDd3m2vCT_p119_real_base.jpg"
DESCRIPTION = "女性塑身衣爆款带货短视频 5 段分镜:钩子(产品大特写抓眼球)→ 模特正面展示 → 360 度转身展示反面收紧效果 → 场景化(在卧室/客厅穿着自然) → CTA 结尾(模特微笑指向产品)"


async def main():
    print("=" * 72)
    print("storyboard probe:VLM + 5 段 Kontext")
    print(f"reference: {REFERENCE_IMAGE}")
    print(f"description: {DESCRIPTION[:80]}...")
    print("=" * 72)

    result = await generate_storyboard(
        reference_image_url=REFERENCE_IMAGE,
        description=DESCRIPTION,
        n_frames=5,
        aspect_ratio="9:16",
    )

    if "error" in result:
        print(f"\n❌ FAIL: {result['error']}")
        return 1

    print(f"\n✅ overall_theme: {result.get('overall_theme')}")
    print(f"✅ success: {result.get('success_count')}/{result.get('total_count')}\n")

    for i, f in enumerate(result.get("frames", []), 1):
        status = "✅" if f.get("image_url") else f"❌ ({f.get('error', '?')[:50]})"
        print(f"段 {i} {status}")
        print(f"  title:    {f.get('title')}")
        print(f"  purpose:  {f.get('purpose')}")
        print(f"  shot:     {f.get('shot_type')}")
        vp = f.get("visual_prompt", "")
        print(f"  vp[80]:   {vp[:80]}{'...' if len(vp) > 80 else ''}")
        if f.get("image_url"):
            print(f"  image:    {f['image_url']}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
