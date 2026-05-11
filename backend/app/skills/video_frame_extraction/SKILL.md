# Skill: video_frame_extraction

> 视频 → 场景切分 → 关键帧 → 九宫格大图(可选 AI 描述 + 语音文字)
>
> 入口:`from app.skills.video_frame_extraction import VideoFrameSkill`
>
> 创建:2026-05-11(原 `video_general_v3` 路径内联实现拆分独立 skill)
> 最近更新:2026-05-12 — 文档化 + 调研未来扩展

---

## 1. 功能说明

把一段视频拆成 "代表性的 N 张帧",并拼成一张 1024×1024 的网格 PNG(带 1..N 编号水印),供下游 LLM 视觉模型一次性读完整段叙事。

设计目的是替代 "上传 N 张独立分镜图" 的传统 i2v 工作流,改成 "上传一段参考视频 → 自动拆帧 → 把 N 个镜头作为分镜参考一次性喂给视频模型" 的 v3 路线。

三个核心步骤独立可用,也可以 `process()` 一把端到端调用。

### 适用场景

- 视频复刻 / 二创:把参考视频拆成镜头 → 喂 Seedance r2v 当多图参考
- 视频脚本提取:拆帧 + VLM 描述 → 自动产出分镜脚本
- 视频内容审核 / 摘要:抽关键帧 → 视觉模型读
- 视频 → 故事板:做 educational / marketing 类素材

### 不适用场景

- 实时流处理(本 skill 一次性读完整段视频,不是流式)
- 需要细粒度 motion vector / optical flow 分析的(本 skill 拿的是"代表帧",不是运动信息)
- 单帧 ≤ 320×240 极低清晰度的(网格 cell 抠裁会糊)

---

## 2. 模块结构

```
app/skills/video_frame_extraction/
├── __init__.py          # 公共入口:VideoFrameSkill 类 + 顶层函数 re-export
├── scene_detector.py    # PySceneDetect ContentDetector 封装
├── keyframe_extractor.py # ffmpeg 抽中间帧封装
├── grid_composer.py     # PIL 拼九宫格 + 编号水印封装
├── video_analyzer.py    # AI 视觉分析 stub(等接入 VLM)
├── exceptions.py        # 异常树
├── SKILL.md             # 本文档
└── tests/
    ├── conftest.py             # ffmpeg testsrc/color fixture
    ├── test_scene_detector.py  # 4 用例
    ├── test_keyframe_extractor.py  # 5 用例
    ├── test_grid_composer.py   # 6 用例
    └── test_full_flow.py       # 4 用例(端到端)
```

---

## 3. 输入 / 输出 Schema

### `VideoFrameSkill.process(video_path, grid_size=9, ...) → dict`

**输入参数**

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `video_path` | str | — | 本地视频文件绝对路径,必须存在 |
| `grid_size` | int | 9 | 目标网格数:9 → 3×3,6 → 2×3,4 → 2×2,2 → 1×2 |
| `include_speech` | bool | False | 当前 stub,见"未来扩展" |
| `use_ai_keyframe_selection` | bool | False | 当前 stub,见"未来扩展" |
| `output_dir` | str | `/tmp/v3_frames` | 抽出的 jpg 帧落地目录 |

**返回 dict**

| 键 | 类型 | 说明 |
|---|---|---|
| `scenes` | list[dict] | 每段 scene 的 `{idx, start_seconds, end_seconds, duration_seconds}` |
| `keyframe_paths` | list[str] | N 个 jpg 帧绝对路径,顺序 = scenes 顺序 |
| `grid_path` | str | 网格 PNG 绝对路径(1024×1024) |
| `n_frames` | int | 实际帧数(可能 < grid_size,scene 不够时) |
| `layout` | tuple[int, int] | `(rows, cols)`,根据 grid_size 计算 |

---

## 4. 使用示例

### 端到端(典型用法)

```python
from app.skills.video_frame_extraction import VideoFrameSkill

skill = VideoFrameSkill()
result = skill.process(
    "/tmp/user_uploaded.mp4",
    grid_size=9,
    output_dir="/tmp/v3_frames/job_abc",
)

print(result["grid_path"])      # /tmp/v3_grid_xxx.png
print(len(result["scenes"]))    # 实际检测出的场景数
print(result["keyframe_paths"]) # 每个 scene 一张 jpg
```

### 分步调用(需要中间结果时)

```python
from app.skills.video_frame_extraction import (
    detect_scenes, extract_keyframes, compose_grid,
)

scenes = detect_scenes("/tmp/v.mp4", threshold=27.0)
frames = extract_keyframes("/tmp/v.mp4", scenes, output_dir="/tmp/frames")
grid = compose_grid(frames, layout=(3, 3), output_path="/tmp/grid.png")
```

### 跟现有 v3 工作流对接(jobs.py 内调用范例)

```python
# 替换之前 jobs.py 里手撸的 scenedetect + ffmpeg + PIL 三段
skill = VideoFrameSkill()
out = skill.process(local_video, grid_size=9, output_dir=task_workdir)
grid_url = upload_to_storage(out["grid_path"])  # 喂给 Seedance r2v 当参考
```

---

## 5. 配置参数 / 调优

### scene_detector

- `threshold=27.0`(默认):内容变化阈值,**越低越敏感**
  - 静态 PPT / 直播录屏:试 20-22(捕捉细微切换)
  - 真实拍摄运动镜头:用默认 27,或升到 30-35 抑制运动误判
- `min_scene_len=15`(帧):小于此长度的场景被合并,防抖动碎片
- **fallback 行为**:`scenedetect` 返回 0 段时(单镜头视频)自动 probe 视频时长,返回 1 个全片 Scene。**这一步用 cv2 做,所以 cv2 是硬依赖**(`scenedetect[opencv]` 默认带)

### keyframe_extractor

- `timestamp_strategy="midpoint"`:取场景中点。换 `"start"` 取场景起点
- `jpeg_quality=2`:ffmpeg `-q:v 2`(1-31,越小越高质),2 是肉眼无损 + 占用合理
- ffmpeg 命令用 **前置 `-ss`**(快速 seek,近似关键帧),3-5s 视频实测毫秒级抽帧;若需精确到帧再加 `-accurate_seek`(慢 10x+)
- 输出目录会被 `os.makedirs(exist_ok=True)`,**不会清空旧文件**,文件名 `frame_{idx:03d}.jpg` 同 idx 会覆盖

### grid_composer

- `canvas_size=1024`:输出 PNG 边长。下游 VLM 偏好 1024,改了得跟下游对齐
- `border=4`:格子间白边像素
- `show_index=True`:左上角黑底白字 "1".."N",方便 VLM 引用 "第 3 帧"
- **不足填充策略**:`len(frames) < rows*cols` 时,**重复最后一张**填满,不报错
- **超额截断策略**:`len(frames) > rows*cols` 时,**截取前 N 张**

### `_layout_for_grid_size` 映射

| grid_size | layout |
|---|---|
| ≤ 2 | (1, 2) |
| ≤ 4 | (2, 2) |
| ≤ 6 | (2, 3) |
| ≥ 7 | (3, 3) |

---

## 6. 性能特征

实测(2026-05-12,ffmpeg 6s 视频,本地 SSD,supervisor 同机):

| 阶段 | 6s 多镜头 | 3s 单镜头 |
|---|---|---|
| scene 检测 | ~0.8s | ~0.6s |
| 抽 9 帧 | ~0.4s(9 × 50ms) | ~0.1s(1 帧) |
| 拼九宫格 | ~0.3s | ~0.2s |
| **总计** | **~1.5s** | **~0.9s** |

19 个单元测试 + 端到端 fixture 整套 5.2s 跑完。

### 内存峰值

PIL `canvas_size=1024` + 9 张 frame in-memory ≈ 30-40MB,可忽略。

### 并发约束

- ffmpeg 子进程,每段视频用一个进程,**不共享 GIL**,可放多 worker 并行
- PySceneDetect 内部用 OpenCV decode,**单视频单线程**;多视频可并行
- 当前 jobs.py 走 asyncio + 9 并发 sem,该 skill 是同步函数,在 worker 里直接调用即可

---

## 7. 依赖列表

| 包 | 版本 | 来源 |
|---|---|---|
| `scenedetect[opencv]` | ≥ 0.6.4 | pip(已装) |
| `Pillow` | ≥ 10.0 | pip(已装,sspx 通用依赖) |
| `ffmpeg` | ≥ 4.x | 系统(apt,已装) |
| **可选**(stubs 等接入) | | |
| `openai` | for DeepSeek-VL OpenAI-compatible | 已装(走 fal 也可) |
| `requests` | for fal.ai HTTP | 已装 |

**安装**(若新环境):

```bash
# 系统
apt-get install ffmpeg

# Python(注意 --break-system-packages 是 PEP 668 要求,本机 venv 之外用)
pip install "scenedetect[opencv]>=0.6.4" Pillow --break-system-packages
```

---

## 8. 错误码 / 异常树

```
VideoFrameError                     # 基类
├── SceneDetectionError             # 文件不存在 / PySceneDetect 抛错 / 0 frame
├── KeyframeExtractionError         # ffmpeg rc!=0 / timeout / 输出 < 100 bytes
└── GridCompositionError            # 空 list / 帧文件丢失 / 图损坏 / 写盘失败
```

所有异常都带可读 message,适合直接冒泡到 API 层转 4xx/5xx。下游处理建议:

| 异常 | 用户看到 | 该做 |
|---|---|---|
| `SceneDetectionError` "video not found" | 4xx "视频文件丢失" | 检查上传链路 / 临时文件 GC |
| `SceneDetectionError` "PySceneDetect failed" | 5xx "视频解析失败" | 看 ffmpeg/cv2 能否打开此视频(可能编码不支持) |
| `KeyframeExtractionError` "timed out" | 5xx "视频抽帧超时" | 视频太长 / 太大,考虑预压 |
| `KeyframeExtractionError` "rc=..." | 5xx | stderr 末 300 字符已带在 message 里,日志查 |
| `GridCompositionError` "frame missing" | 5xx | tmpfs 满 / 被并发清理 |

---

## 9. 测试

```bash
cd /opt/ssp/backend
venv/bin/pytest app/skills/video_frame_extraction/tests/ -v
```

**2026-05-12 实测**:19 passed in 5.20s(0 failed,0 skipped)。

测试自带 ffmpeg fixture(`testsrc` + `color=red/green/blue` 合成 3-6s 视频),**不依赖外部测试素材**,新机器 clone 后直接能跑。

---

## 10. 未来扩展(2026-05-12 调研笔记,**尚未实装**)

下面三块都是 stub,代码占位但 `raise NotImplementedError`。等用户决定再接。

### 10.1 AI 选帧 / 视觉描述 — `analyze_with_ai()`

**目标**:把当前的 "midpoint 中点取帧" 升级成 "VLM 选最有信息量的一帧",或者抽完帧后再用 VLM 写每张帧的描述,产出结构化分镜脚本。

**候选模型**

| 选项 | 端点 | 特点 | 估算价 |
|---|---|---|---|
| **DeepSeek-VL2** | `https://api.deepseek.com/chat/completions`(OpenAI 兼容) | 中文强 / MoE 7B / 已部署在 deepseek 平台;走 `deepseek-vl2-chat` 之类的 model_id(实际 model_id 需查 list-models 端点) | 待用户开 DeepSeek 平台账号查 |
| **fal nano-banana** | `fal-ai/nano-banana`(2.5 Flash Image)/ `fal-ai/nano-banana-2`(3.1 Flash Image)/ `fal-ai/nano-banana-pro`(3 Pro Image) | Google Gemini Image,生成 + 编辑能力强,**视觉理解** 走对应 Gemini API 而非 fal 的 image edit 端点 | nano-banana-2 是 nano-banana 系列里的 Flash 档,适合做帧描述 |
| **qwen-vl-max**(已部署) | 走 DashScope `DASHSCOPE_API_KEY`,本项目已有 | 中文最稳,本仓库 jobs.py 已用过 | 约 ¥0.02 / 张分析 |

**推荐路径**:**先试 qwen-vl-max**(本仓库 .env.enc 已有 key,零接入成本),效果不够再切 DeepSeek-VL2。`fal-ai/nano-banana` 系列定位是图像 **生成 + 编辑**,做 "理解 + 描述" 不如纯 VLM,**不推荐用于本 skill**。

**入参 / 出参草案**(待实装时定稿):

```python
def analyze_with_ai(self, frame_paths: list[str], model: str = "qwen-vl-max") -> list[dict]:
    # 每张帧 → {idx, description: str, shot_type: str, action: str, objects: list[str]}
    ...
```

### 10.2 语音转文字 — `transcribe_speech()`

**候选**

| 选项 | 端点 | 价格 | 速度 |
|---|---|---|---|
| **fal-ai/whisper** | `fal-ai/whisper` (queue.submit) | **~$0.50 / 1000 audio min**(≈ ¥0.0036 / 分钟) | 实测 ~105× 实时 |
| **fal-ai/wizper** | `fal-ai/wizper`(Whisper v3 fal 加速版) | 同档 | **~250× 实时**(2026 行业最快) |
| **OpenAI Whisper** | `/v1/audio/transcriptions` | $0.006 / 分钟(贵 12 倍) | ~30× 实时 |
| **本地 faster-whisper** | self-host | 0 | 视 GPU 而定,本机当前无 GPU,**不推荐** |

**推荐**:`fal-ai/wizper`。理由:fal_service 已有 submit/poll 抽象 + 钱包已绑;wizper 是 fal 加速版,1 分钟视频 < 1s 出文字。

**fal-ai/whisper 输入参数**(WebFetch 实测 schema):

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `audio_url` | str | ✅ | mp3/mp4/m4a/wav/webm |
| `task` | enum | | `"transcribe"` / `"translate"`(默认 transcribe) |
| `language` | enum | | 98 种语言码,null = 自动检测 |
| `diarize` | bool | | 说话人分离(默认 false) |
| `chunk_level` | enum | | `"none"` / `"segment"` / `"word"`(默认 segment) |
| `num_speakers` | int | | 分离时指定,null = 自动 |

**输出**:`{text, chunks[], inferred_languages[], diarization_segments[]}`

**入参草案**:

```python
def transcribe_speech(self, video_path: str, language: str = "zh", diarize: bool = False) -> dict:
    # 1. ffmpeg 抽音轨成 mp3
    # 2. 上传到 fal 临时存储拿 url
    # 3. 调 fal-ai/wizper
    # 4. 返回 {text, chunks: [{start, end, text}], language}
    ...
```

### 10.3 智能 grid_size 自适应

当前 grid_size 是用户传参。未来可以做 `auto`:

- < 4 个 scene → 2×2
- 5-6 → 2×3
- 7-9 → 3×3
- > 9 → 取 IoU 最低的 9 段(去重复镜头)

需要先实装 10.1 拿到帧描述,才能做语义去重。**建议跟 10.1 一起做。**

---

## 11. 红线 / 已知限制(给未来 Claude / 接入方)

- **不要在本 skill 里加 fal / openai 调用**:本 skill 应该是 **纯本地、无网络** 的工具,AI 调用应留给 caller 注入(否则没法离线测试 + 没法换模型)
- **不要把临时帧路径返给前端**:`/tmp/v3_frames/` 是 worker 私有,前端拿到也访问不了。grid_path 也是 tmp;要让前端展示,caller 必须自己 upload 到 OSS / fal 拿 URL
- **video_path 必须是本地路径**:URL / S3 路径不支持,caller 自己负责下载(`media_archiver` 有现成函数)
- **PySceneDetect 对极短视频(< 1s)结果不稳**:fallback 路径会触发,返回 1 个全片 scene,这是预期行为
- **ffmpeg `-ss` 前置是近似 seek**,精度 ±1 frame;对本 skill 取"代表帧"完全够,**不要换成精确 seek**(慢 10x+ 没收益)

---

## 12. 变更历史

| 日期 | 变更 |
|---|---|
| 2026-05-11 | skill 拆出独立模块,3 核心 + 1 stub + 19 tests,集成进 `app.skills` 命名空间 |
| 2026-05-12 | SKILL.md 文档化 + 三方调研(DeepSeek-VL / nano-banana / Whisper)|
