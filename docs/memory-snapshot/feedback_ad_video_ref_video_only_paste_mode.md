---
name: AI带货视频参考视频只在黏贴脚本模式用
description: /ad-video 黏贴脚本模式才用参考视频帧,auto 模式按提示词做不蹭参考视频
type: feedback
originSessionId: bf5e5bd4-8e8a-4d86-89e7-4176ad18cde7
---
/ad-video 两种脚本来源,参考视频处理也必须分开:

- **scriptMode === "paste"**(黏贴脚本):允许传 `reference_video_frame_url` + `ref_video_has_people` → 后端用参考视频中间帧作背景 + has_people 路由
- **scriptMode === "auto"**(AI 自动生成):前端强制传 `null` → 后端纯按提示词出图,即使 styleRefMiddleUrl/styleRefHasPeople state 还在也不发

**Why:** 用户讲"只有上传视频这个功能才需要参考视频画面,但是呢其他得另一个功能是提示词做得就要用提示词做得"。auto 模式没传参考视频但 React state 可能残留(用户切过模式),不能让残留 state 漏到后端。

**How to apply:** 任何时候在 callPreview/callGenerate body 里传 ref_video 相关字段,必须三元 gate `scriptMode === "paste" ? X : null`。新加任何 ref_video 衍生字段也要套同样 gate。
