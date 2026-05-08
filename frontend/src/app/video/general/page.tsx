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
}

interface AnalyzeResult {
  category: string;
  selling_points: string[];
  scenes: Scene[];
  total_duration: number;
}

function token() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

export default function VideoGeneralPage() {
  const [productImageUrls, setProductImageUrls] = useState<string[]>([]);
  const [productImageFiles, setProductImageFiles] = useState<File[]>([]);
  const [modelSource, setModelSource] = useState<"auto" | "image" | "video">("auto");
  const [modelImageUrl, setModelImageUrl] = useState<string>("");
  const [modelVideoUrl, setModelVideoUrl] = useState<string>("");
  const [modelImageFile, setModelImageFile] = useState<File | null>(null);
  const [modelVideoFile, setModelVideoFile] = useState<File | null>(null);
  const [duration, setDuration] = useState<number>(15);
  const [region, setRegion] = useState<"CN" | "Global">("CN");
  const [aspectRatio, setAspectRatio] = useState<string>("9:16");
  const [analyzing, setAnalyzing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [analyzeMsg, setAnalyzeMsg] = useState("");
  const [generateMsg, setGenerateMsg] = useState("");
  const [error, setError] = useState("");
  const [analyzeResult, setAnalyzeResult] = useState<AnalyzeResult | null>(null);
  const [resultVideoUrl, setResultVideoUrl] = useState<string>("");

  const uploadProductImage = async (f: File) => {
    setError("");
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
      setProductImageUrls((urls) => [...urls, d.image_url]);
      setProductImageFiles((files) => [...files, f]);
    } catch (e) { setError(errMsg(e, "产品图上传失败")); }
  };

  const removeProductImage = (idx: number) => {
    setProductImageUrls((urls) => urls.filter((_, i) => i !== idx));
    setProductImageFiles((files) => files.filter((_, i) => i !== idx));
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
              selling_points: sd.selling_points || [],
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
          model_image_url: modelImageUrl || null,
          model_video_url: modelVideoUrl || null,
          category: analyzeResult.category,
          scenes: analyzeResult.scenes,
          total_duration: analyzeResult.total_duration,
          region,
          aspect_ratio: aspectRatio,
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
            setResultVideoUrl(sd.result?.video_url || "");
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

  const Box = ({ children, label }: { children: React.ReactNode; label: string }) => (
    <div style={{ background: "#fff", borderRadius: 14, padding: "1.2rem 1.4rem", marginBottom: "1.2rem", boxShadow: "0 2px 8px rgba(0,0,0,0.04)" }}>
      <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.6rem", fontWeight: 500 }}>{label}</div>
      {children}
    </div>
  );

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

        <Box label="① 上传产品图(主图必填,详情页/包装/多角度可选,最多 5 张)">
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
            {productImageUrls.map((u, i) => (
              <div key={i} style={{ position: "relative" }}>
                <img src={u} alt={`p${i}`} style={{ width: 80, height: 80, objectFit: "cover", borderRadius: 8, border: "1px solid #ddd" }} />
                <button onClick={() => removeProductImage(i)} style={{ position: "absolute", top: -6, right: -6, width: 22, height: 22, borderRadius: "50%", background: "#dc2626", color: "#fff", border: "none", cursor: "pointer", fontSize: "0.7rem" }}>×</button>
                <div style={{ position: "absolute", bottom: 2, left: 4, fontSize: "0.7rem", color: "#fff", textShadow: "0 1px 2px rgba(0,0,0,0.6)" }}>{i === 0 ? "主图" : i === 1 ? "详情" : `图${i + 1}`}</div>
              </div>
            ))}
            {productImageUrls.length < 5 && (
              <label style={{ width: 80, height: 80, border: "2px dashed #ddd", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "#999", fontSize: "0.8rem" }}>
                <input type="file" accept="image/*" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadProductImage(f); e.target.value = ""; }} />
                + 加图
              </label>
            )}
          </div>
          <div style={{ fontSize: "0.78rem", color: "#999" }}>第 1 张作主图,后续作详情页/包装/多角度参考。每张 ≤ 10MB。</div>
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

        <Box label="③ 视频参数">
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>总时长</div>
              <select value={duration} onChange={e => setDuration(parseInt(e.target.value))} style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value={5}>5 秒(1 段)</option>
                <option value={10}>10 秒(2 段)</option>
                <option value={15}>15 秒(3 段)</option>
                <option value={30}>30 秒(6 段)</option>
              </select>
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
        </Box>

        {!analyzeResult && (
          <button onClick={analyze} disabled={analyzing || !productImageUrls.length} style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: analyzing ? "not-allowed" : "pointer", opacity: analyzing ? 0.6 : 1, marginBottom: "1rem" }}>
            {analyzing ? analyzeMsg : "🔍 AI 分析产品 + 出脚本(消耗 1 积分)"}
          </button>
        )}

        {analyzeResult && (
          <>
            <Box label={`④ AI 分析结果 · 品类:${analyzeResult.category}`}>
              {analyzeResult.selling_points.length > 0 && (
                <div style={{ background: "#f0f7fb", padding: "0.6rem 0.8rem", borderRadius: 8, marginBottom: 10 }}>
                  <strong style={{ fontSize: "0.85rem" }}>核心卖点:</strong>
                  <ul style={{ margin: "0.4rem 0 0", paddingLeft: "1.2rem", fontSize: "0.85rem" }}>
                    {analyzeResult.selling_points.map((p, i) => <li key={i}>{p}</li>)}
                  </ul>
                </div>
              )}
              {analyzeResult.scenes.map((sc, idx) => (
                <div key={sc.id || idx} style={{ borderTop: idx > 0 ? "1px solid #eee" : "none", paddingTop: idx > 0 ? "1rem" : 0, marginTop: idx > 0 ? "1rem" : 0 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                    <strong style={{ fontSize: "0.9rem" }}>镜 {sc.id || idx + 1}</strong>
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
              ))}
            </Box>

            {!resultVideoUrl && (
              <button onClick={generate} disabled={generating} style={{ background: "#dc2626", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: generating ? "not-allowed" : "pointer", opacity: generating ? 0.6 : 1, marginBottom: "1rem" }}>
                {generating ? generateMsg : `🎬 生成视频(消耗 ${analyzeResult.scenes.length * 5} 积分)`}
              </button>
            )}
          </>
        )}

        {resultVideoUrl && (
          <Box label="⑤ 生成结果">
            <video src={resultVideoUrl} controls style={{ width: "100%", maxWidth: 480, borderRadius: 10 }} />
            <div style={{ marginTop: 10 }}>
              <a href={resultVideoUrl} download style={{ color: "#0d8a3e", fontSize: "0.85rem" }}>⬇ 下载视频</a>
            </div>
          </Box>
        )}
      </main>
    </div>
  );
}
