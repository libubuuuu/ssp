"use client";
import { useState } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface Scene {
  id: number;
  time_range: string;
  duration_sec: number;
  shot: string;
  action: string;
  visual_prompt: string;
  speech?: string;
  narrative_role?: "hook" | "setup_pain" | "showcase" | "solve" | "memorable" | "cta" | string;
}

interface CreativeBrief {
  hook?: string;
  pain_point?: string;
  emotional_arc?: string;
  scene_setting?: string;
  resonance_signal?: string;
  memorable_moment?: string;
  cta?: string;
}

interface ProductSpecifics {
  subcategory?: string;
  form_constraint?: string;
  key_visual_features?: string[];
}

interface AnalyzeResult {
  category: string;
  target_user?: string;
  selling_points: string[];
  creative_brief?: CreativeBrief;
  product_specifics?: ProductSpecifics;
  scenes: Scene[];
  total_duration: number;
}

const ROLE_META: Record<string, { label: string; bg: string; fg: string }> = {
  hook:        { label: "🎣 钩子",   bg: "#fef3c7", fg: "#92400e" },
  setup_pain:  { label: "💔 痛点",   bg: "#fee2e2", fg: "#991b1b" },
  showcase:    { label: "✨ 展示",   bg: "#dbeafe", fg: "#1e40af" },
  solve:       { label: "💡 解决",   bg: "#d1fae5", fg: "#065f46" },
  memorable:   { label: "💎 记忆点", bg: "#ede9fe", fg: "#5b21b6" },
  cta:         { label: "📢 CTA",    bg: "#fce7f3", fg: "#9d174d" },
};

const BRIEF_LABELS: Record<keyof CreativeBrief, string> = {
  hook:              "🎣 钩子(前 3s 抓眼球)",
  pain_point:        "💔 痛点/冲突",
  emotional_arc:     "🎢 情绪主线",
  scene_setting:     "🪞 场景代入",
  resonance_signal:  "✨ 共鸣信号",
  memorable_moment:  "💎 记忆点",
  cta:               "📢 结尾 CTA",
};

function token() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

type ProductSlot = "front" | "back" | "rear";
const PRODUCT_SLOT_LABELS: Record<ProductSlot, string> = {
  front: "正面图(主图,必传)",
  back: "反面图(选)",
  rear: "背面图(选)",
};
const PRODUCT_SLOT_ORDER: ProductSlot[] = ["front", "back", "rear"];

// 2026-05-12:Box 必须定义在 component 外,否则每次 render 都生成新 function 引用,
// React 看作新组件 → unmount + remount textarea → 输入法 composition 中断 + 失焦 + 页面滚顶
function Box({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <div style={{ background: "#fff", borderRadius: 14, padding: "1.2rem 1.4rem", marginBottom: "1.2rem", boxShadow: "0 2px 8px rgba(0,0,0,0.04)" }}>
      <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.6rem", fontWeight: 500 }}>{label}</div>
      {children}
    </div>
  );
}

export default function VideoGeneralPage() {
  // 2026-05-11 P226:产品图分 3 槽(正面/反面/背面),按 PRODUCT_SLOT_ORDER 顺序拼成 product_image_urls 给 backend
  const [productImagesBySlot, setProductImagesBySlot] = useState<Record<ProductSlot, string>>({ front: "", back: "", rear: "" });
  const [productFilesBySlot, setProductFilesBySlot] = useState<Record<ProductSlot, File | null>>({ front: null, back: null, rear: null });
  // 派生:把非空 slot 按顺序拼成 list 给 backend
  const productImageUrls = PRODUCT_SLOT_ORDER.map((s) => productImagesBySlot[s]).filter(Boolean);
  // 2026-05-11 P226:场景图(可选)
  const [sceneImageUrl, setSceneImageUrl] = useState<string>("");
  const [sceneImageFile, setSceneImageFile] = useState<File | null>(null);
  const [modelSource, setModelSource] = useState<"auto" | "image" | "video">("auto");
  const [modelImageUrl, setModelImageUrl] = useState<string>("");
  const [modelVideoUrl, setModelVideoUrl] = useState<string>("");
  const [modelImageFile, setModelImageFile] = useState<File | null>(null);
  const [modelVideoFile, setModelVideoFile] = useState<File | null>(null);
  const [duration, setDuration] = useState<number>(15);
  const [region, setRegion] = useState<"CN" | "Global">("CN");
  const [aspectRatio, setAspectRatio] = useState<string>("9:16");
  // 2026-05-11:用户大概想法(可选)— 留空 AI 自动生成,填了 AI 纳入脚本
  const [userBrief, setUserBrief] = useState<string>("");
  // 2026-05-12:用户指定的搭配/场景描述 + 批量生成数量
  const [userOutfit, setUserOutfit] = useState<string>("");
  const [userScene, setUserScene] = useState<string>("");
  const [batchCount, setBatchCount] = useState<number>(1);
  const [analyzing, setAnalyzing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [analyzeMsg, setAnalyzeMsg] = useState("");
  const [generateMsg, setGenerateMsg] = useState("");
  const [error, setError] = useState("");
  const [analyzeResult, setAnalyzeResult] = useState<AnalyzeResult | null>(null);
  const [resultVideoUrl, setResultVideoUrl] = useState<string>("");
  const [resultVideoUrls, setResultVideoUrls] = useState<string[]>([]);
  // 2026-05-12:N 段独立分镜视频(每条带 batch_idx / scene_idx / scene meta)
  const [sceneVideos, setSceneVideos] = useState<Array<{batch_idx: number; scene_idx: number; url: string; scene: {narrative_role?: string; shot?: string; visual_prompt?: string; speech?: string; duration_sec?: number}}>>([]);
  // 2026-05-11 P226:分镜板预览 + 角色表
  const [storyboardLoading, setStoryboardLoading] = useState(false);
  const [storyboardMsg, setStoryboardMsg] = useState("");
  const [storyboardUrl, setStoryboardUrl] = useState<string>("");
  const [characterSheetUrl, setCharacterSheetUrl] = useState<string>("");
  const [storyboardModelUrl, setStoryboardModelUrl] = useState<string>("");
  const [storyboardNPanels, setStoryboardNPanels] = useState<number>(0);

  const uploadProductSlot = async (slot: ProductSlot, f: File) => {
    setError("");
    setProductFilesBySlot((prev) => ({ ...prev, [slot]: f }));
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`${API_BASE}/api/video/general/upload/image`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setProductImagesBySlot((prev) => ({ ...prev, [slot]: d.image_url }));
    } catch (e) {
      setError(errMsg(e, `${PRODUCT_SLOT_LABELS[slot]} 上传失败`));
      setProductFilesBySlot((prev) => ({ ...prev, [slot]: null }));
    }
  };

  const removeProductSlot = (slot: ProductSlot) => {
    setProductImagesBySlot((prev) => ({ ...prev, [slot]: "" }));
    setProductFilesBySlot((prev) => ({ ...prev, [slot]: null }));
  };

  const uploadSceneImage = async (f: File) => {
    setError(""); setSceneImageFile(f);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`${API_BASE}/api/video/general/upload/scene-image`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setSceneImageUrl(d.scene_image_url);
    } catch (e) { setError(errMsg(e, "场景图上传失败")); setSceneImageFile(null); }
  };

  const uploadModelImage = async (f: File) => {
    setError(""); setModelImageFile(f);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`${API_BASE}/api/video/general/upload/model-image`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setModelImageUrl(d.model_image_url);
    } catch (e) { setError(errMsg(e, "模特图上传失败")); setModelImageFile(null); }
  };

  const uploadModelVideo = async (f: File) => {
    setError(""); setModelVideoFile(f);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`${API_BASE}/api/video/general/upload/video`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setModelVideoUrl(d.video_url);
      setModelImageUrl(d.model_image_url);  // 视频中间帧自动作为模特图
    } catch (e) { setError(errMsg(e, "模特视频上传失败")); setModelVideoFile(null); }
  };

  const analyze = async () => {
    if (!productImageUrls.length) { setError("请至少上传 1 张产品图"); return; }
    setError(""); setAnalyzing(true); setAnalyzeMsg("提交分析...");
    try {
      const r = await fetch(`${API_BASE}/api/video/general/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({
          product_image_urls: productImageUrls,
          total_duration: duration,
          region,
          user_brief: userBrief.trim() || undefined,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      const aid = d.analyze_job_id;
      adjustLocalUserCredits(-1);
      setAnalyzeMsg("AI 分析中(品类识别 + 卖点提取 + 脚本生成,30-90s)...");
      let elapsed = 0;
      const interval = setInterval(async () => {
        elapsed += 5;
        try {
          const sr = await fetch(`${API_BASE}/api/video/general/analyze/status/${aid}`, {
            headers: { Authorization: `Bearer ${token()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(interval);
            setAnalyzeResult({
              category: sd.category,
              target_user: sd.target_user || "",
              selling_points: sd.selling_points || [],
              creative_brief: sd.creative_brief || {},
              product_specifics: sd.product_specifics || {},
              scenes: sd.scenes || [],
              total_duration: sd.total_duration || duration,
            });
            setAnalyzing(false); setAnalyzeMsg("");
          } else if (sd.status === "failed") {
            clearInterval(interval);
            setError(sd.error || "分析失败");
            setAnalyzing(false); setAnalyzeMsg("");
          } else {
            setAnalyzeMsg(`AI 分析中... 已 ${elapsed}s`);
          }
        } catch {}
      }, 5000);
    } catch (e) { setError(errMsg(e, "分析失败")); setAnalyzing(false); setAnalyzeMsg(""); }
  };

  const generateStoryboard = async () => {
    if (!analyzeResult) return;
    setError(""); setStoryboardLoading(true); setStoryboardMsg("提交分镜预览...");
    setStoryboardUrl(""); setCharacterSheetUrl(""); setStoryboardModelUrl(""); setStoryboardNPanels(0);
    try {
      const r = await fetch(`${API_BASE}/api/video/general/storyboard`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({
          product_image_urls: productImageUrls,
          scene_image_url: sceneImageUrl || null,
          model_image_url: modelImageUrl || null,
          model_video_url: modelVideoUrl || null,
          category: analyzeResult.category,
          target_user: analyzeResult.target_user || null,
          creative_brief: analyzeResult.creative_brief || null,
          product_specifics: analyzeResult.product_specifics || null,
          scenes: analyzeResult.scenes,
          region,
          aspect_ratio: aspectRatio,
          user_outfit: userOutfit || null,
          user_scene: userScene || null,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      const sid = d.job_id;
      adjustLocalUserCredits(-d.cost);
      setStoryboardMsg(`AI 分镜板生成中(预计 3-5 分钟,job=${sid})...`);
      let elapsed = 0;
      const interval = setInterval(async () => {
        elapsed += 5;
        try {
          const sr = await fetch(`${API_BASE}/api/video/general/storyboard/status/${sid}`, {
            headers: { Authorization: `Bearer ${token()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(interval);
            setStoryboardUrl(sd.storyboard_image_url || "");
            setCharacterSheetUrl(sd.character_sheet_url || "");
            setStoryboardModelUrl(sd.model_image_url || "");
            setStoryboardNPanels(sd.n_panels || 0);
            setStoryboardLoading(false); setStoryboardMsg("");
            // 如果 AI 帮我们生成了模特图,自动填入 modelImageUrl(后续 generate 复用)
            if (sd.model_image_url && !modelImageUrl) {
              setModelImageUrl(sd.model_image_url);
            }
          } else if (sd.status === "failed") {
            clearInterval(interval);
            setError(sd.error || "分镜板生成失败");
            setStoryboardLoading(false); setStoryboardMsg("");
          } else {
            setStoryboardMsg(`AI 分镜板生成中... 已 ${elapsed}s`);
          }
        } catch {}
      }, 5000);
    } catch (e) { setError(errMsg(e, "分镜板生成失败")); setStoryboardLoading(false); setStoryboardMsg(""); }
  };

  const updateScene = (idx: number, key: keyof Scene, val: string) => {
    if (!analyzeResult) return;
    const newScenes = analyzeResult.scenes.map((s, i) => i === idx ? { ...s, [key]: val } : s);
    setAnalyzeResult({ ...analyzeResult, scenes: newScenes });
  };

  const generate = async () => {
    if (!analyzeResult) return;
    setError(""); setGenerating(true); setGenerateMsg("提交生成...");
    try {
      const r = await fetch(`${API_BASE}/api/video/general/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({
          product_image_urls: productImageUrls,
          scene_image_url: sceneImageUrl || null,
          model_image_url: modelImageUrl || null,
          model_video_url: modelVideoUrl || null,
          category: analyzeResult.category,
          target_user: analyzeResult.target_user || null,
          creative_brief: analyzeResult.creative_brief || null,
          product_specifics: analyzeResult.product_specifics || null,
          scenes: analyzeResult.scenes,
          total_duration: analyzeResult.total_duration,
          region,
          aspect_ratio: aspectRatio,
          user_outfit: userOutfit || null,
          user_scene: userScene || null,
          batch_count: batchCount,
          storyboard_image_url: storyboardUrl || null,
          storyboard_n_panels: storyboardNPanels || 0,
          character_sheet_image_url: characterSheetUrl || null,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      const jid = d.job_id;
      adjustLocalUserCredits(-d.cost);
      setGenerateMsg(`视频生成中(预计 7-10 分钟,job=${jid})...`);
      let elapsed = 0;
      const interval = setInterval(async () => {
        elapsed += 10;
        try {
          const sr = await fetch(`${API_BASE}/api/jobs/${jid}`, {
            headers: { Authorization: `Bearer ${token()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(interval);
            const urls: string[] = sd.result?.video_urls || (sd.result?.video_url ? [sd.result.video_url] : []);
            const svideos = sd.result?.scene_videos || urls.map((u: string, i: number) => ({batch_idx: 0, scene_idx: i, url: u, scene: {}}));
            setResultVideoUrls(urls);
            setResultVideoUrl(urls[0] || "");
            setSceneVideos(svideos);
            setGenerating(false); setGenerateMsg("");
          } else if (sd.status === "failed") {
            clearInterval(interval);
            setError(sd.error || "生成失败");
            setGenerating(false); setGenerateMsg("");
          } else {
            setGenerateMsg(`生成中... 已 ${Math.floor(elapsed/60)}:${(elapsed%60).toString().padStart(2,"0")}`);
          }
        } catch {}
      }, 10000);
    } catch (e) { setError(errMsg(e, "生成失败")); setGenerating(false); setGenerateMsg(""); }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>AI 创作工具</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>通用<span style={{ fontStyle: "italic" }}> 产品视频</span> · 任意品类</h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            食品 / 日用品 / 化妆品 / 3C / 服装 — 多张产品图 + 可选真人模特视频 → AI 自动判品类 + 出脚本 + 模特持/穿/用产品视频
          </div>
        </div>

        {error && (
          <div style={{ background: "#fff3f3", border: "1px solid #fcc", color: "#c33", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.9rem", whiteSpace: "pre-wrap" }}>{error}</div>
        )}

        <Box label="① 上传产品图(3 角度 — 正面必传 / 反面 / 背面)+ 场景图(可选)">
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
            {PRODUCT_SLOT_ORDER.map((slot) => {
              const url = productImagesBySlot[slot];
              return (
                <div key={slot} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                  <div style={{ fontSize: "0.72rem", color: "#666", fontWeight: 500, height: 16 }}>{PRODUCT_SLOT_LABELS[slot]}</div>
                  {url ? (
                    <div style={{ position: "relative" }}>
                      <img src={url} alt={slot} style={{ width: 96, height: 96, objectFit: "cover", borderRadius: 8, border: "1px solid #ddd" }} />
                      <button onClick={() => removeProductSlot(slot)} style={{ position: "absolute", top: -6, right: -6, width: 22, height: 22, borderRadius: "50%", background: "#dc2626", color: "#fff", border: "none", cursor: "pointer", fontSize: "0.7rem" }}>×</button>
                    </div>
                  ) : (
                    <label style={{ width: 96, height: 96, border: "2px dashed #ddd", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "#999", fontSize: "0.8rem", textAlign: "center" }}>
                      <input type="file" accept="image/*" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadProductSlot(slot, f); e.target.value = ""; }} />
                      + 上传
                    </label>
                  )}
                </div>
              );
            })}
            {/* 场景图 */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, borderLeft: "1px dashed #ddd", paddingLeft: 12, marginLeft: 4 }}>
              <div style={{ fontSize: "0.72rem", color: "#666", fontWeight: 500, height: 16 }}>场景图(可选)</div>
              {sceneImageUrl ? (
                <div style={{ position: "relative" }}>
                  <img src={sceneImageUrl} alt="scene" style={{ width: 96, height: 96, objectFit: "cover", borderRadius: 8, border: "1px solid #ddd" }} />
                  <button onClick={() => { setSceneImageUrl(""); setSceneImageFile(null); }} style={{ position: "absolute", top: -6, right: -6, width: 22, height: 22, borderRadius: "50%", background: "#dc2626", color: "#fff", border: "none", cursor: "pointer", fontSize: "0.7rem" }}>×</button>
                </div>
              ) : (
                <label style={{ width: 96, height: 96, border: "2px dashed #ddd", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "#999", fontSize: "0.8rem", textAlign: "center" }}>
                  <input type="file" accept="image/*" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadSceneImage(f); e.target.value = ""; }} />
                  + 上传
                </label>
              )}
            </div>
          </div>
          <div style={{ fontSize: "0.78rem", color: "#999" }}>正面图必传,反面/背面/场景图可选。每张 ≤ 10MB。</div>
        </Box>

        <Box label="② 模特来源(可选,三选一)">
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
            {[
              { value: "auto", label: "AI 自动出模特", desc: "GPT-Image 2 出亚洲/西方面孔(按市场)" },
              { value: "image", label: "上传模特图", desc: "用你提供的人作模特" },
              { value: "video", label: "上传模特视频", desc: "用视频里那个人作模特(抽中间帧)" },
            ].map((o) => (
              <label key={o.value} style={{ flex: 1, minWidth: 180, border: modelSource === o.value ? "2px solid #0d8a3e" : "1px solid #ddd", borderRadius: 10, padding: "0.7rem", cursor: "pointer", background: modelSource === o.value ? "#f0fdf4" : "#fff" }}>
                <input type="radio" name="model" value={o.value} checked={modelSource === o.value} onChange={() => setModelSource(o.value as "auto" | "image" | "video")} style={{ marginRight: 6 }} />
                <strong style={{ fontSize: "0.9rem" }}>{o.label}</strong>
                <div style={{ fontSize: "0.75rem", color: "#666", marginTop: 4 }}>{o.desc}</div>
              </label>
            ))}
          </div>
          {modelSource === "image" && (
            <label style={{ display: "block", border: "2px dashed #ddd", borderRadius: 10, padding: "0.8rem", textAlign: "center", cursor: "pointer", background: modelImageFile ? "#f9f7f2" : "#fff" }}>
              <input type="file" accept="image/*" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadModelImage(f); }} />
              {modelImageFile ? `✓ ${modelImageFile.name}` : "点击上传模特图(JPG/PNG, ≤10MB)"}
            </label>
          )}
          {modelSource === "video" && (
            <label style={{ display: "block", border: "2px dashed #ddd", borderRadius: 10, padding: "0.8rem", textAlign: "center", cursor: "pointer", background: modelVideoFile ? "#f9f7f2" : "#fff" }}>
              <input type="file" accept="video/*" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadModelVideo(f); }} />
              {modelVideoFile ? `✓ ${modelVideoFile.name}` : "点击上传模特视频(MP4/MOV, ≤100MB)"}
            </label>
          )}
        </Box>

        <Box label="③ 视频参数 + 你的想法(想法可留空,AI 全自动)">
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 14 }}>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>总时长</div>
              <select value={duration} onChange={e => setDuration(parseInt(e.target.value))} style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value={5}>5 秒</option>
                <option value={10}>10 秒</option>
                <option value={15}>15 秒</option>
                <option value={30}>30 秒</option>
                <option value={60}>60 秒</option>
              </select>
              <div style={{ fontSize: "0.7rem", color: "#9ca3af", marginTop: 3 }}>AI 按叙事节奏自动拆段(短时长 1-2 段 / 长时长 5-8 段)</div>
            </div>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>市场</div>
              <select value={region} onChange={e => setRegion(e.target.value as "CN" | "Global")} style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value="CN">国内(中文,亚洲面孔)</option>
                <option value="Global">海外(英文,Western)</option>
              </select>
            </div>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>比例</div>
              <select value={aspectRatio} onChange={e => setAspectRatio(e.target.value)} style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value="9:16">9:16 竖版</option>
                <option value="16:9">16:9 横版</option>
                <option value="1:1">1:1 方形</option>
              </select>
            </div>
          </div>
          <div>
            <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>💡 你的想法(可选,500 字内 — 目标人群/卖点重点/风格/CTA 方向)</div>
            <textarea
              value={userBrief}
              onChange={e => setUserBrief(e.target.value.slice(0, 500))}
              rows={3}
              placeholder="例:目标 30+ 精致妈妈,强调安全成分 + 7 天见效;复古日系小清新风格;结尾导向'立即领取试用装'。留空 AI 完全自动判。"
              style={{ width: "100%", padding: "0.5rem 0.7rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.85rem", lineHeight: 1.6, resize: "vertical", fontFamily: "inherit" }}
            />
            <div style={{ fontSize: "0.72rem", color: "#999", textAlign: "right", marginTop: 2 }}>{userBrief.length}/500</div>
          </div>
        </Box>

        {!analyzeResult && (
          <button onClick={analyze} disabled={analyzing || !productImageUrls.length} style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: analyzing ? "not-allowed" : "pointer", opacity: analyzing ? 0.6 : 1, marginBottom: "1rem" }}>
            {analyzing ? analyzeMsg : "🔍 AI 分析产品 + 出脚本(消耗 1 积分)"}
          </button>
        )}

        {analyzeResult && (
          <>
            <Box label={`④ AI 分析结果 · 品类:${analyzeResult.category}${analyzeResult.target_user ? ` · 目标用户:${analyzeResult.target_user}` : ""}`}>
              {analyzeResult.product_specifics && (analyzeResult.product_specifics.subcategory || (analyzeResult.product_specifics.key_visual_features || []).length > 0) && (
                <div style={{ background: "#fef9e7", border: "1px solid #fde68a", padding: "0.7rem 0.9rem", borderRadius: 8, marginBottom: 10 }}>
                  <strong style={{ fontSize: "0.85rem", color: "#92400e" }}>🔒 AI 识别的产品(用于锁形态不变形):</strong>
                  {analyzeResult.product_specifics.subcategory && (
                    <div style={{ fontSize: "0.82rem", color: "#1f2937", marginTop: 4 }}>
                      <b>子类:</b> {analyzeResult.product_specifics.subcategory}
                    </div>
                  )}
                  {analyzeResult.product_specifics.form_constraint && (
                    <div style={{ fontSize: "0.78rem", color: "#6b7280", marginTop: 2 }}>
                      <b>形态约束:</b> {analyzeResult.product_specifics.form_constraint}
                    </div>
                  )}
                  {(analyzeResult.product_specifics.key_visual_features || []).length > 0 && (
                    <div style={{ fontSize: "0.78rem", color: "#6b7280", marginTop: 2 }}>
                      <b>视觉特征:</b> {(analyzeResult.product_specifics.key_visual_features || []).join("、")}
                    </div>
                  )}
                  <div style={{ fontSize: "0.7rem", color: "#9ca3af", marginTop: 4 }}>识别错了请重新 analyze(或上传更清晰的产品图)</div>
                </div>
              )}
              {analyzeResult.selling_points.length > 0 && (
                <div style={{ background: "#f0f7fb", padding: "0.6rem 0.8rem", borderRadius: 8, marginBottom: 10 }}>
                  <strong style={{ fontSize: "0.85rem" }}>核心卖点:</strong>
                  <ul style={{ margin: "0.4rem 0 0", paddingLeft: "1.2rem", fontSize: "0.85rem" }}>
                    {analyzeResult.selling_points.map((p, i) => <li key={i}>{p}</li>)}
                  </ul>
                </div>
              )}
              {analyzeResult.creative_brief && Object.keys(analyzeResult.creative_brief).length > 0 && (
                <div style={{ background: "#fafaf7", border: "1px solid #ecebe5", padding: "0.7rem 0.9rem", borderRadius: 8, marginBottom: 14 }}>
                  <strong style={{ fontSize: "0.85rem", color: "#374151" }}>🎯 广告创意脑图(可复刻 7 元素)</strong>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "0.5rem 1rem", marginTop: 8 }}>
                    {(Object.keys(BRIEF_LABELS) as (keyof CreativeBrief)[]).map((k) => {
                      const v = analyzeResult.creative_brief?.[k];
                      if (!v) return null;
                      return (
                        <div key={k} style={{ fontSize: "0.78rem", lineHeight: 1.5 }}>
                          <div style={{ color: "#9ca3af", marginBottom: 2 }}>{BRIEF_LABELS[k]}</div>
                          <div style={{ color: "#374151" }}>{v}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {analyzeResult.scenes.map((sc, idx) => {
                const role = sc.narrative_role || "";
                const meta = ROLE_META[role];
                return (
                <div key={sc.id || idx} style={{ borderTop: idx > 0 ? "1px solid #eee" : "none", paddingTop: idx > 0 ? "1rem" : 0, marginTop: idx > 0 ? "1rem" : 0 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6, flexWrap: "wrap" }}>
                    <strong style={{ fontSize: "0.9rem" }}>镜 {sc.id || idx + 1}</strong>
                    {meta && (
                      <span style={{ background: meta.bg, color: meta.fg, fontSize: "0.72rem", padding: "0.15rem 0.5rem", borderRadius: 4, fontWeight: 500 }}>
                        {meta.label}
                      </span>
                    )}
                    <span style={{ fontSize: "0.78rem", color: "#999" }}>{sc.time_range} · {sc.duration_sec}s · {sc.shot}</span>
                  </div>
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ fontSize: "0.75rem", color: "#666", marginBottom: 2 }}>动作:</div>
                    <input value={sc.action || ""} onChange={e => updateScene(idx, "action", e.target.value)} style={{ width: "100%", padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.82rem" }} />
                  </div>
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ fontSize: "0.75rem", color: "#666", marginBottom: 2 }}>画面 prompt:</div>
                    <textarea value={sc.visual_prompt || ""} onChange={e => updateScene(idx, "visual_prompt", e.target.value)} rows={2} style={{ width: "100%", padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.82rem", fontFamily: "monospace", resize: "vertical" }} />
                  </div>
                  <div>
                    <div style={{ fontSize: "0.75rem", color: "#666", marginBottom: 2 }}>口播文字:</div>
                    <textarea value={sc.speech || ""} onChange={e => updateScene(idx, "speech", e.target.value)} rows={2} placeholder="留空 → 该段纯画面无声" style={{ width: "100%", padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.82rem", lineHeight: 1.5, resize: "vertical" }} />
                  </div>
                </div>
                );
              })}
            </Box>

            {/* 2026-05-12:用户指定的人物搭配 + 场景(可选,留空 AI 自动生成)*/}
            {!resultVideoUrl && (
              <Box label="④.3 模特搭配 & 场景(可选 · 留空让 AI 自动生成)">
                <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 10, lineHeight: 1.5 }}>
                  填了就硬约束:模特除产品外的穿搭 + 整体场景 — AI 会严格按你写的来,不再自由发挥。
                </div>
                <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
                  <div>
                    <div style={{ fontSize: "0.78rem", color: "#374151", marginBottom: 4, fontWeight: 500 }}>👕 人物搭配(除产品外):</div>
                    <textarea value={userOutfit} onChange={e => setUserOutfit(e.target.value)} rows={3} placeholder="例:白色 T 恤 + 牛仔裤 + 小白鞋(留空 AI 自配)" style={{ width: "100%", padding: "0.5rem 0.7rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.85rem", lineHeight: 1.5, resize: "vertical", fontFamily: "inherit" }} maxLength={500} />
                  </div>
                  <div>
                    <div style={{ fontSize: "0.78rem", color: "#374151", marginBottom: 4, fontWeight: 500 }}>🏠 场景描述:</div>
                    <textarea value={userScene} onChange={e => setUserScene(e.target.value)} rows={3} placeholder="例:明亮的客厅 / 落地窗 / 午后阳光(留空 AI 自配)" style={{ width: "100%", padding: "0.5rem 0.7rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.85rem", lineHeight: 1.5, resize: "vertical", fontFamily: "inherit" }} maxLength={500} />
                  </div>
                </div>
              </Box>
            )}

            {/* 2026-05-11 P226:分镜板预览(2 积分,3-5 分钟,N≤4 宫格,可看着满意再生成视频)*/}
            {!resultVideoUrl && (
              <Box label={`④.5 分镜板预览(可选 · 2 积分 · ${analyzeResult.scenes.length >= 2 ? `最多 ${Math.min(4, analyzeResult.scenes.length)} 宫格` : "1 张首帧图"})`}>
                <div style={{ fontSize: "0.82rem", color: "#666", marginBottom: 10, lineHeight: 1.5 }}>
                  生成 1 张分镜板预览图(GPT-Image 2 出图,模特/产品/场景全锁同一 lookbook)。
                  看着满意再生成完整视频,省时间省钱。
                </div>
                {!storyboardUrl && (
                  <button onClick={generateStoryboard} disabled={storyboardLoading} style={{ background: "#7c3aed", color: "#fff", border: "none", padding: "0.7rem 1.2rem", borderRadius: 10, fontSize: "0.9rem", cursor: storyboardLoading ? "not-allowed" : "pointer", opacity: storyboardLoading ? 0.6 : 1 }}>
                    {storyboardLoading ? storyboardMsg : "🎨 生成分镜板预览(消耗 2 积分)"}
                  </button>
                )}
                {storyboardUrl && (
                  <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
                    {/* 角色表 — 脸部 + 全身 3 视图 */}
                    {characterSheetUrl && characterSheetUrl !== storyboardUrl && (
                      <div style={{ flex: "0 1 320px" }}>
                        <div style={{ fontSize: "0.78rem", color: "#374151", fontWeight: 500, marginBottom: 4 }}>🎭 模特角色表(脸部 + 全身正/背/侧)</div>
                        <img src={characterSheetUrl} alt="character sheet" style={{ width: "100%", maxWidth: 320, borderRadius: 10, border: "1px solid #ddd", display: "block" }} />
                        <a href={characterSheetUrl} download style={{ fontSize: "0.72rem", color: "#7c3aed" }}>⬇ 下载</a>
                      </div>
                    )}
                    {/* 分镜板 */}
                    <div style={{ flex: "1 1 320px" }}>
                      <div style={{ fontSize: "0.78rem", color: "#374151", fontWeight: 500, marginBottom: 4 }}>🎬 分镜板预览</div>
                      <img src={storyboardUrl} alt="分镜板预览" style={{ width: "100%", maxWidth: 480, borderRadius: 10, border: "1px solid #ddd", display: "block" }} />
                      <div style={{ fontSize: "0.78rem", color: "#666", marginTop: 8 }}>
                        {storyboardNPanels >= 2 ? `${storyboardNPanels} 宫格(对应前 ${storyboardNPanels} 段 narrative_role)` : "单图预览"}
                        {" · "}
                        <a href={storyboardUrl} download style={{ color: "#7c3aed" }}>⬇ 下载</a>
                        {" · "}
                        <button onClick={() => { setStoryboardUrl(""); setCharacterSheetUrl(""); }} style={{ background: "none", border: "none", color: "#dc2626", cursor: "pointer", padding: 0, fontSize: "0.78rem" }}>重出</button>
                      </div>
                      {storyboardModelUrl && (
                        <div style={{ fontSize: "0.72rem", color: "#9ca3af", marginTop: 4 }}>
                          ✓ 模特身份已锁定,生成视频时复用(省 2-3 分钟)
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </Box>
            )}
            {!resultVideoUrl && (
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: "1rem", flexWrap: "wrap" }}>
                <div style={{ fontSize: "0.85rem", color: "#374151", display: "flex", alignItems: "center", gap: 6 }}>
                  📦 批量生成:
                  <select value={batchCount} onChange={e => setBatchCount(Number(e.target.value))} disabled={generating} style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.85rem", cursor: generating ? "not-allowed" : "pointer" }}>
                    <option value={1}>1 个</option>
                    <option value={2}>2 个</option>
                    <option value={3}>3 个</option>
                    <option value={4}>4 个</option>
                    <option value={5}>5 个</option>
                  </select>
                </div>
                <button onClick={generate} disabled={generating} style={{ background: "#dc2626", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: generating ? "not-allowed" : "pointer", opacity: generating ? 0.6 : 1 }}>
                  {generating ? generateMsg : `🎬 生成视频(消耗 ${analyzeResult.scenes.length * 5 * batchCount} 积分)`}
                </button>
                {batchCount > 1 && !generating && (
                  <span style={{ fontSize: "0.78rem", color: "#9ca3af" }}>同 prompt 跑 {batchCount} 个独立版本,挑最佳</span>
                )}
              </div>
            )}
          </>
        )}

        {resultVideoUrl && (
          <Box label={sceneVideos.length > 0 ? `⑤ 独立分镜视频(${sceneVideos.length} 段)` : "⑤ 生成结果"}>
            {sceneVideos.length > 0 ? (
              <>
                <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 10, lineHeight: 1.5 }}>
                  每段独立成片(不拼接),可单独下载使用。后期需要可自己 ffmpeg / 剪辑软件拼接。
                </div>
                <div style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
                  {sceneVideos.map((sv, i) => (
                    <div key={i}>
                      <div style={{ fontSize: "0.78rem", color: "#374151", marginBottom: 4, fontWeight: 500 }}>
                        分镜 {sv.scene_idx + 1}
                        {sv.scene?.narrative_role ? ` · ${sv.scene.narrative_role}` : ""}
                        {batchCount > 1 ? ` · 批次 ${sv.batch_idx + 1}` : ""}
                      </div>
                      <video src={sv.url} controls style={{ width: "100%", borderRadius: 10 }} />
                      {sv.scene?.shot && (
                        <div style={{ fontSize: "0.7rem", color: "#999", marginTop: 4 }}>{sv.scene.shot}{sv.scene.duration_sec ? ` · ${sv.scene.duration_sec}s` : ""}</div>
                      )}
                      <div style={{ marginTop: 4 }}>
                        <a href={sv.url} download style={{ color: "#0d8a3e", fontSize: "0.82rem" }}>⬇ 下载分镜 {sv.scene_idx + 1}</a>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <>
                <video src={resultVideoUrl} controls style={{ width: "100%", maxWidth: 480, borderRadius: 10 }} />
                <div style={{ marginTop: 10 }}>
                  <a href={resultVideoUrl} download style={{ color: "#0d8a3e", fontSize: "0.85rem" }}>⬇ 下载视频</a>
                </div>
              </>
            )}
          </Box>
        )}
      </main>
    </div>
  );
}
