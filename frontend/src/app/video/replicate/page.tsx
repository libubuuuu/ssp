"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";
import { serializeReplicateScenes } from "@/lib/scriptMarkdown";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface Scene {
  id: number;
  time_range: string;
  duration_sec: number;
  shot: string;
  action: string;
  framing: string;
  visual_prompt: string;
}

const ASPECT_OPTIONS = [
  { value: "auto", label: "自动(参考视频原比例)" },
  { value: "9:16", label: "9:16 竖版(抖音)" },
  { value: "16:9", label: "16:9 横版" },
  { value: "1:1", label: "1:1 正方" },
];

const ENGINE_OPTIONS = [
  { value: "catvton-pixverse",  label: "真模特试穿(穿戴类首选)⭐ 产品 100% 严守", desc: "cat-vton 真把你的产品穿到模特身上 · 产品颜色/材质/Logo 像素级保留 · 适合衣服/塑身衣/胸罩 · P209", color: "#dc2626" },
  { value: "pixverse-2step",    label: "动作复刻(双步)🎯手持类首选 · 产品 100% 严守", desc: "先用你产品图做 object swap 再换模特 · 适合手机/包/水杯/化妆品 · 手物 100% 贴合", color: "#1d4ed8" },
  { value: "pixverse-swap",     label: "动作复刻(单步,GPT 自由发挥)", desc: "GPT-Image 2 出图 + pixverse 复刻动作 · 产品颜色/形状会漂 · 仅作快速预览", color: "#888" },
  { value: "seedance-lite-i2v", label: "AI 自由生成",     desc: "AI 自由生成动作(不复刻参考视频)· 产品也会漂", color: "#aaa" },
];

function token() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

// P181(2026-05-08):视频复刻只允许服装大类(穿戴/鞋包/配饰)
const CLOTHING_CATEGORY_PREFIXES = ["服装", "鞋", "包", "配饰"];
function isClothingCategory(category: string): boolean {
  if (!category) return false;
  return CLOTHING_CATEGORY_PREFIXES.some(p => category.startsWith(p));
}

export default function ReplicatePage() {
  const router = useRouter();

  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [productFile, setProductFile] = useState<File | null>(null);
  const [productBackFile, setProductBackFile] = useState<File | null>(null);

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [productUrl, setProductUrl] = useState<string | null>(null);
  const [productBackUrl, setProductBackUrl] = useState<string | null>(null);

  const [scenes, setScenes] = useState<Scene[] | null>(null);
  const [detectedRatio, setDetectedRatio] = useState<string>("9:16");
  const [chosenRatio, setChosenRatio] = useState<string>("auto");
  const [chosenEngine, setChosenEngine] = useState<string>("catvton-pixverse");

  const [originalSpeech, setOriginalSpeech] = useState<string>("");
  const [speechAudioUrl, setSpeechAudioUrl] = useState<string>("");
  const [hasBackgroundMusic, setHasBackgroundMusic] = useState<boolean>(false);

  // P181(2026-05-08):VLM 提取的人物相貌 + 产品大类
  const [modelIdentity, setModelIdentity] = useState<string>("");
  const [productCategory, setProductCategory] = useState<string>("");

  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("");
  const [error, setError] = useState("");

  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<string>("");
  const [finalVideo, setFinalVideo] = useState<string | null>(null);

  // ---- 上传 ----
  const uploadFile = async (file: File, kind: "video" | "image"): Promise<string> => {
    const fd = new FormData();
    fd.append("file", file);
    const path = kind === "video" ? "/api/video/replicate/upload/video" : "/api/video/replicate/upload/image";
    const r = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token()}` },
      body: fd,
    });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    return d.video_url || d.image_url;
  };

  const onPickVideo = async (f: File | null) => {
    if (!f) return;
    setVideoFile(f); setError("");
    setLoading(true); setLoadingMsg("上传参考视频...");
    try {
      const u = await uploadFile(f, "video");
      setVideoUrl(u);
    } catch (e) { setError(errMsg(e, "上传视频失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };
  const onPickProduct = async (f: File | null) => {
    if (!f) return;
    setProductFile(f); setError("");
    setLoading(true); setLoadingMsg("上传产品图...");
    try {
      const u = await uploadFile(f, "image");
      setProductUrl(u);
    } catch (e) { setError(errMsg(e, "上传产品图失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };
  const onPickProductBack = async (f: File | null) => {
    if (!f) return;
    setProductBackFile(f); setError("");
    setLoading(true); setLoadingMsg("上传产品反面/侧面图...");
    try {
      const u = await uploadFile(f, "image");
      setProductBackUrl(u);
    } catch (e) { setError(errMsg(e, "上传产品反面/侧面图失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };

  // ---- 分析(异步:推 job + 轮询)----
  const analyze = async () => {
    if (!videoUrl) return;
    setError(""); setLoading(true); setLoadingMsg("提交分析任务...");
    try {
      const r = await fetch(`${API_BASE}/api/video/replicate/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({ video_url: videoUrl }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      const aid = d.analyze_job_id;
      if (!aid) throw new Error("没拿到 analyze_job_id");
      adjustLocalUserCredits(-1);
      setLoadingMsg("AI 看视频中(qwen-vl,30-180s)...");
      // 轮询 status
      let elapsed = 0;
      const interval = setInterval(async () => {
        elapsed += 6;
        try {
          const sr = await fetch(`${API_BASE}/api/video/replicate/analyze/status/${aid}`, {
            headers: { Authorization: `Bearer ${token()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(interval);
            setScenes(sd.scenes || []);
            setDetectedRatio(sd.detected_aspect_ratio || "9:16");
            setOriginalSpeech(sd.original_speech || "");
            setSpeechAudioUrl(sd.speech_audio_url || "");
            setHasBackgroundMusic(!!sd.has_background_music);
            // P181:VLM 提取的人物 + 产品大类
            setModelIdentity(sd.model_identity || "");
            setProductCategory(sd.product_category || "");
            setLoading(false); setLoadingMsg("");
          } else if (sd.status === "failed") {
            clearInterval(interval);
            setError(sd.error || "分析失败");
            setLoading(false); setLoadingMsg("");
          } else {
            setLoadingMsg(`AI 看视频中... 已用 ${elapsed}s`);
          }
        } catch {}
      }, 6000);
    } catch (e) { setError(errMsg(e, "提交分析失败")); setLoading(false); setLoadingMsg(""); }
  };

  // ---- 编辑 scene visual_prompt ----
  const updateScene = (idx: number, key: keyof Scene, val: string) => {
    if (!scenes) return;
    const ns = scenes.map((s, i) => i === idx ? { ...s, [key]: val } : s);
    setScenes(ns);
  };

  // ---- 生成 ----
  const generate = async () => {
    if (!scenes || !productUrl || !videoUrl) return;
    const ratio = chosenRatio === "auto" ? detectedRatio : chosenRatio;
    setError(""); setLoading(true); setLoadingMsg("提交生成任务...");
    try {
      const r = await fetch(`${API_BASE}/api/video/replicate/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({
          product_image_url: productUrl,
          product_back_image_url: productBackUrl,
          reference_video_url: videoUrl,
          script: { scenes, overall_setting: "", model_description: modelIdentity || "A professional commercial model" },
          aspect_ratio: ratio,
          engine: chosenEngine,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setJobId(d.job_id);
      setJobStatus("pending");
      adjustLocalUserCredits(-(d.cost || 0));
      setLoadingMsg(`已提交,预计 ${scenes.length * 9} 分钟出片(每段约 9 分钟)`);
      // 轮询
      const poll = setInterval(async () => {
        try {
          const jr = await fetch(`${API_BASE}/api/jobs/${d.job_id}`, { headers: { Authorization: `Bearer ${token()}` } });
          if (!jr.ok) return;
          const jd = await jr.json();
          setJobStatus(jd.status);
          if (jd.status === "completed") {
            clearInterval(poll);
            setLoading(false); setLoadingMsg("");
            setFinalVideo(jd.result?.video_url || null);
          } else if (jd.status === "failed") {
            clearInterval(poll);
            setLoading(false); setLoadingMsg("");
            setError(jd.error || "生成失败");
          }
        } catch {}
      }, 8000);
    } catch (e) {
      setError(errMsg(e, "提交失败"));
      setLoading(false); setLoadingMsg("");
    }
  };

  // ---- UI ----
  const Box = ({ children, label }: { children: React.ReactNode; label: string }) => (
    <div style={{ background: "#fff", borderRadius: 14, padding: "1.2rem 1.4rem", marginBottom: "1.2rem", boxShadow: "0 2px 8px rgba(0,0,0,0.04)" }}>
      <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.6rem", fontWeight: 500 }}>{label}</div>
      {children}
    </div>
  );

  const FilePick = ({ file, accept, onPick, label, preview }: { file: File | null; accept: string; onPick: (f: File | null) => void; label: string; preview?: React.ReactNode }) => (
    <label style={{ display: "block", border: "2px dashed #ddd", borderRadius: 10, padding: "1rem", textAlign: "center", cursor: "pointer", background: file ? "#f9f7f2" : "#fff" }}>
      <input type="file" accept={accept} style={{ display: "none" }} onChange={e => onPick(e.target.files?.[0] || null)} />
      {file ? (
        <div>
          <div style={{ fontSize: "0.85rem", color: "#0d0d0d", marginBottom: 6 }}>✓ {file.name}</div>
          {preview}
        </div>
      ) : (
        <div style={{ color: "#999", fontSize: "0.9rem" }}>{label}</div>
      )}
    </label>
  );

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>AI 创作工具</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>视频<span style={{ fontStyle: "italic" }}> 复刻</span></h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            上传参考视频 + 产品图(可选反面/侧面) → AI 拆分镜 → 模特由 AI 自动生成 → 按你的产品复刻
          </div>
          <div style={{ marginTop: 8, padding: "0.6rem 0.9rem", background: "#fffbf0", border: "1px solid #f5e6a8", borderRadius: 8, fontSize: "0.85rem", color: "#7a5800", display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: "1.1rem" }}>⚠️</span>
            <span><strong>这个功能比较适合复刻服装类产品</strong>(衣服/塑身衣/胸罩等穿戴品)。手持产品(手机/水杯/化妆品)选"动作复刻(双步)",数码小配件可以试 AI 自由生成。</span>
          </div>
        </div>

        {error && (
          <div style={{ background: "#fff3f3", border: "1px solid #fcc", color: "#c33", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.9rem" }}>{error}</div>
        )}

        <Box label="① 参考视频(必)">
          <FilePick file={videoFile} accept="video/*" onPick={onPickVideo} label="点击上传参考视频(MP4/MOV,≤100MB)"
            preview={videoUrl ? <video src={videoUrl} controls style={{ maxWidth: 280, maxHeight: 200, marginTop: 6, borderRadius: 8 }} /> : null} />
        </Box>

        <Box label="② 产品图(必)">
          <FilePick file={productFile} accept="image/*" onPick={onPickProduct} label="点击上传产品图(白底最佳)"
            preview={productUrl ? <img src={productUrl} alt="" style={{ maxWidth: 200, maxHeight: 200, marginTop: 6, borderRadius: 8 }} /> : null} />
        </Box>

        <Box label="③ 产品反面/侧面图(可选 — 锁住反面材质/logo/纹理)">
          <FilePick file={productBackFile} accept="image/*" onPick={onPickProductBack} label="点击上传产品反面或侧面图(可选,二选一)"
            preview={productBackUrl ? <img src={productBackUrl} alt="" style={{ maxWidth: 200, maxHeight: 200, marginTop: 6, borderRadius: 8 }} /> : null} />
        </Box>

        {videoUrl && !scenes && (
          <button onClick={analyze} disabled={loading}
            style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, marginBottom: "1rem" }}>
            {loading ? loadingMsg || "分析中..." : "AI 分析参考视频(消耗 1 积分)"}
          </button>
        )}

        {scenes && (originalSpeech || speechAudioUrl) && (
          <Box label={`原视频口播提取${hasBackgroundMusic ? " · 检测到背景音乐(已分离)" : " · 无背景音乐"}`}>
            {originalSpeech ? (
              <div style={{ marginBottom: speechAudioUrl ? 10 : 0 }}>
                <div style={{ fontSize: "0.8rem", color: "#666", marginBottom: 4 }}>识别到的口播文字</div>
                <div style={{ background: "#f9f7f2", padding: "0.7rem 0.9rem", borderRadius: 8, fontSize: "0.88rem", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                  {originalSpeech}
                </div>
                <button
                  onClick={() => navigator.clipboard.writeText(originalSpeech)}
                  style={{ marginTop: 6, background: "transparent", border: "1px solid #ddd", padding: "0.35rem 0.7rem", borderRadius: 6, fontSize: "0.78rem", cursor: "pointer" }}>
                  复制文字
                </button>
              </div>
            ) : (
              <div style={{ fontSize: "0.85rem", color: "#999" }}>原视频未识别到说话内容(可能纯展示/纯音乐)</div>
            )}
            {speechAudioUrl && (
              <div>
                <div style={{ fontSize: "0.8rem", color: "#666", marginBottom: 4 }}>纯人声音轨(已剥离背景音乐)</div>
                <audio src={speechAudioUrl} controls style={{ width: "100%", maxWidth: 480 }} />
              </div>
            )}
          </Box>
        )}

        {/* P181:产品大类不是服装 → 拦截,引导去 ad-video */}
        {scenes && productCategory && !isClothingCategory(productCategory) && (
          <Box label="⚠️ 产品类目不匹配 — 视频复刻仅支持服装类产品">
            <div style={{ background: "#fff3f3", border: "1px solid #fcc", color: "#8a3a3a", padding: "1rem 1.2rem", borderRadius: 10, marginBottom: 12 }}>
              <div style={{ fontSize: "0.95rem", fontWeight: 500, marginBottom: 6 }}>
                AI 检测到你的产品类目:<strong>{productCategory}</strong>
              </div>
              <div style={{ fontSize: "0.85rem", lineHeight: 1.6 }}>
                视频复刻功能针对<strong>服装/鞋/包/配饰</strong>类产品做了专项优化(模特换装、动作复刻)。
                <br />
                你的产品不在覆盖范围 — 强行生成效果会很差(产品形状失真 / 手物贴合差)。
                <br /><br />
                <strong>推荐改用「AI 带货视频」</strong>功能 — 那个功能更适合数码/美妆/家居/食品/日用类,直接出口播带货视频,不需要参考视频。
              </div>
            </div>
            <button onClick={() => router.push("/ad-video")}
              style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.7rem 1.4rem", borderRadius: 8, fontSize: "0.9rem", cursor: "pointer", marginRight: 8 }}>
              去 AI 带货视频 →
            </button>
            <button onClick={() => { setScenes(null); setProductCategory(""); setVideoFile(null); setVideoUrl(null); }}
              style={{ background: "transparent", color: "#666", border: "1px solid #ddd", padding: "0.7rem 1.4rem", borderRadius: 8, fontSize: "0.9rem", cursor: "pointer" }}>
              重新上传别的视频
            </button>
          </Box>
        )}

        {scenes && (!productCategory || isClothingCategory(productCategory)) && (
          <>
            {productCategory && (
              <div style={{ marginBottom: 12, padding: "0.6rem 0.9rem", background: "#f0f7fb", border: "1px solid #b8d8ee", borderRadius: 8, fontSize: "0.85rem", color: "#1a4068" }}>
                ✓ 产品类目:<strong>{productCategory}</strong> — 适配视频复刻
                {modelIdentity && (
                  <div style={{ marginTop: 4, fontSize: "0.78rem", color: "#456" }}>
                    AI 提取的模特特征(后续 GPT-Image 2 出图会按此还原):{modelIdentity.length > 100 ? modelIdentity.slice(0, 100) + "..." : modelIdentity}
                  </div>
                )}
              </div>
            )}
            <Box label={`④ 输出比例(检测到 ${detectedRatio})`}>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {ASPECT_OPTIONS.map(o => (
                  <label key={o.value} style={{
                    flex: "1 1 200px", padding: "0.6rem 0.8rem",
                    border: chosenRatio === o.value ? "2px solid #0d0d0d" : "1px solid #ddd",
                    background: chosenRatio === o.value ? "#f9f7f2" : "#fff",
                    borderRadius: 10, cursor: "pointer",
                  }}>
                    <input type="radio" name="ratio" value={o.value} checked={chosenRatio === o.value}
                      onChange={() => setChosenRatio(o.value)} style={{ marginRight: 8 }} />
                    {o.label}
                  </label>
                ))}
              </div>
            </Box>

            <Box label="⑤ 视频生成引擎">
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {ENGINE_OPTIONS.map(o => (
                  <label key={o.value} style={{
                    padding: "0.7rem 0.9rem",
                    border: chosenEngine === o.value ? "2px solid #0d0d0d" : "1px solid #ddd",
                    background: chosenEngine === o.value ? "#f9f7f2" : "#fff",
                    borderRadius: 10, cursor: "pointer",
                  }}>
                    <input type="radio" name="engine" value={o.value} checked={chosenEngine === o.value}
                      onChange={() => setChosenEngine(o.value)} style={{ marginRight: 8 }} />
                    <strong style={{ color: o.color || "#0d0d0d" }}>{o.label}</strong>
                    <div style={{ fontSize: "0.78rem", color: "#666", marginTop: 4 }}>{o.desc}</div>
                  </label>
                ))}
              </div>
            </Box>

            <Box label={`⑥ AI 拆出 ${scenes.length} 个分镜(可微调每段 prompt)`}>
              {scenes.map((sc, idx) => (
                <div key={sc.id} style={{ borderTop: idx > 0 ? "1px solid #eee" : "none", paddingTop: idx > 0 ? "0.8rem" : 0, marginTop: idx > 0 ? "0.8rem" : 0 }}>
                  <div style={{ fontSize: "0.85rem", fontWeight: 500, marginBottom: 6 }}>
                    镜{sc.id} · {sc.time_range} · {sc.duration_sec}s · 景别={sc.shot} · 动作={sc.action}
                  </div>
                  <textarea value={sc.visual_prompt} onChange={e => updateScene(idx, "visual_prompt", e.target.value)} rows={3}
                    style={{ width: "100%", padding: "0.6rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.85rem", fontFamily: "monospace", resize: "vertical" }} />
                </div>
              ))}
              <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid #eee", display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  onClick={() => {
                    const md = serializeReplicateScenes(scenes, {
                      total_duration_sec: scenes.reduce((a, s) => a + (s.duration_sec || 5), 0),
                      original_speech: originalSpeech || undefined,
                    });
                    navigator.clipboard.writeText(md);
                    alert("已复制 markdown 脚本,可粘贴到 AI 带货视频(/ad-video)使用");
                  }}
                  style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.55rem 1rem", borderRadius: 8, fontSize: "0.82rem", cursor: "pointer" }}>
                  📋 复制脚本(markdown)
                </button>
                <button
                  onClick={() => {
                    const md = serializeReplicateScenes(scenes, {
                      total_duration_sec: scenes.reduce((a, s) => a + (s.duration_sec || 5), 0),
                      original_speech: originalSpeech || undefined,
                    });
                    const blob = new Blob([md], { type: "text/markdown" });
                    const a = document.createElement("a");
                    a.href = URL.createObjectURL(blob);
                    a.download = "video-script.md";
                    a.click();
                  }}
                  style={{ background: "transparent", color: "#0d0d0d", border: "1px solid #ddd", padding: "0.55rem 1rem", borderRadius: 8, fontSize: "0.82rem", cursor: "pointer" }}>
                  ⬇ 下载 .md
                </button>
              </div>
            </Box>

            {!jobId && (
              <button onClick={generate} disabled={loading || !productUrl}
                style={{ background: productUrl ? "#0d0d0d" : "#ccc", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: (loading || !productUrl) ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1 }}>
                {loading ? loadingMsg || "提交中..." : `生成复刻视频(${Math.ceil(scenes.reduce((a, s) => a + s.duration_sec, 0) * 1.5)} 积分)`}
              </button>
            )}
            {!productUrl && (
              <div style={{ fontSize: "0.8rem", color: "#c33", marginTop: 6 }}>需要先上传产品图</div>
            )}
          </>
        )}

        {jobId && (
          <Box label={`⑦ 任务状态:${jobStatus}`}>
            {jobStatus !== "completed" && (
              <div style={{ color: "#666", fontSize: "0.85rem" }}>
                {loadingMsg || "后台生成中,可关闭页面去做别的,任务在后台跑..."}
              </div>
            )}
            {finalVideo && (
              <div>
                <video src={finalVideo} controls style={{ width: "100%", maxWidth: 480, borderRadius: 10 }} />
                <div style={{ marginTop: 10 }}>
                  <a href={finalVideo} download style={{ background: "#0d0d0d", color: "#fff", padding: "0.6rem 1.2rem", borderRadius: 8, textDecoration: "none", fontSize: "0.85rem" }}>下载视频</a>
                </div>
              </div>
            )}
          </Box>
        )}
      </main>
    </div>
  );
}
