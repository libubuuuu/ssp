"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
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
  framing: string;
  visual_prompt: string;
}

const ASPECT_OPTIONS = [
  { value: "auto", label: "自动(参考视频原比例)" },
  { value: "9:16", label: "9:16 竖版(抖音)" },
  { value: "16:9", label: "16:9 横版" },
  { value: "1:1", label: "1:1 正方" },
];

function token() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

export default function ReplicatePage() {
  const router = useRouter();

  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [productFile, setProductFile] = useState<File | null>(null);
  const [modelFile, setModelFile] = useState<File | null>(null);

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [productUrl, setProductUrl] = useState<string | null>(null);
  const [modelUrl, setModelUrl] = useState<string | null>(null);

  const [scenes, setScenes] = useState<Scene[] | null>(null);
  const [detectedRatio, setDetectedRatio] = useState<string>("9:16");
  const [chosenRatio, setChosenRatio] = useState<string>("auto");

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
  const onPickModel = async (f: File | null) => {
    if (!f) return;
    setModelFile(f); setError("");
    setLoading(true); setLoadingMsg("上传模特图...");
    try {
      const u = await uploadFile(f, "image");
      setModelUrl(u);
    } catch (e) { setError(errMsg(e, "上传模特图失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };

  // ---- 分析 ----
  const analyze = async () => {
    if (!videoUrl) return;
    setError(""); setLoading(true); setLoadingMsg("AI 分析参考视频(可能 30-60s)...");
    try {
      const r = await fetch(`${API_BASE}/api/video/replicate/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({ video_url: videoUrl }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setScenes(d.scenes || []);
      setDetectedRatio(d.detected_aspect_ratio || "9:16");
      adjustLocalUserCredits(-1);
    } catch (e) { setError(errMsg(e, "分析失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
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
          model_image_url: modelUrl,
          reference_video_url: videoUrl,
          script: { scenes, overall_setting: "", model_description: "A professional commercial model" },
          aspect_ratio: ratio,
          engine: "aliyun-wan2.7-r2v",
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
            上传参考视频 + 你的产品图(模特图可选) → AI 拆分镜 → 按你的产品复刻一条同风格视频
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

        <Box label="③ 模特图(可选 — 让 AI 知道是谁出镜)">
          <FilePick file={modelFile} accept="image/*" onPick={onPickModel} label="点击上传模特图(可选)"
            preview={modelUrl ? <img src={modelUrl} alt="" style={{ maxWidth: 200, maxHeight: 200, marginTop: 6, borderRadius: 8 }} /> : null} />
        </Box>

        {videoUrl && !scenes && (
          <button onClick={analyze} disabled={loading}
            style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, marginBottom: "1rem" }}>
            {loading ? loadingMsg || "分析中..." : "AI 分析参考视频(消耗 1 积分)"}
          </button>
        )}

        {scenes && (
          <>
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

            <Box label={`⑤ AI 拆出 ${scenes.length} 个分镜(可微调每段 prompt)`}>
              {scenes.map((sc, idx) => (
                <div key={sc.id} style={{ borderTop: idx > 0 ? "1px solid #eee" : "none", paddingTop: idx > 0 ? "0.8rem" : 0, marginTop: idx > 0 ? "0.8rem" : 0 }}>
                  <div style={{ fontSize: "0.85rem", fontWeight: 500, marginBottom: 6 }}>
                    镜{sc.id} · {sc.time_range} · {sc.duration_sec}s · 景别={sc.shot} · 动作={sc.action}
                  </div>
                  <textarea value={sc.visual_prompt} onChange={e => updateScene(idx, "visual_prompt", e.target.value)} rows={3}
                    style={{ width: "100%", padding: "0.6rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.85rem", fontFamily: "monospace", resize: "vertical" }} />
                </div>
              ))}
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
          <Box label={`⑥ 任务状态:${jobStatus}`}>
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
