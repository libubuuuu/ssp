/**
 * 视频脚本 Markdown 序列化 / 解析(P180,2026-05-08)
 *
 * 设计原则:
 * - **共享 helper**:`/ad-video` 粘贴 + `/video/replicate` 复制 都用这一个 module
 * - 字段双向兼容:replicate 的 scene 形状 ↔ ad-video 的 scene 形状
 * - 解析器宽松:容错空格 / 中英文标点 / 大小写 / 换行差异
 *
 * Markdown 模板:
 * ```
 * # 视频脚本
 *
 * **总时长:** 15s
 * **整体场景:** 客厅,白天自然光
 * **模特描述:** 中国年轻女性,20-25 岁
 *
 * ## 镜 1 · 中景 · 0-5s
 * **动作:** 模特拿起产品展示
 * **画面:** medium shot, 自然光
 * **口播:** 大家好今天介绍...
 *
 * ## 镜 2 · 特写 · 5-10s
 * ...
 * ```
 */

/** 通用 scene 形状 — markdown 直接序列化的中间表示 */
export interface ScriptScene {
  id: number;
  time_range: string;       // "0-5s"
  duration_sec?: number;    // 派生
  shot: string;             // 中景 / 特写 / 全身 ...
  action: string;           // 动作描述
  visual_prompt: string;    // 完整画面 prompt
  speech: string;           // 口播文字(可空)
  // 兼容字段(用于不同 API 的转换)
  purpose?: string;
  framing?: string;
}

export interface ScriptDoc {
  total_duration_sec?: number;
  overall_setting: string;
  model_description: string;
  scenes: ScriptScene[];
}

// ============== Serialize ==============

/** 把 ScriptDoc 序列化成结构化 markdown(供"复制"/"下载"用) */
export function serializeToMarkdown(doc: ScriptDoc): string {
  const lines: string[] = ["# 视频脚本", ""];
  if (doc.total_duration_sec) lines.push(`**总时长:** ${doc.total_duration_sec}s`);
  if (doc.overall_setting) lines.push(`**整体场景:** ${doc.overall_setting}`);
  if (doc.model_description) lines.push(`**模特描述:** ${doc.model_description}`);
  lines.push("");
  for (const s of doc.scenes) {
    lines.push(`## 镜 ${s.id} · ${s.shot || "中景"} · ${s.time_range || "0-5s"}`);
    if (s.action) lines.push(`**动作:** ${s.action}`);
    if (s.visual_prompt) lines.push(`**画面:** ${s.visual_prompt}`);
    if (s.speech) lines.push(`**口播:** ${s.speech}`);
    lines.push("");
  }
  return lines.join("\n").trim() + "\n";
}

/** 把一段长口播按 scene duration 比例分到 N 段,适合 ASR 整段没时间戳的情况
 *  优先按"。!?,;"分句然后分配到段;不行就按字符数硬切。
 */
export function distributeSpeechToScenes(fullSpeech: string, scenes: { duration_sec?: number }[]): string[] {
  if (!fullSpeech.trim() || scenes.length === 0) return scenes.map(() => "");
  if (scenes.length === 1) return [fullSpeech.trim()];

  // 1. 按句号/问号/感叹号/逗号分句(中英文都覆盖)
  const sentences = fullSpeech.split(/(?<=[。!?\.\!\?,;,;])\s*/g).filter(s => s.trim());
  // 2. 计算每段目标字符数(按 duration 比例)
  const totalDur = scenes.reduce((a, s) => a + (s.duration_sec || 5), 0);
  const totalChars = fullSpeech.length;
  const targets = scenes.map(s => Math.round(((s.duration_sec || 5) / totalDur) * totalChars));

  // 3. 句子顺序分配到段,直到这段累计字符数 >= 目标
  const result: string[] = scenes.map(() => "");
  let sIdx = 0;
  for (const sent of sentences) {
    if (sIdx >= scenes.length) sIdx = scenes.length - 1;  // 兜底:剩余句子塞最后一段
    result[sIdx] += sent;
    if (result[sIdx].length >= targets[sIdx] && sIdx < scenes.length - 1) {
      sIdx++;
    }
  }
  // 4. 如果分句失败(整段无标点),按 target 字符数硬切
  if (result.every(r => !r.trim()) && fullSpeech.length > 0) {
    let pos = 0;
    for (let i = 0; i < scenes.length; i++) {
      const len = i === scenes.length - 1 ? fullSpeech.length - pos : targets[i];
      result[i] = fullSpeech.slice(pos, pos + len).trim();
      pos += len;
    }
  }
  return result.map(r => r.trim());
}

/** 从 replicate analyze 输出(scenes 数组)序列化(无 model_description) */
export function serializeReplicateScenes(scenes: any[], opts?: {
  total_duration_sec?: number;
  overall_setting?: string;
  original_speech?: string;  // 整体口播,如果有就按比例分配到各段
}): string {
  // P183:如果 scene 自己已有 speech 就用,没有但 opts 给了 original_speech 就分配
  const hasPerSceneSpeech = scenes.some(s => (s.speech || "").trim());
  const distributedSpeech = (!hasPerSceneSpeech && opts?.original_speech)
    ? distributeSpeechToScenes(opts.original_speech, scenes)
    : null;

  const docScenes: ScriptScene[] = scenes.map((s, i) => ({
    id: typeof s.id === "number" ? s.id : i + 1,
    time_range: s.time_range || `${i * 5}-${(i + 1) * 5}s`,
    duration_sec: s.duration_sec,
    shot: s.shot || "中景",
    action: s.action || "",
    visual_prompt: s.visual_prompt || "",
    speech: s.speech || (distributedSpeech ? distributedSpeech[i] : ""),
  }));
  return serializeToMarkdown({
    total_duration_sec: opts?.total_duration_sec,
    overall_setting: opts?.overall_setting || "",
    model_description: "",
    scenes: docScenes,
  });
}

// ============== Parse ==============

/** 字段名宽松匹配 — 中英文 / 大小写 / 全半角冒号都行 */
function normalizeFieldName(name: string): string {
  return name.trim().toLowerCase()
    .replace(/[：:\s]/g, "")
    .replace(/\s+/g, "");
}

const FIELD_ALIASES: Record<string, "total_duration" | "overall_setting" | "model_description" | "action" | "visual" | "speech"> = {
  "总时长": "total_duration", "总时长sec": "total_duration", "duration": "total_duration", "totalduration": "total_duration",
  "整体场景": "overall_setting", "场景": "overall_setting", "setting": "overall_setting", "overallsetting": "overall_setting",
  "模特描述": "model_description", "模特": "model_description", "model": "model_description", "modeldescription": "model_description",
  "动作": "action", "action": "action",
  "画面": "visual", "画面描述": "visual", "visual": "visual", "visualprompt": "visual", "prompt": "visual",
  "口播": "speech", "台词": "speech", "speech": "speech", "tts": "speech",
};

/** 解析 markdown → ScriptDoc。失败时 throw 带行号的 Error */
export function parseMarkdown(md: string): ScriptDoc {
  const text = (md || "").replace(/\r\n/g, "\n").trim();
  if (!text) throw new Error("脚本为空");

  const lines = text.split("\n");
  const doc: ScriptDoc = {
    total_duration_sec: undefined,
    overall_setting: "",
    model_description: "",
    scenes: [],
  };
  let currentScene: ScriptScene | null = null;
  let mode: "header" | "scene" = "header";

  // 匹配 ## 镜 N · 景别 · time_range  (支持半角 . 全角 · 都行)
  const sceneHeaderRe = /^##\s*(?:镜|场|scene|shot)\s*(\d+)\s*[·•・\-\s]+(.+?)\s*[·•・\-\s]+(.+?)\s*$/i;
  // 匹配 **字段:** value  (支持中英文冒号)
  const fieldRe = /^\*\*\s*([^:：*]+?)\s*[:：]\s*\*\*\s*(.+?)\s*$/;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    if (/^#\s*视频脚本/.test(line) || /^#\s*video script/i.test(line)) continue;

    const sceneMatch = line.match(sceneHeaderRe);
    if (sceneMatch) {
      if (currentScene) doc.scenes.push(currentScene);
      const [, idStr, shot, timeRange] = sceneMatch;
      currentScene = {
        id: parseInt(idStr, 10),
        time_range: timeRange.trim(),
        shot: shot.trim(),
        action: "",
        visual_prompt: "",
        speech: "",
      };
      // 派生 duration_sec
      const tm = currentScene.time_range.match(/(\d+(?:\.\d+)?)\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*s?/);
      if (tm) currentScene.duration_sec = parseFloat(tm[2]) - parseFloat(tm[1]);
      mode = "scene";
      continue;
    }

    const fieldMatch = line.match(fieldRe);
    if (fieldMatch) {
      const [, name, value] = fieldMatch;
      const key = FIELD_ALIASES[normalizeFieldName(name)];
      if (!key) continue;  // 未知字段静默忽略
      const v = value.trim();
      if (mode === "header" || !currentScene) {
        if (key === "total_duration") {
          const m = v.match(/(\d+(?:\.\d+)?)/);
          if (m) doc.total_duration_sec = parseFloat(m[1]);
        } else if (key === "overall_setting") {
          doc.overall_setting = doc.overall_setting ? doc.overall_setting + "\n" + v : v;
        } else if (key === "model_description") {
          doc.model_description = doc.model_description ? doc.model_description + "\n" + v : v;
        }
      } else {
        if (key === "action") currentScene.action = v;
        else if (key === "visual") currentScene.visual_prompt = v;
        else if (key === "speech") currentScene.speech = v;
      }
      continue;
    }
    // 没识别的行,如果在 header 模式下追加到 overall_setting(用户多行场景描述)
    if (mode === "header" && line.trim()) {
      doc.overall_setting = doc.overall_setting ? doc.overall_setting + "\n" + line : line;
    }
    // scene 模式下未识别的行,如果上一字段是 speech 就追加(支持口播多行)
    if (mode === "scene" && currentScene && line.trim() && !line.startsWith("#")) {
      // 多行口播追加 — 只有当 speech 已经有内容(说明上面就是 speech 字段)
      if (currentScene.speech) currentScene.speech += "\n" + line.trim();
    }
  }
  if (currentScene) doc.scenes.push(currentScene);

  if (doc.scenes.length === 0) throw new Error("没解析到任何镜头(`## 镜 1 · 景别 · time_range` 这种格式)");
  return doc;
}

// ============== Convert to ad-video Script shape ==============

/** ScriptDoc → ad-video API 要的 Script 格式(scenes 字段不同) */
export function toAdVideoScript(doc: ScriptDoc): {
  overall_setting: string;
  model_description: string;
  scenes: Array<{
    id: number;
    time_range: string;
    purpose: string;
    shot_language: string;
    content: string;
    visual_prompt: string;
    speech: string;
  }>;
} {
  return {
    overall_setting: doc.overall_setting,
    model_description: doc.model_description || "Professional commercial model",
    scenes: doc.scenes.map((s, i) => ({
      id: s.id || i + 1,
      time_range: s.time_range,
      purpose: s.action ? "showcase" : "intro",
      shot_language: s.shot,
      content: s.action,
      visual_prompt: s.visual_prompt || s.action,
      speech: s.speech,
    })),
  };
}

/** 简短 sample 模板,展示给用户看格式 */
export const MARKDOWN_TEMPLATE_SAMPLE = `# 视频脚本

**总时长:** 15s
**整体场景:** 室内客厅,白天自然光
**模特描述:** 中国年轻女性,25 岁,自然妆容

## 镜 1 · 中景 · 0-5s
**动作:** 模特拿起车载充电器,正面展示给镜头
**画面:** medium shot, 模特手持产品,自然光,客厅背景
**口播:** 各位老司机注意了,今天这个东西你的老车一定缺

## 镜 2 · 特写 · 5-10s
**动作:** 镜头推进,特写产品 USB 接口
**画面:** extreme close-up of USB ports, 产品质感清晰
**口播:** 蓝牙 5.0 + 双 USB 快充,79 块解决全部问题

## 镜 3 · 中景 · 10-15s
**动作:** 模特转向镜头,做出号召动作
**画面:** medium shot, 模特微笑指向产品
**口播:** 直播间专享,前 100 名加送车载收纳袋
`;
