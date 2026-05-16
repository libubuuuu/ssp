"use client";
import { useState, useCallback, useEffect } from "react";
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

// ─── 主页面 ─────────────────────────────────────────────────────────────────
type Tab = "replicate" | "ai_video";
type ModelSrc = "auto" | "image" | "video";
const MARKETS = ["欧美", "东南亚", "日韩", "中国"];

const REPLICATE_WHITELIST = ["lirunting1a@gmail.com"];

export default function VideoGeneralPage() {
  const [tab, setTab] = useState<Tab>("ai_video");

  // 灰度：视频复刻入口A 白名单判断
  const [isWhitelisted, setIsWhitelisted] = useState(false);
  useEffect(() => {
    const tk = localStorage.getItem("token");
    if (!tk) return;
    fetch(`${API_BASE}/api/auth/me`, { headers: { Authorization: `Bearer ${tk}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.email) setIsWhitelisted(REPLICATE_WHITELIST.includes(d.email)); })
      .catch(() => {});
  }, []);

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
  const [scriptMode, setScriptMode] = useState<"story" | "direct">("story");
  const [duration, setDuration]     = useState(15);
  const [market, setMarket]         = useState("欧美");
  const [userIdea, setUserIdea]     = useState("");
  const [resolution, setResolution]       = useState("480p");
  const [contrastImg, setContrastImg]     = useState<ImgSlot>(emptySlot());
  const [enableVoice, setEnableVoice]     = useState(true);

  // Step 4 - script result
  const [loading, setLoading]   = useState(false);
  const [script, setScript]     = useState("");
  const [error, setError]       = useState("");

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
      setVidProgress(`Seedance 2.0 生成中，约 3-8 分钟（job=${jid}）…`);

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
                    <option value="480p">480p（标准）不加价</option>
                    <option value="1080p">1080p（高清）+10积分</option>
                    <option value="4k">4K（超清）+50积分</option>
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
                      ✅ 任务已提交，预计3-8分钟完成。你可以关闭此页面去做其他事，生成完成后在右上角「我的任务」查看结果。
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

        {/* ── 标签B：AI 爆款视频 ── */}
        {tab === "ai_video" && (
          <>
            {error && (
              <div style={{ background: "#fff5f5", border: "1px solid #fed7d7", color: "#c53030", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.88rem" }}>
                {error}
              </div>
            )}

            {/* ── Step 1：产品图 ── */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 1</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>上传产品图</span>
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
              <div style={{ fontSize: "0.72rem", color: "#a0aec0", marginTop: 10 }}>正面图必传，其余可选 · 每张 ≤ 10MB · 支持 JPG / PNG / WebP</div>
            </div>

            {/* ── Step 2：模特来源 ── */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 2</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>模特来源</span>
              </div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
                {([
                  { v: "auto",  label: "AI 自动出模特",  desc: "GPT-Image 2 生成亚洲/欧美面孔，按市场自动匹配" },
                  { v: "image", label: "上传模特图",      desc: "上传真人照片，复刻模特形象" },
                  { v: "video", label: "上传模特视频",    desc: "上传视频，自动提取中间帧" },
                ] as { v: ModelSrc; label: string; desc: string }[]).map(o => (
                  <label key={o.v} onClick={() => setModelSrc(o.v)}
                    style={{ flex: "1 1 180px", minWidth: 160, border: `2px solid ${modelSrc === o.v ? "#0d0d0d" : "#e2e8f0"}`, borderRadius: 12, padding: "0.85rem", cursor: "pointer", background: modelSrc === o.v ? "#f7f7f5" : "#fff", transition: "all 0.15s" }}>
                    <input type="radio" name="modelSrc" value={o.v} checked={modelSrc === o.v} onChange={() => setModelSrc(o.v)} style={{ marginRight: 7 }} />
                    <strong style={{ fontSize: "0.88rem", color: "#1a202c" }}>{o.label}</strong>
                    <div style={{ fontSize: "0.73rem", color: "#718096", marginTop: 5, lineHeight: 1.5 }}>{o.desc}</div>
                  </label>
                ))}
              </div>
              {modelSrc === "image" && (
                <label style={{ display: "flex", alignItems: "center", gap: 10, border: "2px dashed #e2e8f0", borderRadius: 10, padding: "0.9rem 1rem", cursor: "pointer", background: "#fafafa" }}>
                  <input type="file" accept="image/*" style={{ display: "none" }}
                    onChange={e => { const f = e.target.files?.[0]; if (f) uploadImg(f, "/api/video/general/upload/model-image", "model_image_url", setModelImg); e.target.value = ""; }} />
                  {modelImg.preview
                    ? <><img src={modelImg.preview} alt="model" style={{ width: 48, height: 48, objectFit: "cover", borderRadius: 8 }} /><span style={{ fontSize: "0.85rem", color: "#4a5568" }}>已上传 {modelImg.uploading ? "（上传中…）" : "✓"}</span></>
                    : <span style={{ fontSize: "0.85rem", color: "#a0aec0" }}>点击上传模特图片</span>
                  }
                </label>
              )}
              {modelSrc === "video" && (
                <label style={{ display: "flex", alignItems: "center", gap: 10, border: "2px dashed #e2e8f0", borderRadius: 10, padding: "0.9rem 1rem", cursor: "pointer", background: "#fafafa" }}>
                  <input type="file" accept="video/*" style={{ display: "none" }}
                    onChange={async e => {
                      const f = e.target.files?.[0]; if (!f) return;
                      setModelVid({ file: f, uploading: true }); setError("");
                      try {
                        const fd = new FormData(); fd.append("file", f);
                        const r = await fetch(`${API_BASE}/api/video/general/upload/video`, { method: "POST", headers: { Authorization: `Bearer ${getToken()}` }, body: fd });
                        if (!r.ok) throw new Error(await r.text());
                        const d = await r.json();
                        setModelVidUrl(d.video_url || "");
                        setModelVid({ file: f, uploading: false });
                      } catch (err) { setError((err as Error).message || "视频上传失败"); setModelVid({ file: null, uploading: false }); }
                      e.target.value = "";
                    }} />
                  <span style={{ fontSize: "0.85rem", color: modelVid.file ? "#4a5568" : "#a0aec0" }}>
                    {modelVid.uploading ? "上传中…" : modelVid.file ? `已上传：${modelVid.file.name} ✓` : "点击上传模特视频（≤ 100MB）"}
                  </span>
                </label>
              )}
            </div>

            {/* ── Step 3：视频参数 ── */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 3</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>视频参数</span>
              </div>

              {/* 脚本模式选择 */}
              <div style={{ marginBottom: 18 }}>
                <div style={{ fontSize: "0.75rem", color: "#718096", marginBottom: 8 }}>脚本模式</div>
                <div style={{ display: "flex", gap: 10 }}>
                  {([
                    { v: "story",  icon: "🎬", label: "剧情模式",  desc: "有故事冲突，产品是救星（推荐）" },
                    { v: "direct", icon: "📦", label: "直接带货",  desc: "博主展示+安利产品，无剧情" },
                  ] as { v: "story" | "direct"; icon: string; label: string; desc: string }[]).map(o => (
                    <button key={o.v} onClick={() => setScriptMode(o.v)}
                      style={{
                        flex: "1 1 140px", padding: "0.7rem 0.9rem", border: `2px solid ${scriptMode === o.v ? "#0d0d0d" : "#e2e8f0"}`,
                        borderRadius: 10, background: scriptMode === o.v ? "#0d0d0d" : "#fff",
                        color: scriptMode === o.v ? "#fff" : "#4a5568", cursor: "pointer",
                        textAlign: "left", transition: "all 0.15s",
                      }}>
                      <div style={{ fontSize: "0.88rem", fontWeight: 600, marginBottom: 3 }}>{o.icon} {o.label}</div>
                      <div style={{ fontSize: "0.7rem", opacity: scriptMode === o.v ? 0.75 : 0.7, lineHeight: 1.4 }}>{o.desc}</div>
                    </button>
                  ))}
                </div>
              </div>

              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
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
                    <option value="480p">480p（标准）不加价</option>
                    <option value="1080p">1080p（高清）+10积分</option>
                    <option value="4k">4K（超清）+50积分</option>
                  </select>
                </div>
              </div>
              {/* 声音开关 */}
              <div style={{ marginBottom: 16 }}>
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

              <div>
                <div style={{ fontSize: "0.75rem", color: "#718096", marginBottom: 5 }}>你的想法（可选）</div>
                <textarea
                  value={userIdea}
                  onChange={e => setUserIdea(e.target.value.slice(0, 500))}
                  rows={3}
                  placeholder="描述你想要的风格、场景等，留空 AI 自动决定"
                  style={{ width: "100%", padding: "0.6rem 0.8rem", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.85rem", lineHeight: 1.6, resize: "vertical", fontFamily: "inherit", boxSizing: "border-box", color: "#1a202c" }}
                />
                <div style={{ fontSize: "0.7rem", color: "#cbd5e0", textAlign: "right", marginTop: 3 }}>{userIdea.length}/500</div>
              </div>
            </div>

            {/* ── Step 4：生成按钮 ── */}
            <div style={CARD}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: "1rem" }}>
                <span style={STEP_LABEL}>STEP 4</span>
                <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1a202c" }}>生成创意脚本</span>
              </div>

              <button
                onClick={generate}
                disabled={loading || !front.url || front.uploading}
                style={{
                  width: "100%", padding: "0.9rem", borderRadius: 10, border: "none", fontSize: "0.95rem", fontWeight: 600,
                  background: loading || !front.url ? "#e2e8f0" : "#0d0d0d",
                  color: loading || !front.url ? "#a0aec0" : "#fff",
                  cursor: loading || !front.url ? "not-allowed" : "pointer",
                  transition: "all 0.15s",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                }}
              >
                {loading ? (
                  <>
                    <span style={{ display: "inline-block", width: 16, height: 16, border: "2px solid #a0aec0", borderTopColor: "#555", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                    AI 正在分析产品并撰写爆款脚本…
                  </>
                ) : script ? "重新生成脚本（35 积分）" : "生成创意脚本（35 积分）"}
              </button>
              {!front.url && !loading && (
                <div style={{ fontSize: "0.75rem", color: "#a0aec0", textAlign: "center", marginTop: 6 }}>请先上传正面产品图</div>
              )}

              {/* 脚本展示区 */}
              {script && !loading && (
                <div style={{ marginTop: "1.4rem" }}>
                  <div style={{ fontSize: "0.8rem", color: "#718096", fontWeight: 600, marginBottom: 10, letterSpacing: "0.05em" }}>✨ 生成的创意脚本</div>
                  <ScriptDisplay text={script} />
                  <ContrastUploadHint scriptText={script} />

                  {/* 行动按钮 */}
                  <div style={{ display: "flex", gap: 10, marginTop: "1.2rem", flexWrap: "wrap" }}>
                    <button
                      onClick={generate}
                      disabled={loading || vidLoading}
                      style={{ flex: "1 1 200px", padding: "0.7rem 1rem", borderRadius: 10, border: "1px solid #e2e8f0", background: "#fff", color: "#374151", fontSize: "0.88rem", cursor: (loading || vidLoading) ? "not-allowed" : "pointer", fontWeight: 500 }}
                    >
                      🔄 重新生成脚本（35 积分）
                    </button>
                    <button
                      onClick={() => generateVideo()}
                      disabled={vidLoading || loading}
                      style={{
                        flex: "1 1 200px", padding: "0.7rem 1rem", borderRadius: 10, border: "none",
                        background: vidLoading || loading ? "#e2e8f0" : "#16a34a",
                        color: vidLoading || loading ? "#a0aec0" : "#fff",
                        fontSize: "0.88rem", cursor: vidLoading || loading ? "not-allowed" : "pointer",
                        fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                      }}
                    >
                      {vidLoading ? (
                        <>
                          <span style={{ display: "inline-block", width: 14, height: 14, border: "2px solid #a0aec0", borderTopColor: "#555", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                          生成中…
                        </>
                      ) : "🎬 确认脚本，生成视频"}
                    </button>
                  </div>

                  {/* 视频生成进度 */}
                  {vidLoading && vidProgress && (
                    <div style={{ marginTop: 12, padding: "0.7rem 0.9rem", background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, fontSize: "0.82rem", color: "#166534" }}>
                      ⏳ {vidProgress}
                    </div>
                  )}

                  {/* 视频结果 */}
                  {vidUrl && !vidLoading && (
                    <div style={{ marginTop: "1.2rem" }}>
                      <div style={{ fontSize: "0.8rem", color: "#718096", fontWeight: 600, marginBottom: 10 }}>🎬 生成完成</div>
                      <video src={vidUrl} controls style={{ width: "100%", maxWidth: 400, borderRadius: 10, display: "block" }} />
                      <div style={{ marginTop: 8, display: "flex", gap: 10, flexWrap: "wrap" }}>
                        <a href={vidUrl} download style={{ fontSize: "0.85rem", color: "#16a34a", textDecoration: "none", fontWeight: 500 }}>⬇ 下载视频</a>
                        <button onClick={() => { setVidUrl(""); setVidCost(0); }}
                          style={{ background: "none", border: "none", color: "#888", fontSize: "0.82rem", cursor: "pointer" }}>
                          重新生成视频
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}

        {/* spin keyframe */}
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </main>
    </div>
  );
}
