---
name: 修复 bug 前必须扫所有同类路径,不能只改撞到的那条
description: 用户报失败后我曾连续 3 次"修了又挂"(P173/P175/P176),原因是只修了报错那条 code path,没扫并行的其它 path 有没有同样漏洞
type: feedback
originSessionId: bf5e5bd4-8e8a-4d86-89e7-4176ad18cde7
---
**铁律:用户报某个 pattern 的失败后,先 grep 找所有同类入口,一次性扫完再动手 — 不能只改报错那一条。**

**Why:** 2026-05-07 视频复刻 bug 修复过程:
- 第 1 次失败 → 我修 pixverse-2step Step B mask(P173),声称修好
- 第 2 次失败 → 同函数 Step A mask 也挂(P174 + P175),又声称修好
- 第 3 次失败 → 用户切到 pixverse-swap(单步),发现根本没碰过(P176)
- 用户怒了:"你做好能不能在好好检查深度思考一下啊,搞成这样"
- 第 4 次扫干净:发现还有 seedance-lite-i2v 也没兜(P177)

每次"修复"只改了被报错砸到的函数,没注意 replicate 有 4 条独立 engine 路径(pixverse-swap / pixverse-2step / seedance-lite-i2v / catvton-pixverse)。每条路独立 `gather` + 独立 `raise`,共享同一漏洞 pattern。

**How to apply:**
- 用户报"X 失败"后第一步:`grep -nE "^async def _gen_videos_|return await _asyncio.gather|raise RuntimeError" <file>`,看清楚有几条同类入口
- 设计修复时考虑 module-level 共享 helper,避免每条路 inline 一份(后期增删 engine 必然漏)
- 给用户回复"修好了"前问自己:**"这个失败 pattern 在文件里还能在哪触发?都覆盖了吗?"**
- 不要等用户测 4 次帮我扫
