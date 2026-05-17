"use client";
import { useState, useCallback, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

function getToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

// ─── 图片上传格子 ────────────────────────────────────────────────────────────
interface ImgSlot { url: string; preview: string; uploading: boolean; }
const emptySlot = (): ImgSlot => ({ url: "", preview: "", uploading: false });

function UploadBox({
  slot, label, required, onUpload, onRemove,
}: {
  slot: ImgSlot; label: string; required?: boolean;
  onUpload: (f: File) => void; onRemove: () => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, flex: "1 1 120px", maxWidth: 140 }}>
      <div style={{ fontSize: "0.72rem", color: required ? "#0d0d0d" : "#888", fontWeight: required ? 600 : 400 }}>
        {label}{required && <span style={{ color: "#e53e3e" }}>*</span>}
      </div>
      {slot.preview ? (
        <div style={{ position: "relative", width: "100%", paddingTop: "100%" }}>
          <img src={slot.preview} alt={label}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", borderRadius: 10, border: "1px solid #e2e8f0" }} />
          {slot.uploading && (
            <div style={{ position: "absolute", inset: 0, background: "rgba(255,255,255,0.7)", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 10, fontSize: "0.75rem", color: "#555" }}>上传中…</div>
          )}
          {!slot.uploading && (
            <button onClick={onRemove} style={{ position: "absolute", top: -8, right: -8, width: 22, height: 22, borderRadius: "50%", background: "#e53e3e", color: "#fff", border: "none", cursor: "pointer", fontSize: "0.8rem", display: "flex", alignItems: "center", justifyContent: "center", lineHeight: 1 }}>×</button>
          )}
        </div>
      ) : (
        <label style={{ width: "100%", paddingTop: "100%", position: "relative", border: `2px dashed ${required ? "#a0aec0" : "#e2e8f0"}`, borderRadius: 10, cursor: "pointer", display: "block", background: "#fafafa" }}>
          <input type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" style={{ display: "none" }}
            onChange={e => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = ""; }} />
          <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 4 }}>
            <span style={{ fontSize: "1.4rem", color: "#cbd5e0" }}>+</span>
            <span style={{ fontSize: "0.68rem", color: "#a0aec0" }}>上传图片</span>
          </div>
        </label>
      )}
    </div>
  );
}

// ─── 脚本格式化展示 ─────────────────────────────────────────────────────────
function ScriptDisplay({ text }: { text: string }) {
  // 预处理：修复模型输出格式不一致问题（多个镜头挤一行、字段不换行等）
  const normalized = text
    // 在 [镜头X] 前插入换行（前面没有换行时）
    .replace(/([^\n])\s*(\[镜头)/g, "$1\n$2")
    // 在常用字段标签前插入换行
    .replace(/([^\n])\s*(\[(?:目标语言|情节|模特|产品描述|环境|音乐|分镜)\][：:])/g, "$1\n$2")
    // 3个以上连续换行合并成2个
    .replace(/\n{3,}/g, "\n\n");

  // 把 [xxx]：yyy 解析成结构
  const lines = normalized.split("\n").map(l => l.trim()).filter(Boolean);
  const sections: Array<{ key: string; value: string; isShot: boolean }> = [];
  let shotAccum = "";

  for (const line of lines) {
    const m = line.match(/^[\[【](.+?)[\]】][：:]\s*(.*)$/);
    if (m) {
      if (shotAccum) { sections.push({ key: "shot", value: shotAccum, isShot: true }); shotAccum = ""; }
      const key = m[1].trim();
      const value = m[2].trim();
      if (/^镜头/.test(key)) {
        shotAccum = `[${key}]：${value}`;
      } else {
        sections.push({ key, value, isShot: false });
      }
    } else if (line !== "[分镜]：" && line !== "[分镜]:") {
      if (shotAccum) shotAccum += "\n" + line;
      else if (sections.length > 0) sections[sections.length - 1].value += "\n" + line;
    }
  }
  if (shotAccum) sections.push({ key: "shot", value: shotAccum, isShot: true });

  const META_STYLE: Record<string, { label: string; bg: string; color: string }> = {
    "目标语言": { label: "🌍 目标语言", bg: "#ebf8ff", color: "#2b6cb0" },
    "情节":     { label: "📖 情节概述", bg: "#faf5ff", color: "#6b46c1" },
    "模特":     { label: "🧑 模特设定", bg: "#fff5f5", color: "#c53030" },
    "产品描述": { label: "📦 产品描述", bg: "#f0fff4", color: "#276749" },
    "环境":     { label: "🏠 拍摄环境", bg: "#fffff0", color: "#744210" },
    "音乐":     { label: "🎵 背景音乐", bg: "#fff5f7", color: "#97266d" },
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {sections.map((s, i) => {
        if (s.isShot) {
          const shotMatch = s.value.match(/^\[([^\]]+)\]：([\s\S]+)/);
          const shotTitle = shotMatch ? shotMatch[1] : "镜头";
          const shotBody = shotMatch ? shotMatch[2] : s.value;
          // 解析台词
          const speechMatch = shotBody.match(/模特说[：:]([\s\S]+)/);
          const desc = speechMatch ? shotBody.slice(0, shotBody.indexOf(speechMatch[0])).trim() : shotBody;
          const speech = speechMatch ? speechMatch[1].trim() : "";
          return (
            <div key={i} style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, overflow: "hidden" }}>
              <div style={{ background: "#2d3748", color: "#fff", padding: "0.45rem 0.8rem", fontSize: "0.78rem", fontWeight: 600 }}>
                🎬 {shotTitle}
              </div>
              <div style={{ padding: "0.7rem 0.9rem" }}>
                <div style={{ fontSize: "0.82rem", color: "#4a5568", lineHeight: 1.6 }}>{desc}</div>
                {speech && (
                  <div style={{ marginTop: 8, background: "#f7fafc", borderLeft: "3px solid #4299e1", padding: "0.5rem 0.7rem", borderRadius: "0 6px 6px 0", fontSize: "0.82rem", color: "#2b6cb0", fontStyle: "italic", lineHeight: 1.6 }}>
                    💬 {speech}
                  </div>
                )}
              </div>
            </div>
          );
        }
        const meta = META_STYLE[s.key];
        return (
          <div key={i} style={{ background: meta?.bg || "#f7fafc", borderRadius: 8, padding: "0.55rem 0.85rem", display: "flex", gap: 10, alignItems: "flex-start" }}>
            <span style={{ fontSize: "0.75rem", fontWeight: 600, color: meta?.color || "#4a5568", whiteSpace: "nowrap", minWidth: 80 }}>
              {meta?.label || s.key}
            </span>
            <span style={{ fontSize: "0.82rem", color: "#1a202c", lineHeight: 1.6, flex: 1 }}>{s.value}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── 多角色对话流辅助组件 ────────────────────────────────────────────────────
const AGENT_CFG: Record<string, { char: string; bg: string; label: string; lc: string }> = {
  system:     { char: "AI",  bg: "#6366f1", label: "AI助手",         lc: "#6366f1" },
  xiaoli:     { char: "李",  bg: "#3b82f6", label: "小李·趋势研究员", lc: "#3b82f6" },
  linjiu:     { char: "久",  bg: "#7c3aed", label: "林久·创意导师",   lc: "#7c3aed" },
  reviewer:   { char: "审",  bg: "#16a34a", label: "审稿专家",        lc: "#16a34a" },
  copywriter: { char: "文",  bg: "#ea580c", label: "文案师",          lc: "#ea580c" },
};
function AgentRow({ sender, children }: { sender: string; children: React.ReactNode }) {
  const cfg = AGENT_CFG[sender] || AGENT_CFG.system;
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 18, alignItems: "flex-start" }}>
      <div style={{ flexShrink: 0, width: 34, height: 34, borderRadius: "50%", background: cfg.bg, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.78rem", fontWeight: 700, marginTop: 2 }}>{cfg.char}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: "0.67rem", color: cfg.lc, fontWeight: 700, marginBottom: 5, letterSpacing: "0.04em", textTransform: "uppercase" }}>{cfg.label}</div>
        {children}
      </div>
    </div>
  );
}
function UserRow({ content, images }: { content: string; images?: string[] }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 18, alignItems: "flex-start", flexDirection: "row-reverse" }}>
      <div style={{ flexShrink: 0, width: 34, height: 34, borderRadius: "50%", background: "#0d0d0d", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.78rem", fontWeight: 700, marginTop: 2 }}>我</div>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
        {images?.length ? (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
            {images.map((url, ii) => <img key={ii} src={url} alt="附图" style={{ width: 60, height: 60, objectFit: "cover", borderRadius: 8, border: "1px solid #444" }} />)}
          </div>
        ) : null}
        {content && <div style={{ maxWidth: "85%", padding: "0.6rem 0.9rem", borderRadius: 14, background: "#0d0d0d", color: "#fff", fontSize: "0.85rem", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{content}</div>}
      </div>
    </div>
  );
}
function FlowBubble({ children, style: s }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div style={{ padding: "0.65rem 0.9rem", background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, fontSize: "0.85rem", lineHeight: 1.6, color: "#1a202c", ...s }}>{children}</div>;
}
function FlowStepCard({ step, title, children }: { step: number; title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, overflow: "hidden" }}>
      <div style={{ padding: "0.65rem 1rem", borderBottom: "1px solid #f0f0f0", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: "0.63rem", fontWeight: 700, color: "#3b82f6", background: "#eff6ff", borderRadius: 5, padding: "0.15rem 0.45rem", letterSpacing: "0.06em" }}>STEP {step}</span>
        <span style={{ fontSize: "0.88rem", fontWeight: 600, color: "#1a202c" }}>{title}</span>
      </div>
      <div style={{ padding: "1rem" }}>{children}</div>
    </div>
  );
}
function FlowNextBtn({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{ marginTop: 14, padding: "0.55rem 1.2rem", borderRadius: 8, border: "none", background: disabled ? "#e2e8f0" : "#0d0d0d", color: disabled ? "#a0aec0" : "#fff", fontSize: "0.85rem", fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer" }}>
      {label} →
    </button>
  );
}

// ─── 主页面 ─────────────────────────────────────────────────────────────────
type Tab = "replicate" | "ai_video";
type ModelSrc = "auto" | "image" | "video";
const MARKETS = ["欧美", "东南亚", "日韩", "中国"];
const LANG_OPTIONS = (
  <>
    <option value="en">English（英语）</option>
    <option value="zh">中文</option>
    <option value="ja">日本語（日语）</option>
    <option value="ko">한국어（韩语）</option>
    <option value="es">Español（西班牙语）</option>
    <option value="pt">Português（葡萄牙语）</option>
    <option value="ar">العربية（阿拉伯语）</option>
    <option value="fr">Français（法语）</option>
    <option value="de">Deutsch（德语）</option>
    <option value="it">Italiano（意大利语）</option>
    <option value="th">ไทย（泰语）</option>
    <option value="vi">Tiếng Việt（越南语）</option>
    <option value="id">Bahasa Indonesia（印尼语）</option>
    <option value="ms">Bahasa Melayu（马来语）</option>
    <option value="tr">Türkçe（土耳其语）</option>
    <option value="ru">Русский（俄语）</option>
    <option value="pl">Polski（波兰语）</option>
    <option value="nl">Nederlands（荷兰语）</option>
    <option value="hi">हिन्दी（印地语）</option>
  </>
);

const REPLICATE_WHITELIST = ["lirunting1a@gmail.com"];

const TIKTOK_COUNTRIES = [
  { lang: "en", label: "🇺🇸 美国/英国", sub: "English" },
  { lang: "ja", label: "🇯🇵 日本", sub: "日本語" },
  { lang: "ko", label: "🇰🇷 韩国", sub: "한국어" },
  { lang: "th", label: "🇹🇭 泰国", sub: "ไทย" },
  { lang: "vi", label: "🇻🇳 越南", sub: "Tiếng Việt" },
  { lang: "id", label: "🇮🇩 印尼", sub: "Bahasa" },
  { lang: "es", label: "🌎 拉美", sub: "Español" },
  { lang: "ar", label: "🌙 中东", sub: "العربية" },
  { lang: "fr", label: "🇫🇷 法国", sub: "Français" },
  { lang: "de", label: "🇩🇪 德国", sub: "Deutsch" },
  { lang: "ru", label: "🇷🇺 俄罗斯", sub: "Русский" },
  { lang: "pt", label: "🇧🇷 巴西", sub: "Português" },
];

function _langToMarket(lang: string): string {
  if (lang === "ja" || lang === "ko") return "日韩";
  if (["th", "vi", "id", "ms"].includes(lang)) return "东南亚";
  if (lang === "zh") return "中国";
  return "欧美";
}

export default function VideoGeneralPage() {
  const [tab, setTab] = useState<Tab>("ai_video");

  // 灰度：视频复刻入口A 白名单判断
  const [isWhitelisted, setIsWhitelisted] = useState(false);
  const [flowStep, setFlowStep]           = useState(1);  // 1=step1 2=step2 3=step3 4=create
  const chatEndRef = useRef<HTMLDivElement>(null);
  const _lastAutoSceneDesc = useRef("");

  useEffect(() => {
    const tk = localStorage.getItem("token");
    if (!tk) return;
    fetch(`${API_BASE}/api/auth/me`, { headers: { Authorization: `Bearer ${tk}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.email) setIsWhitelisted(REPLICATE_WHITELIST.includes(d.email)); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (flowStep > 1) setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 200);
    setError("");
  }, [flowStep]);

  useEffect(() => {
    setError("");
  }, [tab]);

  // 入口A 专用：参考视频
  const [refVid, setRefVid] = useState<{ file: File | null; uploading: boolean; url: string }>({ file: null, uploading: false, url: "" });
  // 入口A 专用：脚本状态（与入口B script 独立）
  const [scriptA, setScriptA]       = useState("");
  const [loadingA, setLoadingA]     = useState(false);

  // Step 1 - product images
  const [front, setFront]   = useState<ImgSlot>(emptySlot());
  const [back,  setBack]    = useState<ImgSlot>(emptySlot());
  const [rear,  setRear]    = useState<ImgSlot>(emptySlot());
  const [scene, setScene]   = useState<ImgSlot>(emptySlot());

  // Step 2 - model source
  const [modelSrc, setModelSrc]     = useState<ModelSrc>("auto");
  const [modelImg, setModelImg]     = useState<ImgSlot>(emptySlot());
  const [modelVid, setModelVid]     = useState<{ file: File | null; uploading: boolean }>({ file: null, uploading: false });
  const [modelVidUrl, setModelVidUrl] = useState("");

  // Step 3 - params
  const [scriptMode, setScriptMode] = useState<"story" | "direct" | "chat">("story");
  const [platform, setPlatform]     = useState<"tiktok" | "douyin">("tiktok");
  const [duration, setDuration]     = useState(15);
  const [market, setMarket]         = useState("欧美");
  const [userIdea, setUserIdea]     = useState("");
  const [resolution, setResolution]       = useState("1080p");
  const [contrastImg, setContrastImg]     = useState<ImgSlot>(emptySlot());
  const [enableVoice, setEnableVoice]     = useState(true);
  const [targetLang, setTargetLang]       = useState("en");  // 目标语言代码

  // market → targetLang 联动（必须在 market 和 targetLang 声明之后）
  useEffect(() => {
    if (market === "中国大陆" || market === "中国") setTargetLang("zh");
  }, [market]);

  // Step 4 - script result
  const [loading, setLoading]   = useState(false);
  const [script, setScript]     = useState("");
  const [error, setError]       = useState("");

  // Chat mode state
  type ChatQuestion = { question: string; description: string; options: string[]; allow_custom: boolean };
  type ChatMsg = { role: "user" | "assistant"; content: string; images?: string[]; questions?: ChatQuestion[]; searchResult?: string; review?: ChatReview };
  const [chatMsgs, setChatMsgs]           = useState<ChatMsg[]>([]);
  const [chatInput, setChatInput]         = useState("");
  const [chatLoading, setChatLoading]     = useState(false);
  const [chatScript, setChatScript]       = useState("");
  const [chatNeedsContrast, setChatNeedsContrast] = useState(false);
  const [chatCustomInputs, setChatCustomInputs]   = useState<Record<number, string>>({});  // key=msgIdx*10+qi → customText
  const [chatSelections, setChatSelections]       = useState<Record<number, string>>({});  // key=msgIdx*10+qi → selected option
  const [chatPendingImages, setChatPendingImages] = useState<string[]>([]);  // 待发送图片URL列表
  const [chatImgUploading, setChatImgUploading]   = useState(false);
  const [chatShowParams, setChatShowParams]       = useState(false);  // 确认脚本后显示参数面板
  const [chatResolution, setChatResolution]       = useState("1080p");
  const [chatEnableVoice, setChatEnableVoice]     = useState(true);
  // 多角色 AI 附加数据
  type ChatReview = { score: number; details: string; suggestions: string };
  type ChatCopy   = { title: string; description: string; hashtags: string[]; best_time: string };
  const [chatCopy, setChatCopy]                 = useState<ChatCopy | null>(null);
  const [chatReviewExpanded, setChatReviewExpanded] = useState<Record<number, boolean>>({});
  const [chatCopyCopied, setChatCopyCopied]     = useState("");

  // 脚本后自动生成场景图
  const [sceneAutoDesc, setSceneAutoDesc]         = useState("");
  const [sceneAutoPreview, setSceneAutoPreview]   = useState("");   // 待确认的预览URL
  const [sceneAutoLoading, setSceneAutoLoading]   = useState(false);
  const [sceneAutoConfirmed, setSceneAutoConfirmed] = useState(false);
  const [sceneAutoEditMode, setSceneAutoEditMode] = useState(false);
  const [sceneAutoEditInput, setSceneAutoEditInput] = useState("");

  // ── 脚本生成后自动触发场景图生成（必须在所有 state 之后定义，避免 TDZ）──

  // Step 5 - video generation
  const [vidLoading, setVidLoading] = useState(false);
  const [vidProgress, setVidProgress] = useState("");
  const [vidUrl, setVidUrl]         = useState("");
  const [vidCost, setVidCost]       = useState(0);

  // ── upload helpers ─────────────────────────────────────────────────────────
  const uploadImg = useCallback(async (
    file: File,
    endpoint: string,
    resultKey: string,
    setter: (fn: (s: ImgSlot) => ImgSlot) => void,
  ) => {
    if (file.size > 10 * 1024 * 1024) { setError("图片不能超过 10MB"); return; }
    const preview = URL.createObjectURL(file);
    setter(s => ({ ...s, preview, uploading: true }));
    setError("");
    try {
      const fd = new FormData(); fd.append("file", file);
      const r = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: fd,
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setter(s => ({ ...s, url: d[resultKey] || "", uploading: false }));
    } catch (e) {
      setter(s => ({ ...s, preview: "", url: "", uploading: false }));
      setError((e as Error).message || "上传失败");
    }
  }, []);

  const removeSlot = (setter: (s: ImgSlot) => void) => setter(emptySlot());

  // ── generate script ────────────────────────────────────────────────────────
  const generate = async () => {
    if (!front.url) { setError("请先上传正面产品图"); return; }
    setError(""); setScript(""); setLoading(true);
    adjustLocalUserCredits(-35);
    try {
      const modelInfo = modelSrc === "auto"
        ? (market === "中国" ? "AI 自动生成亚洲模特" : "AI 自动生成欧美模特")
        : modelSrc === "image" ? "用户上传模特图" : "用户上传模特视频";

      const r = await fetch(`${API_BASE}/api/video/general/script`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({
          image_url: front.url,
          market,
          duration,
          model_info: modelInfo,
          user_idea: userIdea.trim(),
          mode: scriptMode,
        }),
      });
      if (!r.ok) {
        adjustLocalUserCredits(35); // 退还（后端也退了，前端同步）
        throw new Error((await r.json()).detail || await r.text());
      }
      const d = await r.json();
      setScript(d.script || "");
    } catch (e) {
      setError((e as Error).message || "脚本生成失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  // ── 入口A：分析参考视频 ────────────────────────────────────────────────────
  const analyzeVideo = async () => {
    if (!refVid.url)  { setError("请先上传参考视频"); return; }
    if (!front.url)   { setError("请先上传正面产品图"); return; }
    setError(""); setScriptA(""); setLoadingA(true);
    adjustLocalUserCredits(-35);
    const modelInfo = modelSrc === "auto"
      ? (market === "中国" ? "AI 自动生成亚洲模特" : "AI 自动生成欧美模特")
      : modelSrc === "image" ? "用户上传模特图" : "用户上传模特视频";
    const productUrls = [front.url, back.url, rear.url].filter(Boolean);
    try {
      const r = await fetch(`${API_BASE}/api/video/general/video-analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({
          video_url: refVid.url,
          product_image_urls: productUrls,
          market, duration,
          model_info: modelInfo,
          user_idea: userIdea.trim(),
        }),
      });
      if (!r.ok) {
        adjustLocalUserCredits(35);
        throw new Error((await r.json()).detail || await r.text());
      }
      const d = await r.json();
      setScriptA(d.script || "");
    } catch (e) {
      setError((e as Error).message || "视频分析失败，请重试");
    } finally {
      setLoadingA(false);
    }
  };

  // ── AI导师对话发送 ─────────────────────────────────────────────────────────
  const sendChatMessage = useCallback(async (msgText?: string, msgImages?: string[]) => {
    const text = (msgText ?? chatInput).trim();
    // 从用户回答提取时长并同步 duration state
    if (text.includes("10秒") || text.includes("10 秒")) setDuration(10);
    else if (text.includes("15秒") || text.includes("15 秒")) setDuration(15);
    else if (text.includes("30秒") || text.includes("30 秒")) setDuration(30);
    // 从用户回答提取目标语言
    const _langMap: Record<string, string> = {
      "英语": "en", "english": "en", "美国": "en", "英国": "en", "东南亚": "en",
      "日语": "ja", "日本": "ja", "japanese": "ja",
      "韩语": "ko", "韩国": "ko", "korean": "ko",
      "西班牙语": "es", "拉美": "es", "spanish": "es",
      "葡萄牙语": "pt", "巴西": "pt", "portuguese": "pt",
      "阿拉伯语": "ar", "中东": "ar", "arabic": "ar",
      "中文": "zh", "中国": "zh", "抖音": "zh",
    };
    const _tl = text.toLowerCase();
    for (const [kw, lang] of Object.entries(_langMap)) {
      if (_tl.includes(kw.toLowerCase())) { setTargetLang(lang); break; }
    }
    const images = msgImages ?? (chatPendingImages.length ? [...chatPendingImages] : undefined);
    if (!text && !images?.length) return;
    const newMsg: ChatMsg = { role: "user", content: text, images };
    const updatedMsgs = [...chatMsgs, newMsg];
    setChatPendingImages([]);  // 清空待发图片
    setChatMsgs(updatedMsgs);
    setChatInput("");
    setChatLoading(true);
    setError("");
    try {
      const r = await fetch(`${API_BASE}/api/video/general/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({
          messages: updatedMsgs,
          product_image_urls: [front.url, back.url, rear.url].filter(Boolean),
          market, duration, target_lang: targetLang, platform,
          video_url: null,
        }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || await r.text());
      const d = await r.json();
      const assistantMsg: ChatMsg = {
        role: "assistant",
        content: d.reply || "",
        questions: d.questions?.length ? d.questions : undefined,
        searchResult: d.search_result || undefined,
        review: d.review || undefined,
      };
      setChatMsgs(prev => [...prev, assistantMsg]);
      if (d.script) {
        setChatScript(d.script);
        setChatCopy(null);
        // 解析脚本总时长
        const _times: number[] = [];
        for (const _m of d.script.matchAll(/(\d+)-(\d+)s/g)) _times.push(parseInt(_m[2]));
        const _parsed = _times.length > 0 ? Math.max(..._times) : 0;
        if (_parsed > 0) setDuration(_parsed);
        // 直接触发场景图生成（不用 useEffect，避免闭包问题）
        if (!scene.url) {
          const _envMatch = d.script.match(/\[环境\][：:]\s*(.+?)(?:\n|\[|$)/s);
          const _envDesc = (_envMatch ? _envMatch[1].trim() : "") || "Modern clean indoor room, natural soft lighting, minimalist decor";
          _lastAutoSceneDesc.current = _envDesc;
          setSceneAutoDesc(_envDesc); setSceneAutoEditInput(_envDesc);
          setSceneAutoLoading(true); setSceneAutoPreview(""); setSceneAutoConfirmed(false); setSceneAutoEditMode(false);
          fetch(`${API_BASE}/api/video/general/generate-scene`, {
            method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
            body: JSON.stringify({ description: _envDesc, orientation: "portrait" }),
          }).then(r => r.ok ? r.json() : null)
            .then(d2 => { if (d2?.scene_image_url) setSceneAutoPreview(d2.scene_image_url); })
            .catch(() => {})
            .finally(() => setSceneAutoLoading(false));
        }
      }
      if (d.need_contrast_image) setChatNeedsContrast(true);
      if (d.copy) setChatCopy(d.copy);
      setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
    } catch (e) {
      setError((e as Error).message || "对话失败，请重试");
    } finally {
      setChatLoading(false);
    }
  }, [chatInput, chatMsgs, chatPendingImages, front.url, back.url, rear.url, market, duration, scene.url, targetLang]);

  // ── 检测脚本里是否含对比关键词（决定是否显示对比图上传）──────────────────
  const _CONTRAST_KW = ["旧", "老款", "之前", "原来", "以前", "对比", "竞品", "old", "previous", "used to", "regular", "normal", "换上", "换了", "before", "instead"];
  const hasContrastHint = (text: string) => _CONTRAST_KW.some(kw => text.toLowerCase().includes(kw.toLowerCase()));

  // ── 对比图上传区 JSX（脚本展示后、生成按钮前复用）──────────────────────────
  const ContrastUploadHint = ({ scriptText }: { scriptText: string }) => {
    if (!hasContrastHint(scriptText)) return null;
    return (
      <div style={{ margin: "1rem 0", padding: "0.9rem 1rem", background: "#fefce8", border: "1px solid #fde68a", borderRadius: 10 }}>
        <div style={{ fontSize: "0.82rem", fontWeight: 600, color: "#92400e", marginBottom: 6 }}>💡 脚本中有产品对比环节</div>
        <div style={{ fontSize: "0.75rem", color: "#78350f", marginBottom: 10 }}>
          上传一张旧款/竞品图片，视频中的对比效果更真实。不上传也可以，AI 会自动处理。
        </div>
        <UploadBox slot={contrastImg} label="对比图"
          onUpload={f => uploadImg(f, "/api/video/general/upload/image", "image_url", setContrastImg)}
          onRemove={() => removeSlot(setContrastImg)} />
      </div>
    );
  };

  // ── generate video from confirmed script ──────────────────────────────────
  const generateVideo = async (scriptOverride?: string, refVideoOverride?: string) => {
    const useScript = scriptOverride ?? script;
    if (!front.url)   { setError("请先上传正面产品图"); return; }
    if (!useScript)   { setError("请先生成脚本"); return; }
    setError(""); setVidUrl(""); setVidLoading(true); setVidProgress("提交视频生成任务…");

    const productUrls = [front.url, back.url, rear.url].filter(Boolean);

    try {
      const r = await fetch(`${API_BASE}/api/video/general/script-to-video`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({
          script: useScript,
          product_image_urls: productUrls,
          scene_image_url:  scene.url  || null,
          model_image_url:  modelSrc === "image" ? modelImg.url || null : null,
          model_video_url:  modelSrc === "video" ? modelVidUrl || null : null,
          model_source:     modelSrc,
          aspect_ratio:     "9:16",
          ref_video_url:    refVideoOverride || null,
          resolution:           resolution,
          contrast_image_url:   contrastImg.url || null,
          enable_voice:         enableVoice,
          target_duration:      duration,
          target_lang:          targetLang,
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || await r.text());
      }
      const d = await r.json();
      const jid: string = d.job_id;
      const cost: number = d.cost ?? 0;
      setVidCost(cost);
      adjustLocalUserCredits(-cost);
      const _estTime = duration >= 30 ? "4-8 分钟" : "2-4 分钟";
      setVidProgress(`Seedance 2.0 生成中，约 ${_estTime}（job=${jid}）…`);

      // poll
      let elapsed = 0;
      const iv = setInterval(async () => {
        elapsed += 10;
        try {
          const sr = await fetch(`${API_BASE}/api/jobs/${jid}`, {
            headers: { Authorization: `Bearer ${getToken()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(iv);
            // 兼容多种字段路径：result.video_url（直接）或 result 本身就是 URL
            const _vidUrl = sd.result?.video_url || sd.result?.url || (typeof sd.result === "string" ? sd.result : "") || "";
            console.log("[script-to-video] job result:", JSON.stringify(sd.result));
            setVidUrl(_vidUrl);
            setVidLoading(false); setVidProgress("");
          } else if (sd.status === "failed") {
            clearInterval(iv);
            adjustLocalUserCredits(cost);  // 退还（后端也退了，前端同步）
            const rawErr: string = sd.error || "";
            const friendlyErr = rawErr.includes("超时") || rawErr.includes("timeout") || rawErr.includes("Timeout")
              ? "视频生成超时，请稍后重试。积分已退还。"
              : rawErr || "视频生成失败，积分已退还。";
            setError(friendlyErr);
            setVidLoading(false); setVidProgress("");
          } else {
            const mm = Math.floor(elapsed / 60), ss = elapsed % 60;
            setVidProgress(`Seedance 2.0 生成中… 已 ${mm}:${String(ss).padStart(2,"0")}（job=${jid}）`);
          }
        } catch {}
      }, 10000);
    } catch (e) {
      setError((e as Error).message || "视频生成失败，请重试");
      setVidLoading(false); setVidProgress("");
    }
  };

  // ── tabs ───────────────────────────────────────────────────────────────────
  const TAB_STYLE = (active: boolean): React.CSSProperties => ({
    padding: "0.55rem 1.4rem",
    border: "none",
    borderBottom: active ? "2px solid #0d0d0d" : "2px solid transparent",
    background: "none",
    color: active ? "#0d0d0d" : "#888",
    fontWeight: active ? 600 : 400,
    fontSize: "0.9rem",
    cursor: "pointer",
    transition: "all 0.15s",
  });

  const CARD: React.CSSProperties = {
    background: "#fff",
    borderRadius: 16,
    padding: "1.4rem 1.6rem",
    marginBottom: "1.2rem",
    boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
    border: "1px solid rgba(0,0,0,0.05)",
  };

  const STEP_LABEL: React.CSSProperties = {
    fontSize: "0.7rem",
    fontWeight: 700,
    color: "#0d0d0d",
    background: "#f0ede6",
    borderRadius: 6,
    padding: "0.15rem 0.5rem",
    marginRight: 8,
    letterSpacing: "0.05em",
  };

  // ── 场景确认卡片（脚本生成后自动触发，chat和非chat模式共用）────────────
  const SceneConfirmCard = () => {
    if (!sceneAutoDesc) return null;
    if (sceneAutoConfirmed && scene.url) return (
      <AgentRow sender="system">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <FlowBubble style={{ background: "#f0fdf4", border: "1px solid #86efac", color: "#166534" }}>
            ✅ 场景已确认，视频将在此环境中拍摄
          </FlowBubble>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <img src={scene.url} alt="已确认场景" style={{ width: 64, height: 64, objectFit: "cover", borderRadius: 8, border: "1px solid #86efac" }} />
            <button onClick={() => { removeSlot(setScene); setSceneAutoConfirmed(false); setSceneAutoPreview(""); _lastAutoSceneDesc.current = ""; }}
              style={{ padding: "0.3rem 0.7rem", borderRadius: 6, border: "1px solid #e2e8f0", background: "#fff", color: "#6b7280", fontSize: "0.75rem", cursor: "pointer" }}>
              更换场景
            </button>
          </div>
        </div>
      </AgentRow>
    );
    return (
      <AgentRow sender="system">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {sceneAutoLoading ? (
            <FlowBubble style={{ color: "#6b7280", display: "inline-flex", alignItems: "center", gap: 8 }}>
              <span style={{ display: "inline-block", width: 14, height: 14, border: "2px solid #d1d5db", borderTopColor: "#6366f1", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
              根据脚本[环境]字段，正在生成场景参考图…
            </FlowBubble>
          ) : sceneAutoPreview ? (
            <>
              <FlowBubble>根据脚本，我为你生成了场景参考图：</FlowBubble>
              <img src={sceneAutoPreview} alt="场景预览" style={{ width: "100%", maxWidth: 200, borderRadius: 10, border: "2px solid #e2e8f0" }} />
              <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>场景描述：{sceneAutoDesc}</div>
              {!sceneAutoEditMode ? (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button onClick={() => { setScene({ url: sceneAutoPreview, preview: sceneAutoPreview, uploading: false }); setSceneAutoConfirmed(true); }}
                    style={{ padding: "0.4rem 0.9rem", borderRadius: 7, border: "none", background: "#16a34a", color: "#fff", fontSize: "0.8rem", fontWeight: 600, cursor: "pointer" }}>✓ 使用这个场景</button>
                  <button onClick={() => setSceneAutoEditMode(true)}
                    style={{ padding: "0.4rem 0.9rem", borderRadius: 7, border: "1px solid #e2e8f0", background: "#fff", color: "#374151", fontSize: "0.8rem", cursor: "pointer" }}>✏️ 修改场景</button>
                  <label style={{ padding: "0.4rem 0.9rem", borderRadius: 7, border: "1px solid #e2e8f0", background: "#fff", color: "#374151", fontSize: "0.8rem", cursor: "pointer" }}>
                    <input type="file" accept="image/*" style={{ display: "none" }} onChange={async e => {
                      const f = e.target.files?.[0]; if (!f) return;
                      await uploadImg(f, "/api/video/general/upload/scene-image", "scene_image_url", setScene);
                      setSceneAutoConfirmed(true); e.target.value = "";
                    }} />
                    📎 自己上传
                  </label>
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <textarea value={sceneAutoEditInput} onChange={e => setSceneAutoEditInput(e.target.value)} rows={2}
                    placeholder="修改场景描述…"
                    style={{ width: "100%", padding: "0.4rem 0.6rem", border: "1px solid #e2e8f0", borderRadius: 6, fontSize: "0.8rem", resize: "none", fontFamily: "inherit", boxSizing: "border-box" }} />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={async () => {
                      const desc = sceneAutoEditInput.trim(); if (!desc) return;
                      _lastAutoSceneDesc.current = desc;
                      setSceneAutoLoading(true); setSceneAutoPreview(""); setSceneAutoEditMode(false); setSceneAutoDesc(desc);
                      try {
                        const r = await fetch(`${API_BASE}/api/video/general/generate-scene`, {
                          method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
                          body: JSON.stringify({ description: desc, orientation: "portrait" }),
                        });
                        const d = await r.json(); setSceneAutoPreview(d.scene_image_url || "");
                      } catch (ex) { setError((ex as Error).message || "生成失败"); }
                      finally { setSceneAutoLoading(false); }
                    }} style={{ flex: 1, padding: "0.4rem 0.7rem", borderRadius: 7, border: "none", background: "#7c3aed", color: "#fff", fontSize: "0.8rem", fontWeight: 600, cursor: "pointer" }}>重新生成</button>
                    <button onClick={() => setSceneAutoEditMode(false)}
                      style={{ padding: "0.4rem 0.7rem", borderRadius: 7, border: "1px solid #e2e8f0", background: "#fff", color: "#6b7280", fontSize: "0.8rem", cursor: "pointer" }}>取消</button>
                  </div>
                </div>
              )}
            </>
          ) : null}
        </div>
      </AgentRow>
    );
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, overflowY: "auto", maxWidth: 760, width: "100%", margin: "0 auto", padding: "0 1.5rem 3rem" }}>

        {/* ── 页面标题 ── */}
        <div style={{ padding: "1.8rem 0 0.5rem" }}>
          <div style={{ fontSize: "0.8rem", color: "#999", marginBottom: 4 }}>AI 创作工具</div>
          <h1 style={{ margin: 0, fontSize: "1.5rem", fontWeight: 500, color: "#0d0d0d", fontFamily: "Georgia,serif" }}>
            图片<span style={{ fontStyle: "italic" }}>复刻</span>
          </h1>
        </div>

        {/* ── 标签页 ── */}
        <div style={{ display: "flex", borderBottom: "1px solid #e2e8f0", marginBottom: "1.4rem" }}>
          <button style={TAB_STYLE(tab === "replicate")} onClick={() => setTab("replicate")}>视频复刻</button>
          <button style={TAB_STYLE(tab === "ai_video")}  onClick={() => setTab("ai_video")}>AI 爆款视频</button>
        </div>

        {/* ── 标签A：视频复刻 ── */}
        {tab === "replicate" && !isWhitelisted && (
          <div style={{ ...CARD, textAlign: "center", padding: "3rem 2rem", color: "#888" }}>
            <div style={{ fontSize: "2rem", marginBottom: 12 }}>🎬</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 500, color: "#555", marginBottom: 8 }}>视频复刻功能</div>
            <div style={{ fontSize: "0.88rem" }}>功能开发中，敬请期待</div>
          </div>
        )}

        {tab === "replicate" && isWhitelisted && (
          <>
            {error && (
              <div style={{ background: "#fff5f5", border: "1px solid #fed7d7", color: "#c53030", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.88rem" }}>{error}</div>
            )}

            {/* Step 1A：上传参考视频 */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 1</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>上传参考视频</span>
              </div>
              <label style={{ display: "flex", alignItems: "center", gap: 12, border: `2px dashed ${refVid.url ? "#16a34a" : "#e2e8f0"}`, borderRadius: 12, padding: "1rem 1.2rem", cursor: "pointer", background: refVid.url ? "#f0fdf4" : "#fafafa", transition: "all 0.15s" }}>
                <input type="file" accept="video/mp4,video/quicktime,video/webm" style={{ display: "none" }}
                  onChange={async e => {
                    const f = e.target.files?.[0]; if (!f) return;
                    if (f.size > 100 * 1024 * 1024) { setError("视频不能超过 100MB"); return; }
                    setRefVid({ file: f, uploading: true, url: "" }); setError("");
                    try {
                      const fd = new FormData(); fd.append("file", f);
                      const r = await fetch(`${API_BASE}/api/video/general/upload/video`, {
                        method: "POST", headers: { Authorization: `Bearer ${getToken()}` }, body: fd,
                      });
                      if (!r.ok) throw new Error(await r.text());
                      const d = await r.json();
                      setRefVid({ file: f, uploading: false, url: d.video_url || "" });
                    } catch (err) {
                      setError((err as Error).message || "视频上传失败");
                      setRefVid({ file: null, uploading: false, url: "" });
                    }
                    e.target.value = "";
                  }} />
                <span style={{ fontSize: "1.4rem" }}>{refVid.uploading ? "⏳" : refVid.url ? "✅" : "🎬"}</span>
                <div>
                  <div style={{ fontSize: "0.9rem", fontWeight: 500, color: "#1a202c" }}>
                    {refVid.uploading ? "上传中…" : refVid.url ? `已上传：${refVid.file?.name}` : "点击上传参考视频"}
                  </div>
                  <div style={{ fontSize: "0.72rem", color: "#a0aec0", marginTop: 3 }}>MP4 / MOV / WebM · ≤ 100MB</div>
                </div>
              </label>
            </div>

            {/* Step 2A：产品图（复用入口B组件） */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 2</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>上传产品白底图</span>
              </div>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
                <UploadBox slot={front} label="正面图" required
                  onUpload={f => uploadImg(f, "/api/video/general/upload/image", "image_url", setFront)}
                  onRemove={() => removeSlot(setFront)} />
                <UploadBox slot={back}  label="反面图"
                  onUpload={f => uploadImg(f, "/api/video/general/upload/image", "image_url", setBack)}
                  onRemove={() => removeSlot(setBack)} />
                <UploadBox slot={rear}  label="侧面图"
                  onUpload={f => uploadImg(f, "/api/video/general/upload/image", "image_url", setRear)}
                  onRemove={() => removeSlot(setRear)} />
                <UploadBox slot={scene} label="场景图"
                  onUpload={f => uploadImg(f, "/api/video/general/upload/scene-image", "scene_image_url", setScene)}
                  onRemove={() => removeSlot(setScene)} />
              </div>
              <div style={{ fontSize: "0.72rem", color: "#a0aec0", marginTop: 10 }}>正面图必传，其余可选 · 每张 ≤ 10MB</div>
            </div>

            {/* Step 3A：模特来源（复用） */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 3</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>模特来源</span>
              </div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
                {([
                  { v: "auto",  label: "AI 自动出模特",  desc: "GPT-Image 2 生成，按市场自动匹配" },
                  { v: "image", label: "上传模特图",      desc: "上传真人照片，复刻模特形象" },
                  { v: "video", label: "上传模特视频",    desc: "上传视频，自动提取中间帧" },
                ] as { v: ModelSrc; label: string; desc: string }[]).map(o => (
                  <label key={o.v} onClick={() => setModelSrc(o.v)}
                    style={{ flex: "1 1 160px", minWidth: 140, border: `2px solid ${modelSrc === o.v ? "#0d0d0d" : "#e2e8f0"}`, borderRadius: 12, padding: "0.85rem", cursor: "pointer", background: modelSrc === o.v ? "#f7f7f5" : "#fff", transition: "all 0.15s" }}>
                    <input type="radio" name="modelSrcA" value={o.v} checked={modelSrc === o.v} onChange={() => setModelSrc(o.v)} style={{ marginRight: 7 }} />
                    <strong style={{ fontSize: "0.88rem" }}>{o.label}</strong>
                    <div style={{ fontSize: "0.73rem", color: "#718096", marginTop: 4, lineHeight: 1.4 }}>{o.desc}</div>
                  </label>
                ))}
              </div>
            </div>

            {/* Step 4A：视频参数（复用） */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 4</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>视频参数</span>
              </div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                <div>
                  <div style={{ fontSize: "0.75rem", color: "#718096", marginBottom: 5 }}>总时长</div>
                  <select value={duration} onChange={e => setDuration(+e.target.value)}
                    style={{ padding: "0.5rem 0.7rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.88rem", background: "#fff", color: "#1a202c", cursor: "pointer" }}>
                    {[10, 15, 30].map(v => <option key={v} value={v}>{v} 秒</option>)}
                  </select>
                </div>
                <div>
                  <div style={{ fontSize: "0.75rem", color: "#718096", marginBottom: 5 }}>目标市场</div>
                  <select value={market} onChange={e => setMarket(e.target.value)}
                    style={{ padding: "0.5rem 0.7rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.88rem", background: "#fff", color: "#1a202c", cursor: "pointer" }}>
                    {MARKETS.map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div>
                  <div style={{ fontSize: "0.75rem", color: "#718096", marginBottom: 5 }}>分辨率</div>
                  <select value={resolution} onChange={e => setResolution(e.target.value)}
                    style={{ padding: "0.5rem 0.7rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.88rem", background: "#fff", color: "#1a202c", cursor: "pointer" }}>
                    <option value="1080p">1080P（高清）</option>
                    <option value="2k">2K（超清）+20积分</option>
                    <option value="4k">4K（极清）+50积分</option>
                  </select>
                </div>
              </div>

              {/* 声音开关（入口A） */}
              <div style={{ marginTop: 14 }}>
                <div style={{ fontSize: "0.75rem", color: "#718096", marginBottom: 8 }}>声音</div>
                <div style={{ display: "flex", gap: 8 }}>
                  {([{ v: true, icon: "🔊", label: "有声音", desc: "TTS 配音 + 对口型" }, { v: false, icon: "🔇", label: "无声音", desc: "纯视觉，无配音" }] as { v: boolean; icon: string; label: string; desc: string }[]).map(o => (
                    <button key={String(o.v)} onClick={() => setEnableVoice(o.v)}
                      style={{ flex: "1 1 120px", padding: "0.6rem 0.8rem", border: `2px solid ${enableVoice === o.v ? "#0d0d0d" : "#e2e8f0"}`, borderRadius: 10, background: enableVoice === o.v ? "#0d0d0d" : "#fff", color: enableVoice === o.v ? "#fff" : "#4a5568", cursor: "pointer", textAlign: "left", transition: "all 0.15s" }}>
                      <div style={{ fontSize: "0.85rem", fontWeight: 600, marginBottom: 2 }}>{o.icon} {o.label}</div>
                      <div style={{ fontSize: "0.68rem", opacity: 0.7 }}>{o.desc}</div>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Step 5A：分析按钮 + 脚本 + 生成视频 */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 5</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>分析视频并生成脚本</span>
              </div>

              <button onClick={analyzeVideo} disabled={loadingA || vidLoading || !refVid.url || !front.url}
                style={{
                  width: "100%", padding: "0.9rem", borderRadius: 10, border: "none", fontSize: "0.95rem", fontWeight: 600,
                  background: loadingA || !refVid.url || !front.url ? "#e2e8f0" : "#7c3aed",
                  color: loadingA || !refVid.url || !front.url ? "#a0aec0" : "#fff",
                  cursor: loadingA || !refVid.url || !front.url ? "not-allowed" : "pointer",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                }}>
                {loadingA ? (
                  <><span style={{ display: "inline-block", width: 16, height: 16, border: "2px solid #a0aec0", borderTopColor: "#fff", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />AI 正在分析视频…</>
                ) : scriptA ? "重新分析视频（35 积分）" : "分析视频并生成脚本（35 积分）"}
              </button>
              {(!refVid.url || !front.url) && !loadingA && (
                <div style={{ fontSize: "0.75rem", color: "#a0aec0", textAlign: "center", marginTop: 6 }}>
                  {!refVid.url ? "请先上传参考视频" : "请先上传正面产品图"}
                </div>
              )}

              {scriptA && !loadingA && (
                <div style={{ marginTop: "1.4rem" }}>
                  <div style={{ fontSize: "0.8rem", color: "#718096", fontWeight: 600, marginBottom: 10 }}>✨ 视频分析结果（可复刻脚本）</div>
                  <ScriptDisplay text={scriptA} />
                  <ContrastUploadHint scriptText={scriptA} />
                  <div style={{ display: "flex", gap: 10, marginTop: "1.2rem", flexWrap: "wrap" }}>
                    <button onClick={analyzeVideo} disabled={loadingA || vidLoading}
                      style={{ flex: "1 1 200px", padding: "0.7rem 1rem", borderRadius: 10, border: "1px solid #e2e8f0", background: "#fff", color: "#374151", fontSize: "0.88rem", cursor: "pointer", fontWeight: 500 }}>
                      🔄 重新分析（35 积分）
                    </button>
                    <button onClick={() => generateVideo(scriptA, refVid.url || undefined)} disabled={vidLoading || loadingA}
                      style={{
                        flex: "1 1 200px", padding: "0.7rem 1rem", borderRadius: 10, border: "none",
                        background: vidLoading || loadingA ? "#e2e8f0" : "#16a34a",
                        color: vidLoading || loadingA ? "#a0aec0" : "#fff",
                        fontSize: "0.88rem", cursor: vidLoading || loadingA ? "not-allowed" : "pointer",
                        fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                      }}>
                      {vidLoading ? <><span style={{ display: "inline-block", width: 14, height: 14, border: "2px solid #a0aec0", borderTopColor: "#fff", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />生成中…</> : "🎬 确认脚本，生成视频"}
                    </button>
                  </div>
                  {vidLoading && (
                    <div style={{ marginTop: 12, background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 8, padding: "0.65rem 0.9rem", fontSize: "0.8rem", color: "#1e40af", marginBottom: 6 }}>
                      ✅ 任务已提交，预计{duration >= 30 ? "4-8" : "2-4"}分钟完成。你可以关闭此页面去做其他事，生成完成后在右上角「我的任务」查看结果。
                    </div>
                  )}
                  {vidLoading && vidProgress && (
                    <div style={{ padding: "0.7rem 0.9rem", background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, fontSize: "0.82rem", color: "#166534" }}>⏳ {vidProgress}</div>
                  )}
                  {vidUrl && !vidLoading && (
                    <div style={{ marginTop: "1.2rem" }}>
                      <div style={{ fontSize: "0.8rem", color: "#718096", fontWeight: 600, marginBottom: 10 }}>🎬 生成完成</div>
                      <video src={vidUrl} controls style={{ width: "100%", maxWidth: 400, borderRadius: 10, display: "block" }} />
                      <a href={vidUrl} download style={{ display: "inline-block", marginTop: 8, fontSize: "0.85rem", color: "#16a34a", textDecoration: "none", fontWeight: 500 }}>⬇ 下载视频</a>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}

        {/* ── 标签B：AI 爆款视频（对话流） ── */}
        {tab === "ai_video" && (
          <div style={{ position: "relative" }}>
            {error && (
              <div style={{ background: "#fff5f5", border: "1px solid #fed7d7", color: "#c53030", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.88rem" }}>{error}</div>
            )}

            {/* ── 对话消息流 ── */}
            <div style={{ paddingBottom: 88 }}>

              {/* 开场问候 */}
              <AgentRow sender="system">
                <FlowBubble>你好！我将帮你一步步完成爆款视频的生成 👋<br />先上传产品图片，让我们开始吧。</FlowBubble>
              </AgentRow>

              {/* STEP 1：上传产品图 */}
              <AgentRow sender="system">
                <FlowStepCard step={1} title="上传产品图">
                  <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
                    <UploadBox slot={front} label="正面图" required onUpload={f => uploadImg(f, "/api/video/general/upload/image", "image_url", setFront)} onRemove={() => removeSlot(setFront)} />
                    <UploadBox slot={back}  label="反面图" onUpload={f => uploadImg(f, "/api/video/general/upload/image", "image_url", setBack)}  onRemove={() => removeSlot(setBack)} />
                    <UploadBox slot={rear}  label="侧面图" onUpload={f => uploadImg(f, "/api/video/general/upload/image", "image_url", setRear)}  onRemove={() => removeSlot(setRear)} />
                  </div>
                  <div style={{ fontSize: "0.72rem", color: "#a0aec0", marginTop: 10 }}>正面图必传，其余可选 · 每张 ≤ 10MB</div>
                  {flowStep === 1 && <FlowNextBtn label="已上传，下一步" onClick={() => setFlowStep(2)} disabled={!front.url || front.uploading} />}
                </FlowStepCard>
              </AgentRow>

              {/* STEP 2：模特来源 */}
              {flowStep >= 2 && (
                <>
                  <UserRow content="已上传产品图 ✓" />
                  <AgentRow sender="system">
                    <FlowStepCard step={2} title="选择模特来源">
                      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
                        {([
                          { v: "auto",  label: "AI 自动出模特", desc: "GPT-Image 2 生成，按市场自动匹配" },
                          { v: "image", label: "上传模特图",    desc: "上传真人照片，复刻模特形象" },
                          { v: "video", label: "上传模特视频",  desc: "上传视频，自动提取中间帧" },
                        ] as { v: ModelSrc; label: string; desc: string }[]).map(o => (
                          <label key={o.v} onClick={() => setModelSrc(o.v)}
                            style={{ flex: "1 1 160px", minWidth: 140, border: `2px solid ${modelSrc === o.v ? "#0d0d0d" : "#e2e8f0"}`, borderRadius: 12, padding: "0.75rem", cursor: "pointer", background: modelSrc === o.v ? "#f7f7f5" : "#fff", transition: "all 0.15s" }}>
                            <input type="radio" name="modelSrcFlow" value={o.v} checked={modelSrc === o.v} onChange={() => setModelSrc(o.v)} style={{ marginRight: 7 }} />
                            <strong style={{ fontSize: "0.85rem" }}>{o.label}</strong>
                            <div style={{ fontSize: "0.72rem", color: "#718096", marginTop: 4, lineHeight: 1.4 }}>{o.desc}</div>
                          </label>
                        ))}
                      </div>
                      {modelSrc === "image" && (
                        <label style={{ display: "flex", alignItems: "center", gap: 10, border: "2px dashed #e2e8f0", borderRadius: 10, padding: "0.75rem 1rem", cursor: "pointer", background: "#fafafa", marginBottom: 10 }}>
                          <input type="file" accept="image/*" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadImg(f, "/api/video/general/upload/model-image", "model_image_url", setModelImg); e.target.value = ""; }} />
                          {modelImg.preview ? <><img src={modelImg.preview} alt="model" style={{ width: 44, height: 44, objectFit: "cover", borderRadius: 8 }} /><span style={{ fontSize: "0.82rem", color: "#4a5568" }}>已上传 {modelImg.uploading ? "（上传中…）" : "✓"}</span></> : <span style={{ fontSize: "0.82rem", color: "#a0aec0" }}>点击上传模特图片</span>}
                        </label>
                      )}
                      {modelSrc === "video" && (
                        <label style={{ display: "flex", alignItems: "center", gap: 10, border: "2px dashed #e2e8f0", borderRadius: 10, padding: "0.75rem 1rem", cursor: "pointer", background: "#fafafa", marginBottom: 10 }}>
                          <input type="file" accept="video/*" style={{ display: "none" }} onChange={async e => {
                            const f = e.target.files?.[0]; if (!f) return;
                            setModelVid({ file: f, uploading: true }); setError("");
                            try {
                              const fd = new FormData(); fd.append("file", f);
                              const r = await fetch(`${API_BASE}/api/video/general/upload/video`, { method: "POST", headers: { Authorization: `Bearer ${getToken()}` }, body: fd });
                              if (!r.ok) throw new Error(await r.text());
                              const d = await r.json(); setModelVidUrl(d.video_url || ""); setModelVid({ file: f, uploading: false });
                            } catch (err) { setError((err as Error).message || "视频上传失败"); setModelVid({ file: null, uploading: false }); }
                            e.target.value = "";
                          }} />
                          <span style={{ fontSize: "0.82rem", color: modelVid.file ? "#4a5568" : "#a0aec0" }}>{modelVid.uploading ? "上传中…" : modelVid.file ? `已上传：${modelVid.file.name} ✓` : "点击上传模特视频（≤ 100MB）"}</span>
                        </label>
                      )}
                      {flowStep === 2 && <FlowNextBtn label="确认模特来源" onClick={() => setFlowStep(3)} />}
                    </FlowStepCard>
                  </AgentRow>
                </>
              )}

              {/* STEP 3：创作方式 */}
              {flowStep >= 3 && (
                <>
                  <UserRow content="已选择模特来源 ✓" />
                  <AgentRow sender="system">
                    <FlowStepCard step={3} title="选择创作方式">
                      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
                        {([
                          { v: "story",  icon: "🎬", label: "剧情模式",   desc: "有故事冲突，产品是救星（推荐）" },
                          { v: "direct", icon: "📦", label: "直接带货",   desc: "博主展示+安利产品，无剧情" },
                          { v: "chat",   icon: "💬", label: "AI导师对话", desc: "跟AI对话，共同策划专属脚本" },
                        ] as { v: "story" | "direct" | "chat"; icon: string; label: string; desc: string }[]).map(o => (
                          <button key={o.v} onClick={() => setScriptMode(o.v)}
                            style={{ flex: "1 1 130px", padding: "0.7rem 0.8rem", border: `2px solid ${scriptMode === o.v ? "#0d0d0d" : "#e2e8f0"}`, borderRadius: 10, background: scriptMode === o.v ? "#0d0d0d" : "#fff", color: scriptMode === o.v ? "#fff" : "#4a5568", cursor: "pointer", textAlign: "left", transition: "all 0.15s" }}>
                            <div style={{ fontSize: "0.85rem", fontWeight: 600, marginBottom: 3 }}>{o.icon} {o.label}</div>
                            <div style={{ fontSize: "0.68rem", opacity: scriptMode === o.v ? 0.75 : 0.7, lineHeight: 1.4 }}>{o.desc}</div>
                          </button>
                        ))}
                      </div>
                      {scriptMode !== "chat" && (
                        <div style={{ borderTop: "1px solid #f0f0f0", paddingTop: 14 }}>
                          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 14 }}>
                            <div>
                              <div style={{ fontSize: "0.72rem", color: "#718096", marginBottom: 5 }}>总时长</div>
                              <select value={duration} onChange={e => setDuration(+e.target.value)} style={{ padding: "0.45rem 0.7rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.85rem", background: "#fff", color: "#1a202c" }}>
                                {[10, 15, 30].map(v => <option key={v} value={v}>{v} 秒</option>)}
                              </select>
                            </div>
                            <div>
                              <div style={{ fontSize: "0.72rem", color: "#718096", marginBottom: 5 }}>分辨率</div>
                              <select value={resolution} onChange={e => setResolution(e.target.value)} style={{ padding: "0.45rem 0.7rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.85rem", background: "#fff", color: "#1a202c" }}>
                                <option value="1080p">1080P（高清）</option>
                                <option value="2k">2K +20分</option>
                                <option value="4k">4K +50分</option>
                              </select>
                            </div>
                          </div>
                          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                            {([{ v: true, icon: "🔊", label: "有声音" }, { v: false, icon: "🔇", label: "无声音" }] as { v: boolean; icon: string; label: string }[]).map(o => (
                              <button key={String(o.v)} onClick={() => setEnableVoice(o.v)}
                                style={{ flex: 1, padding: "0.5rem 0.7rem", borderRadius: 8, border: `2px solid ${enableVoice === o.v ? "#0d0d0d" : "#e2e8f0"}`, background: enableVoice === o.v ? "#0d0d0d" : "#fff", color: enableVoice === o.v ? "#fff" : "#4a5568", fontSize: "0.82rem", fontWeight: 600, cursor: "pointer", transition: "all 0.15s" }}>
                                {o.icon} {o.label}
                              </button>
                            ))}
                          </div>
                          <div>
                            <div style={{ fontSize: "0.72rem", color: "#718096", marginBottom: 5 }}>你的想法（可选）</div>
                            <textarea value={userIdea} onChange={e => setUserIdea(e.target.value.slice(0, 500))} rows={2} placeholder="描述想要的风格、场景等，留空AI自动决定" style={{ width: "100%", padding: "0.5rem 0.7rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.82rem", lineHeight: 1.5, resize: "vertical", fontFamily: "inherit", boxSizing: "border-box", color: "#1a202c" }} />
                          </div>
                        </div>
                      )}
                      {/* 平台 + 目标国家选择 */}
                      <div style={{ borderTop: "1px solid #f0f0f0", paddingTop: 14, marginBottom: 14 }}>
                        <div style={{ fontSize: "0.75rem", color: "#718096", marginBottom: 8 }}>发布平台</div>
                        <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
                          {([{ v: "tiktok" as const, icon: "🌍", label: "TikTok", sub: "海外市场" }, { v: "douyin" as const, icon: "🇨🇳", label: "抖音", sub: "中国市场" }]).map(o => (
                            <button key={o.v} onClick={() => { setPlatform(o.v); if (o.v === "douyin") { setTargetLang("zh"); setMarket("中国"); } }}
                              style={{ flex: 1, padding: "0.7rem", borderRadius: 10, border: `2px solid ${platform === o.v ? "#0d0d0d" : "#e2e8f0"}`, background: platform === o.v ? "#0d0d0d" : "#fff", color: platform === o.v ? "#fff" : "#4a5568", cursor: "pointer", textAlign: "center" as const, transition: "all 0.15s" }}>
                              <div style={{ fontWeight: 600 }}>{o.icon} {o.label}</div>
                              <div style={{ fontSize: "0.68rem", opacity: 0.7 }}>{o.sub}</div>
                            </button>
                          ))}
                        </div>
                        {platform === "tiktok" && (
                          <>
                            <div style={{ fontSize: "0.75rem", color: "#718096", marginBottom: 8 }}>目标国家</div>
                            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                              {TIKTOK_COUNTRIES.map(o => (
                                <button key={o.lang} onClick={() => { setTargetLang(o.lang); setMarket(_langToMarket(o.lang)); }}
                                  style={{ padding: "0.5rem 0.8rem", borderRadius: 10, border: `2px solid ${targetLang === o.lang ? "#7c3aed" : "#e2e8f0"}`, background: targetLang === o.lang ? "#7c3aed" : "#fff", color: targetLang === o.lang ? "#fff" : "#374151", fontSize: "0.8rem", cursor: "pointer", transition: "all 0.12s", minWidth: 100, textAlign: "center" as const }}>
                                  <div style={{ fontWeight: 600 }}>{o.label}</div>
                                  <div style={{ fontSize: "0.68rem", opacity: 0.7 }}>{o.sub}</div>
                                </button>
                              ))}
                            </div>
                          </>
                        )}
                        {platform === "douyin" && (
                          <div style={{ fontSize: "0.78rem", color: "#7c3aed", background: "#faf5ff", borderRadius: 8, padding: "0.6rem 0.8rem" }}>✅ 抖音内容将使用中文，面向中国市场</div>
                        )}
                      </div>
                      {flowStep === 3 && <FlowNextBtn label={scriptMode === "chat" ? "开始AI对话" : "生成脚本"} onClick={() => setFlowStep(4)} />}
                    </FlowStepCard>
                  </AgentRow>
                </>
              )}

              {/* STEP 4A：普通模式（剧情/直接带货） */}
              {flowStep >= 4 && scriptMode !== "chat" && (
                <>
                  <UserRow content={`选择${scriptMode === "story" ? "剧情模式" : "直接带货"} ✓`} />
                  <AgentRow sender="linjiu">
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      <FlowBubble>好的！我来帮你生成{scriptMode === "story" ? "剧情带货" : "直接带货"}脚本 🎬</FlowBubble>
                      <button onClick={generate} disabled={loading || !front.url || front.uploading}
                        style={{ padding: "0.7rem", borderRadius: 10, border: "none", background: loading || !front.url ? "#e2e8f0" : "#7c3aed", color: loading || !front.url ? "#a0aec0" : "#fff", fontSize: "0.9rem", fontWeight: 600, cursor: loading || !front.url ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
                        {loading ? <><span style={{ display: "inline-block", width: 14, height: 14, border: "2px solid #a0aec0", borderTopColor: "#fff", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />AI 正在撰写脚本…</> : script ? "重新生成脚本（35积分）" : "生成创意脚本（35积分）"}
                      </button>
                      {script && !loading && (
                        <div>
                          <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "#718096", marginBottom: 8 }}>✨ 生成的创意脚本</div>
                          <ScriptDisplay text={script} />
                          <ContrastUploadHint scriptText={script} />
                          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                            <button onClick={generate} disabled={loading || vidLoading} style={{ flex: "1 1 140px", padding: "0.6rem 0.9rem", borderRadius: 8, border: "1px solid #e2e8f0", background: "#fff", color: "#374151", fontSize: "0.85rem", cursor: "pointer", fontWeight: 500 }}>🔄 重新生成（35积分）</button>
                            <button onClick={() => generateVideo()} disabled={vidLoading || loading}
                              style={{ flex: "1 1 140px", padding: "0.6rem 0.9rem", borderRadius: 8, border: "none", background: vidLoading || loading ? "#e2e8f0" : "#16a34a", color: vidLoading || loading ? "#a0aec0" : "#fff", fontSize: "0.85rem", fontWeight: 600, cursor: vidLoading || loading ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
                              {vidLoading ? <><span style={{ display: "inline-block", width: 12, height: 12, border: "2px solid #a0aec0", borderTopColor: "#555", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />生成中…</> : "🎬 确认，生成视频"}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </AgentRow>
                  {/* 场景确认（非chat模式脚本生成后自动触发）*/}
                  {script && !loading && <SceneConfirmCard />}
                </>
              )}

              {/* STEP 4B：AI导师对话模式 */}
              {flowStep >= 4 && scriptMode === "chat" && (
                <>
                  <UserRow content="开始AI导师对话，策划专属脚本！" />

                  {/* 林久开场 */}
                  {chatMsgs.length === 0 && !chatLoading && (
                    <AgentRow sender="linjiu">
                      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                        <FlowBubble>我是林久，你的专属创意导师 💜<br />让我先分析一下你的产品，然后我们一起策划最适合的爆款脚本。</FlowBubble>
                        <button onClick={() => sendChatMessage(
                          scene.url
                            ? "请帮我分析这个产品，开始创作！"
                            : "请帮我分析这个产品，开始创作！（提示：我还没有上传场景图，如果有合适的场景环境照片请提醒我上传）"
                        )} disabled={chatLoading || !front.url}
                          style={{ alignSelf: "flex-start", padding: "0.55rem 1.2rem", borderRadius: 8, border: "none", background: !front.url ? "#e2e8f0" : "#7c3aed", color: !front.url ? "#a0aec0" : "#fff", fontSize: "0.85rem", fontWeight: 600, cursor: !front.url ? "not-allowed" : "pointer" }}>
                          开始分析产品 →
                        </button>
                      </div>
                    </AgentRow>
                  )}

                  {/* 对话消息 */}
                  {chatMsgs.map((m, msgIdx) => (
                    <div key={msgIdx}>
                      {m.role === "user" ? (
                        <UserRow content={m.content} images={m.images} />
                      ) : (
                        <>
                          {/* 小李搜索结果（先于林久显示） */}
                          {m.searchResult && (
                            <AgentRow sender="xiaoli">
                              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                <FlowBubble style={{ background: "#eff6ff", border: "1px solid #bfdbfe", color: "#1e3a8a" }}>
                                  我是小李，趋势研究员 📡<br />让我搜索一下最新爆款趋势…
                                </FlowBubble>
                                <FlowBubble style={{ background: "#eff6ff", border: "1px solid #bfdbfe", color: "#1e40af", fontSize: "0.8rem" }}>
                                  {m.searchResult.slice(0, 300)}{m.searchResult.length > 300 ? "…" : ""}
                                </FlowBubble>
                                <div style={{ fontSize: "0.75rem", color: "#3b82f6", fontWeight: 500, paddingLeft: 2 }}>以上是最新趋势，交给林久继续创作 ✅</div>
                              </div>
                            </AgentRow>
                          )}
                          {/* 林久主回复 */}
                          <AgentRow sender="linjiu">
                            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                              {m.content && <FlowBubble>{m.content}</FlowBubble>}
                              {m.questions && m.questions.length > 0 && (() => {
                                const qs = m.questions!;
                                const submitAll = () => {
                                  const parts: string[] = [];
                                  qs.forEach((q, qi) => {
                                    const key = msgIdx * 10 + qi;
                                    const ans = chatCustomInputs[key]?.trim() || chatSelections[key]?.trim();
                                    if (ans) parts.push(`${qi + 1}. ${q.question}：${ans}`);
                                  });
                                  if (!parts.length) return;
                                  sendChatMessage(parts.join("\n"));
                                  setChatSelections(prev => { const n = { ...prev }; qs.forEach((_, qi) => delete n[msgIdx * 10 + qi]); return n; });
                                  setChatCustomInputs(prev => { const n = { ...prev }; qs.forEach((_, qi) => delete n[msgIdx * 10 + qi]); return n; });
                                };
                                return (
                                  <div>
                                    {qs.map((q, qi) => {
                                      const key = msgIdx * 10 + qi;
                                      const selected = chatSelections[key];
                                      return (
                                        <div key={qi} style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, padding: "0.8rem 1rem", marginBottom: 8 }}>
                                          <div style={{ fontWeight: 600, fontSize: "0.85rem", color: "#1a202c", marginBottom: 4 }}>{q.question}</div>
                                          {q.description && <div style={{ fontSize: "0.7rem", color: "#9ca3af", marginBottom: 8 }}>{q.description}</div>}
                                          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
                                            {q.options.map((opt, oi) => {
                                              const isSel = selected === opt;
                                              return (
                                                <button key={oi} onClick={() => { setChatSelections(prev => ({ ...prev, [key]: isSel ? "" : opt })); if (!isSel) setChatCustomInputs(prev => ({ ...prev, [key]: "" })); }} disabled={chatLoading}
                                                  style={{ padding: "0.3rem 0.75rem", borderRadius: 999, fontSize: "0.78rem", cursor: chatLoading ? "not-allowed" : "pointer", border: isSel ? "1px solid #0d0d0d" : "1px solid #d1d5db", background: isSel ? "#0d0d0d" : "#f9fafb", color: isSel ? "#fff" : "#374151", transition: "all 0.12s" }}>
                                                  {opt}
                                                </button>
                                              );
                                            })}
                                          </div>
                                          <input placeholder="或自定义回答…" value={chatCustomInputs[key] || ""} onChange={e => { setChatCustomInputs(prev => ({ ...prev, [key]: e.target.value })); if (e.target.value) setChatSelections(prev => ({ ...prev, [key]: "" })); }}
                                            style={{ width: "100%", padding: "0.3rem 0.6rem", border: "1px solid #e2e8f0", borderRadius: 6, fontSize: "0.78rem", boxSizing: "border-box" }} />
                                        </div>
                                      );
                                    })}
                                    <button onClick={submitAll} disabled={chatLoading}
                                      style={{ width: "100%", padding: "0.6rem", borderRadius: 10, border: "none", background: chatLoading ? "#e2e8f0" : "#7c3aed", color: chatLoading ? "#a0aec0" : "#fff", fontSize: "0.85rem", fontWeight: 600, cursor: chatLoading ? "not-allowed" : "pointer" }}>
                                      提交回答
                                    </button>
                                  </div>
                                );
                              })()}
                            </div>
                          </AgentRow>
                          {/* 审稿专家 — 每条有 review 的消息独立显示 */}
                          {m.review && (() => {
                            const rv = m.review!;
                            const s = rv.score;
                            const clr = s >= 50 ? "#16a34a" : s >= 40 ? "#d97706" : "#dc2626";
                            const bg  = s >= 50 ? "#f0fdf4" : s >= 40 ? "#fefce8" : "#fef2f2";
                            const bdr = s >= 50 ? "#86efac" : s >= 40 ? "#fde68a" : "#fca5a5";
                            const expanded = chatReviewExpanded[msgIdx] ?? false;
                            return (
                              <AgentRow sender="reviewer">
                                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                  <FlowBubble>让我来审查一下这个脚本… 🔍</FlowBubble>
                                  <div style={{ border: `1px solid ${bdr}`, borderRadius: 10, overflow: "hidden" }}>
                                    <div style={{ padding: "0.55rem 0.85rem", background: bg, display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer" }}
                                      onClick={() => setChatReviewExpanded(prev => ({ ...prev, [msgIdx]: !expanded }))}>
                                      <span style={{ fontWeight: 700, fontSize: "0.82rem", color: clr }}>{s >= 50 ? "✅" : s >= 40 ? "⚠️" : "❌"} 审稿评分：{s}/60</span>
                                      <span style={{ fontSize: "0.72rem", color: clr }}>{expanded ? "▲ 收起" : "▼ 展开"}</span>
                                    </div>
                                    {expanded && (
                                      <div style={{ padding: "0.75rem 0.85rem", background: "#fff", fontSize: "0.78rem", color: "#374151", lineHeight: 1.6 }}>
                                        <div style={{ whiteSpace: "pre-wrap", marginBottom: rv.suggestions ? 8 : 0 }}>{rv.details}</div>
                                        {rv.suggestions && <div style={{ borderTop: "1px solid #f3f4f6", paddingTop: 8, color: "#6b7280", whiteSpace: "pre-wrap" }}>{rv.suggestions}</div>}
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </AgentRow>
                            );
                          })()}
                        </>
                      )}
                    </div>
                  ))}

                  {/* 思考中 */}
                  {chatLoading && (
                    <AgentRow sender="linjiu">
                      <FlowBubble style={{ color: "#6b7280", display: "inline-flex", alignItems: "center", gap: 8 }}>
                        <span style={{ display: "inline-block", width: 14, height: 14, border: "2px solid #d8b4fe", borderTopColor: "#7c3aed", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                        正在思考…
                      </FlowBubble>
                    </AgentRow>
                  )}

                  {/* 脚本 */}
                  {chatScript && (
                    <AgentRow sender="linjiu">
                      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                        <FlowBubble>好的，根据我们的讨论，脚本来了 ✨</FlowBubble>
                        <ScriptDisplay text={chatScript} />
                        <ContrastUploadHint scriptText={chatScript} />
                      </div>
                    </AgentRow>
                  )}

                  {/* 场景确认（脚本后自动触发）*/}
                  {chatScript && <SceneConfirmCard />}

                  {/* 确认脚本 + 参数 */}
                  {chatScript && (
                    <div style={{ paddingLeft: 44, marginBottom: 18 }}>
                      {!chatShowParams ? (
                        <button onClick={() => setChatShowParams(true)} disabled={vidLoading}
                          style={{ padding: "0.65rem 1.4rem", borderRadius: 10, border: "none", background: vidLoading ? "#e2e8f0" : "#16a34a", color: vidLoading ? "#a0aec0" : "#fff", fontSize: "0.88rem", fontWeight: 600, cursor: vidLoading ? "not-allowed" : "pointer" }}>
                          🎬 确认脚本，生成视频
                        </button>
                      ) : (
                        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: "1rem" }}>
                          <div style={{ fontSize: "0.82rem", fontWeight: 600, color: "#1a202c", marginBottom: 12 }}>选择生成参数</div>
                          <div style={{ marginBottom: 12 }}>
                            <div style={{ fontSize: "0.72rem", color: "#718096", marginBottom: 6 }}>声音</div>
                            <div style={{ display: "flex", gap: 8 }}>
                              {([{ v: true, icon: "🔊", label: "有声音" }, { v: false, icon: "🔇", label: "无声音" }] as { v: boolean; icon: string; label: string }[]).map(o => (
                                <button key={String(o.v)} onClick={() => setChatEnableVoice(o.v)}
                                  style={{ flex: 1, padding: "0.5rem 0.7rem", borderRadius: 8, border: `2px solid ${chatEnableVoice === o.v ? "#0d0d0d" : "#e2e8f0"}`, background: chatEnableVoice === o.v ? "#0d0d0d" : "#fff", color: chatEnableVoice === o.v ? "#fff" : "#4a5568", fontSize: "0.82rem", fontWeight: 600, cursor: "pointer" }}>
                                  {o.icon} {o.label}
                                </button>
                              ))}
                            </div>
                          </div>
                          <div style={{ marginBottom: 14 }}>
                            <div style={{ fontSize: "0.72rem", color: "#718096", marginBottom: 6 }}>分辨率</div>
                            <select value={chatResolution} onChange={e => setChatResolution(e.target.value)}
                              style={{ width: "100%", padding: "0.5rem 0.7rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.85rem", background: "#fff", color: "#1a202c" }}>
                              <option value="1080p">1080P（高清）</option>
                              <option value="2k">2K（超清）+20积分</option>
                              <option value="4k">4K（极清）+50积分</option>
                            </select>
                          </div>
                          <div style={{ marginBottom: 14 }}>
                            <div style={{ fontSize: "0.72rem", color: "#718096", marginBottom: 6 }}>目标语言</div>
                            <select value={targetLang} onChange={e => setTargetLang(e.target.value)}
                              style={{ width: "100%", padding: "0.5rem 0.7rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.85rem", background: "#fff", color: "#1a202c" }}>
                              {LANG_OPTIONS}
                            </select>
                          </div>
                          <div style={{ display: "flex", gap: 8 }}>
                            <button onClick={() => setChatShowParams(false)} style={{ padding: "0.6rem 1rem", borderRadius: 8, border: "1px solid #e2e8f0", background: "#fff", color: "#4a5568", fontSize: "0.82rem", cursor: "pointer" }}>← 返回</button>
                            <button onClick={() => { const oR = resolution; const oV = enableVoice; setResolution(chatResolution); setEnableVoice(chatEnableVoice); setTimeout(() => { generateVideo(chatScript); setResolution(oR); setEnableVoice(oV); }, 0); setChatShowParams(false); }} disabled={vidLoading}
                              style={{ flex: 1, padding: "0.6rem 1rem", borderRadius: 8, border: "none", background: vidLoading ? "#e2e8f0" : "#16a34a", color: vidLoading ? "#a0aec0" : "#fff", fontSize: "0.88rem", fontWeight: 600, cursor: vidLoading ? "not-allowed" : "pointer" }}>
                              🎬 开始生成视频
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* 对比图上传 */}
                  {chatNeedsContrast && (
                    <div style={{ paddingLeft: 44, marginBottom: 14 }}>
                      <div style={{ padding: "0.85rem 1rem", background: "#fefce8", border: "1px solid #fde68a", borderRadius: 10 }}>
                        <div style={{ fontWeight: 600, fontSize: "0.82rem", color: "#92400e", marginBottom: 6 }}>📸 AI导师建议上传对比图</div>
                        <div style={{ fontSize: "0.75rem", color: "#78350f", marginBottom: 10 }}>上传一张旧款/竞品图片，视频对比效果更真实。</div>
                        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "0.4rem 0.8rem", background: "#f59e0b", color: "#fff", borderRadius: 6, fontSize: "0.8rem", fontWeight: 600, cursor: chatImgUploading ? "not-allowed" : "pointer" }}>
                          <input type="file" accept="image/*" style={{ display: "none" }} disabled={chatImgUploading} onChange={async e => { const f = e.target.files?.[0]; if (!f) return; setChatImgUploading(true); try { const fd = new FormData(); fd.append("file", f); const r = await fetch(`${API_BASE}/api/video/general/upload/image`, { method: "POST", headers: { Authorization: `Bearer ${getToken()}` }, body: fd }); if (!r.ok) throw new Error(await r.text()); const d = await r.json(); if (d.image_url) setChatPendingImages(prev => [...prev, d.image_url]); } catch (err) { setError((err as Error).message || "图片上传失败"); } finally { setChatImgUploading(false); } e.target.value = ""; }} />
                          {chatImgUploading ? "上传中…" : "📎 上传对比图"}
                        </label>
                      </div>
                    </div>
                  )}

                  {/* 文案师 */}
                  {chatCopy && chatCopy.title && (
                    <AgentRow sender="copywriter">
                      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        <FlowBubble style={{ background: "#fff7ed", border: "1px solid #fed7aa", color: "#9a3412" }}>视频准备生成了！我帮你准备了发布文案 📋</FlowBubble>
                        <div style={{ padding: "0.9rem 1rem", background: "#fff7ed", border: "1px solid #fed7aa", borderRadius: 12 }}>
                          <div style={{ marginBottom: 8 }}>
                            <div style={{ fontSize: "0.7rem", color: "#9a3412", marginBottom: 3 }}>视频标题</div>
                            <div style={{ display: "flex", alignItems: "center", gap: 6, background: "#fff", border: "1px solid #fed7aa", borderRadius: 6, padding: "0.4rem 0.6rem" }}>
                              <span style={{ flex: 1, fontSize: "0.83rem", color: "#111827" }}>{chatCopy.title}</span>
                              <button onClick={() => { navigator.clipboard.writeText(chatCopy!.title); setChatCopyCopied("title"); setTimeout(() => setChatCopyCopied(""), 1500); }} style={{ flexShrink: 0, padding: "0.2rem 0.5rem", borderRadius: 4, border: "1px solid #fed7aa", background: "#fff7ed", color: "#ea580c", fontSize: "0.72rem", cursor: "pointer" }}>{chatCopyCopied === "title" ? "✓" : "复制"}</button>
                            </div>
                          </div>
                          {chatCopy.description && (
                            <div style={{ marginBottom: 8 }}>
                              <div style={{ fontSize: "0.7rem", color: "#9a3412", marginBottom: 3 }}>视频描述</div>
                              <div style={{ display: "flex", alignItems: "flex-start", gap: 6, background: "#fff", border: "1px solid #fed7aa", borderRadius: 6, padding: "0.4rem 0.6rem" }}>
                                <span style={{ flex: 1, fontSize: "0.8rem", color: "#374151", lineHeight: 1.5 }}>{chatCopy.description}</span>
                                <button onClick={() => { navigator.clipboard.writeText(chatCopy!.description); setChatCopyCopied("desc"); setTimeout(() => setChatCopyCopied(""), 1500); }} style={{ flexShrink: 0, padding: "0.2rem 0.5rem", borderRadius: 4, border: "1px solid #fed7aa", background: "#fff7ed", color: "#ea580c", fontSize: "0.72rem", cursor: "pointer" }}>{chatCopyCopied === "desc" ? "✓" : "复制"}</button>
                              </div>
                            </div>
                          )}
                          {chatCopy.hashtags?.length > 0 && (
                            <div style={{ marginBottom: 8 }}>
                              <div style={{ fontSize: "0.7rem", color: "#9a3412", marginBottom: 5 }}>话题标签</div>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 4 }}>
                                {chatCopy.hashtags.map((tag, i) => <span key={i} style={{ padding: "0.2rem 0.55rem", background: "#dbeafe", color: "#1d4ed8", borderRadius: 999, fontSize: "0.75rem", fontWeight: 500 }}>{tag}</span>)}
                              </div>
                              <button onClick={() => { navigator.clipboard.writeText(chatCopy!.hashtags.join(" ")); setChatCopyCopied("tags"); setTimeout(() => setChatCopyCopied(""), 1500); }} style={{ padding: "0.2rem 0.6rem", borderRadius: 4, border: "1px solid #bfdbfe", background: "#eff6ff", color: "#1d4ed8", fontSize: "0.72rem", cursor: "pointer" }}>{chatCopyCopied === "tags" ? "✓ 已复制" : "一键复制全部标签"}</button>
                            </div>
                          )}
                          {chatCopy.best_time && (
                            <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "0.35rem 0.6rem", background: "#fff", border: "1px solid #fed7aa", borderRadius: 6 }}>
                              <span style={{ fontSize: "0.7rem", color: "#9a3412" }}>推荐发布时间</span>
                              <span style={{ fontSize: "0.8rem", color: "#ea580c", fontWeight: 600 }}>🕐 {chatCopy.best_time}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    </AgentRow>
                  )}
                </>
              )}

              {/* 视频生成进度 */}
              {vidLoading && vidProgress && (
                <AgentRow sender="system">
                  <FlowBubble style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", color: "#166534" }}>⏳ {vidProgress}</FlowBubble>
                </AgentRow>
              )}

              {/* 视频结果 */}
              {vidUrl && !vidLoading && (
                <AgentRow sender="system">
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <FlowBubble>🎬 视频生成完成！</FlowBubble>
                    <video src={vidUrl} controls style={{ width: "100%", maxWidth: 400, borderRadius: 12, display: "block" }} />
                    <div style={{ display: "flex", gap: 10 }}>
                      <a href={vidUrl} download style={{ fontSize: "0.85rem", color: "#16a34a", textDecoration: "none", fontWeight: 500 }}>⬇ 下载视频</a>
                      <button onClick={() => { setVidUrl(""); setVidCost(0); }} style={{ background: "none", border: "none", color: "#888", fontSize: "0.82rem", cursor: "pointer" }}>重新生成</button>
                    </div>
                  </div>
                </AgentRow>
              )}

              <div ref={chatEndRef} />
            </div>

            {/* ── 底部固定输入栏（仅AI导师模式激活） ── */}
            <div style={{ position: "sticky", bottom: 0, background: "#edeae4", borderTop: "1px solid #e2e8f0", padding: "0.7rem 0" }}>
              {chatPendingImages.length > 0 && (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                  {chatPendingImages.map((url, i) => (
                    <div key={i} style={{ position: "relative" }}>
                      <img src={url} alt="待发图" style={{ width: 48, height: 48, objectFit: "cover", borderRadius: 8, border: "1px solid #e2e8f0" }} />
                      <button onClick={() => setChatPendingImages(prev => prev.filter((_, j) => j !== i))} style={{ position: "absolute", top: -6, right: -6, width: 16, height: 16, borderRadius: "50%", background: "#e53e3e", color: "#fff", border: "none", cursor: "pointer", fontSize: "0.65rem", display: "flex", alignItems: "center", justifyContent: "center" }}>×</button>
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <label style={{ width: 36, height: 36, borderRadius: 8, border: "1px solid #e2e8f0", background: flowStep >= 4 && scriptMode === "chat" ? "#fafafa" : "#f4f4f4", display: "flex", alignItems: "center", justifyContent: "center", cursor: flowStep >= 4 && scriptMode === "chat" && !chatImgUploading ? "pointer" : "not-allowed", flexShrink: 0, fontSize: "1rem", opacity: flowStep >= 4 && scriptMode === "chat" ? 1 : 0.4 }}>
                  <input type="file" accept="image/*" style={{ display: "none" }} disabled={!(flowStep >= 4 && scriptMode === "chat") || chatImgUploading}
                    onChange={async e => { const f = e.target.files?.[0]; if (!f) return; setChatImgUploading(true); try { const fd = new FormData(); fd.append("file", f); const r = await fetch(`${API_BASE}/api/video/general/upload/image`, { method: "POST", headers: { Authorization: `Bearer ${getToken()}` }, body: fd }); if (!r.ok) throw new Error(await r.text()); const d = await r.json(); if (d.image_url) setChatPendingImages(prev => [...prev, d.image_url]); } catch (err) { setError((err as Error).message || "图片上传失败"); } finally { setChatImgUploading(false); } e.target.value = ""; }} />
                  {chatImgUploading ? "⏳" : "📎"}
                </label>
                <input
                  value={chatInput}
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && flowStep >= 4 && scriptMode === "chat") { e.preventDefault(); sendChatMessage(); } }}
                  placeholder={flowStep >= 4 && scriptMode === "chat" ? "输入你的想法或修改意见…" : "请先完成上方步骤…"}
                  disabled={!(flowStep >= 4 && scriptMode === "chat")}
                  style={{ flex: 1, padding: "0.6rem 0.9rem", border: "1px solid #e2e8f0", borderRadius: 10, fontSize: "0.85rem", background: flowStep >= 4 && scriptMode === "chat" ? "#fff" : "#f4f4f4", color: flowStep >= 4 && scriptMode === "chat" ? "#1a202c" : "#a0aec0" }}
                />
                <button onClick={() => sendChatMessage()} disabled={!(flowStep >= 4 && scriptMode === "chat") || chatLoading || (!chatInput.trim() && !chatPendingImages.length)}
                  style={{ padding: "0.6rem 1.1rem", borderRadius: 10, border: "none", background: flowStep >= 4 && scriptMode === "chat" && !chatLoading && (chatInput.trim() || chatPendingImages.length) ? "#0d0d0d" : "#e2e8f0", color: flowStep >= 4 && scriptMode === "chat" && !chatLoading && (chatInput.trim() || chatPendingImages.length) ? "#fff" : "#a0aec0", cursor: flowStep >= 4 && scriptMode === "chat" && !chatLoading && (chatInput.trim() || chatPendingImages.length) ? "pointer" : "not-allowed", fontWeight: 600, fontSize: "0.85rem" }}>
                  发送
                </button>
              </div>
            </div>
          </div>
        )}

        {/* spin keyframe */}
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </main>
    </div>
  );
}
