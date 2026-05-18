---
name: project-ssp-2026-05-18-daily
description: SSP 2026-05-18 AI爆款视频 UI改版/生成流程重构/TTS先跑传Seedance/场景铁律三重拦截，最新 commit e56b640
metadata: 
  node_type: memory
  type: project
  originSessionId: d2d8c3c9-760a-4cdb-b6f9-be30f908d3bb
---

# SSP 2026-05-18 大改动汇总

最新 commit: `e56b640`，已 push + deploy（当前激活 green）。

## 一、平台/市场选择重构

- Step3 chat 模式加 TikTok/抖音 + 国家选择卡片，选一次即可，Step4B 不再重复
- `platform` state（"tiktok"/"douyin"）传给后端 `/chat`
- 林久 system prompt：第一轮不再问平台/语言/时长，直接问产品本身
- 林久 prompt 注入 `{platform}` + `{target_lang}` + `{duration}秒`

## 二、知识库 + 每日趋势

- 知识库整合到林久/审稿员/文案师三个 prompt（`_get_knowledge_base()` 注入）
- 每日趋势自动更新系统（`trend_updater.py`）：
  - SEARCH_API_KEY 专用，`gpt-4o-search-preview-2025-03-11`
  - INSERT 后加 `conn.commit()` 修复数据未落盘 bug
  - 搜索失败时静默跳过（不阻断主流程）

## 三、TTS先跑 → audio_url 传 Seedance 原生口播（最终架构）

- TTS `await` 串行先跑，拿到 `audio_url` 再提交 Seedance
- Seedance `generate_audio: True`（环境音）+ `audio_urls: [tts_url]`（TTS口播）
- `enable_voice=False` 时不传 `audio_urls`，Seedance 仍有环境音
- **去掉 ffmpeg 音频合并整块**（不再下载/合并/再上传，节省 10-30s）
- pipeline: TTS → Seedance(+audio) → concat → upscale → 完成
- commit: `69eb3d4`

## 四、时长选项固定（5/10/15/30/60 秒）

- 前端：时长从自由输入改为 5 个固定按钮（默认 10，推荐）
  - Step3 chat 模式：平台+国家选择后加时长按钮（进入对话前选好）
  - 参数区保留时长下拉（非 chat 模式用）
- 后端验证：`_ALLOWED_DURATIONS = {5, 10, 15, 30, 60}`，不在集合内 400
- 计费改为严格用用户选的值：`cost = max(65, body.target_duration * 65)`（废弃旧 `_preview_cost` 按批拆算法）
- `ChatRequest.duration` 默认 10，`ScriptToVideoRequest.target_duration` 默认 10

## 五、场景图+文案改为"确认脚本后"才生成

- 脚本返回时：只保存 chatScript + 解析时长，**不再自动触发** generate-scene
- 用户点「确认脚本，生成视频」后：
  1. 并发 `generate-scene`（每个唯一场景标签）
  2. 调新端点 `POST /api/video/general/generate-copy`（脚本+平台+语言 → 文案，免费不扣积分）
  3. `setChatShowParams(true)` → 参数面板弹出
- 新增后端端点：`GenerateCopyRequest` + `/generate-copy`（复用 `_call_copywriter`）

## 六、林久 prompt 加固

- **修改脚本铁律**（最高优先级）：修改时必须输出完整 `===SCRIPT_START===...===SCRIPT_END===`，禁止只描述/只给局部
- **场景数量规则**：5/10秒→1场景，15秒→≤2，30秒→≤3，60秒→≤4

## 七、Seedance prompt 加 scene_descriptions 序列

- `jobs.py` 中从 `task["scenes"]` 提取 `visual_prompt`，用 ` → ` 连接
- 追加到 Seedance prompt 末尾：`Scene action sequence: A → B → C.`

## 八、task 拆分改回纯时长

- 去掉 `_scene_changed` 场景边界判断（导致"公园跑道"≠"同一跑道"过度拆分）
- 恢复纯 `MAX_DUR` 时长拆分，场景图生成不受影响（仍按 scene_label 各自生成）

## 九、UI 调整

- 对话区 `main` maxWidth: 760→1000，padding: "0 1.5rem"→"0 0.5rem"
- 底部输入栏：改回 `position: sticky`（fixed 导致遮挡问题已 revert），padding "0.7rem 0"
- 输入框加大：padding 0.8rem 1rem，fontSize 0.95rem，minHeight 44，borderRadius 12
- 发送按钮 + 附件按钮同步加大

## 审稿员触发逻辑（确认，无需改动）

每次 reply_text 含 `===SCRIPT_START===` 就触发，`if script:` 无条件，
每次修改脚本都重新审查 ✅

## 十、Seedance prompt 结构重构（bc45254）

旧结构：`@Image1 EXACT visual → 动作描述 → CRITICAL: Strictly match`（图片优先，文字被压制）

新结构：
```
Generate a {N}-second continuous video with the following actions: {scene_descriptions}.
{model_line}{portrait_line}
@Image1 is the reference for the model's appearance (face, body type).
{ref_tags}
{env_line}
Use natural, cinematic movement.
{NO_TEXT}
```
- 动作描述置顶（Seedance 最先解析）
- 去掉 `CRITICAL: Strictly match all visual details` 和 `Ignore any color words`
- `@Image1` 降格为外观参考，不再锁死所有视觉细节

## 十一、场景数量铁律三重拦截（e56b640）

prompt 里场景规则出现三次，防止 LLM 忽略：
1. `===SCRIPT_START===` **之前**：`⛔⛔⛔ 场景数量铁律（写脚本前必须先检查！违反=废稿！）`
2. 验算铁律：输出前必验算①时长 ②场景数量，任何一项不合格就重写
3. 镜头格式要求清单：场景数量限制写进格式条目

旧弱版 `⛔ 场景数量规则` 已删除（避免重复/混淆）。

**Why:** 2026-05-18 集中完善 AI 爆款视频：固定时长/确认后才生图文案/TTS原生口播/prompt优先级重构/场景铁律三重拦截。
**How to apply:** 当前稳定架构 = TTS先跑+传Seedance原生口播 + 动作描述置顶prompt + 场景数量三重校验。不要回退。
