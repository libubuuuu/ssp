"use client";
import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

function token() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

interface ImageItem {
  url: string;
  filename: string;
}

export default function VideoClonePage() {
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [videoDuration, setVideoDuration] = useState<number>(0);
  const [imageItems, setImageItems] = useState<ImageItem[]>([]);
  const [prompt, setPrompt] = useState<string>("用 @Image1 的人物按 @Video1 的镜头动作复刻,产品换成 @Image2");
  const [duration, setDuration] = useState<string>("5");
  const [aspectRatio, setAspectRatio] = useState<string>("9:16");
  const [count, setCount] = useState<number>(1);
  const [generateAudio, setGenerateAudio] = useState<boolean>(true);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genMsg, setGenMsg] = useState("");
  const [error, setError] = useState("");
  const [resultUrls, setResultUrls] = useState<string[]>([]);
  const [priceInfo, setPriceInfo] = useState<{ price_rmb: number; credits: number } | null>(null);

  // 实时刷价格
  useEffect(() => {
    const dur = duration === "auto" ? 10 : parseInt(duration);
    fetch(`${API_BASE}/api/video/clone/price?duration=${dur}&count=${count}`, {
      headers: { Authorization: `Bearer ${token()}` },
    })
      .then(r => r.json())
      .then(d => setPriceInfo(d))
      .catch(() => {});
  }, [duration, count]);

  // P200 风格的 XHR + 4 次退避重试上传
  const uploadWithRetry = (
    url: string, file: File, tk: string, onProgress?: (pct: number) => void,
  ): Promise<{ status: number; ok: boolean; text: string }> => {
    const tryOnce = () => new Promise<{ status: number; ok: boolean; text: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);
      xhr.setRequestHeader("Authorization", `Bearer ${tk}`);
      xhr.timeout = 5 * 60 * 1000;
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable && onProgress) onProgress(Math.round(ev.loaded / ev.total * 100));
      };
      xhr.onload = () => resolve({ status: xhr.status, ok: xhr.status >= 200 && xhr.status < 300, text: xhr.responseText });
      xhr.onerror = () => reject(new Error("network"));
      xhr.ontimeout = () => reject(new Error("timeout"));
      const fd = new FormData();
      fd.append("file", file);
      xhr.send(fd);
    });
    return (async () => {
      let lastErr: unknown = null;
      for (let i = 1; i <= 4; i++) {
        try {
          const r = await tryOnce();
          if (r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429) return r;
          if (r.ok) return r;
          lastErr = new Error(`HTTP ${r.status}`);
        } catch (e) { lastErr = e; }
        if (i < 4) {
          const wait = Math.min(1000 * Math.pow(2, i - 1), 8000);
          setUploadMsg(`上传断了,${wait/1000}s 后重试 ${i + 1}/4...`);
          await new Promise(r => setTimeout(r, wait));
        }
      }
      throw lastErr instanceof Error ? lastErr : new Error("上传失败");
    })();
  };

  const onPickVideo = async (f: File) => {
    setError(""); setVideoFile(f);
    setUploading(true); setUploadMsg("上传参考视频...");
    try {
      const r = await uploadWithRetry(
        `${API_BASE}/api/video/clone/upload/video`, f, token(),
        (pct) => setUploadMsg(`上传视频... ${pct}%`),
      );
      if (!r.ok) {
        const d = (() => { try { return JSON.parse(r.text); } catch { return {}; } })();
        const detail = d?.detail || `HTTP ${r.status}`;
        throw new Error(detail);
      }
      const d = JSON.parse(r.text);
      setVideoUrl(d.video_url);
      setVideoDuration(d.duration_sec || 0);
    } catch (e) { setError(errMsg(e, "视频上传失败")); setVideoFile(null); }
    finally { setUploading(false); setUploadMsg(""); }
  };

  const onPickImage = async (f: File) => {
    if (imageItems.length >= 9) { setError("最多 9 张参考图"); return; }
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`${API_BASE}/api/video/clone/upload/image`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setImageItems(items => [...items, { url: d.image_url, filename: f.name }]);
    } catch (e) { setError(errMsg(e, "图片上传失败")); }
  };

  const removeImage = (idx: number) => {
    setImageItems(items => items.filter((_, i) => i !== idx));
  };

  const generate = async () => {
    if (!videoUrl) { setError("请先上传参考视频"); return; }
    if (!prompt.trim()) { setError("请输入 prompt"); return; }
    setError(""); setGenerating(true); setResultUrls([]);
    setGenMsg("提交任务...");
    try {
      const r = await fetch(`${API_BASE}/api/video/clone/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({
          prompt,
          reference_video_url: videoUrl,
          reference_image_urls: imageItems.map(it => it.url),
          duration,
          aspect_ratio: aspectRatio,
          generate_audio: generateAudio,
          count,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      const jid = d.job_id;
      adjustLocalUserCredits(-(d.cost_credits || 0));
      const estimate = d.estimated_time_sec || 90;
      setGenMsg(`生成中(预计 ${estimate}s,job=${jid})...`);
      let elapsed = 0;
      const interval = setInterval(async () => {
        elapsed += 5;
        try {
          const sr = await fetch(`${API_BASE}/api/jobs/${jid}`, {
            headers: { Authorization: `Bearer ${token()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(interval);
            const urls = sd.result?.video_urls || (sd.result?.video_url ? [sd.result.video_url] : []);
            setResultUrls(urls);
            setGenerating(false); setGenMsg("");
          } else if (sd.status === "failed") {
            clearInterval(interval);
            setError(sd.error || "生成失败");
            setGenerating(false); setGenMsg("");
          } else {
            setGenMsg(`生成中... 已 ${elapsed}s / 预计 ${estimate}s`);
          }
        } catch {}
      }, 5000);
    } catch (e) { setError(errMsg(e, "提交失败")); setGenerating(false); setGenMsg(""); }
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
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>视频<span style={{ fontStyle: "italic" }}> 复刻</span> · Seedance 2.0 Fast r2v</h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            上传 1 个参考视频 + 0-9 张参考图 → 用 prompt 引导(@Video1 / @Image1 占位符)→ 真复刻动作 + 替换场景/人物/产品
          </div>
        </div>

        {error && (
          <div style={{ background: "#fff3f3", border: "1px solid #fcc", color: "#c33", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.9rem", whiteSpace: "pre-wrap" }}>{error}</div>
        )}

        <Box label="① 参考视频(必填,3-15s,≤50MB)">
          <label style={{ display: "block", border: "2px dashed #ddd", borderRadius: 10, padding: "1rem", textAlign: "center", cursor: "pointer", background: videoFile ? "#f9f7f2" : "#fff" }}>
            <input type="file" accept="video/mp4,video/quicktime,video/webm,video/x-m4v,image/gif" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) onPickVideo(f); }} />
            {videoFile ? (
              <div>
                <div style={{ fontSize: "0.85rem", color: "#0d0d0d", marginBottom: 6 }}>✓ {videoFile.name} {videoDuration > 0 && `(${videoDuration.toFixed(1)}s)`}</div>
                {uploading && <div style={{ fontSize: "0.78rem", color: "#888" }}>{uploadMsg}</div>}
                {videoUrl && !uploading && <video src={videoUrl} controls style={{ maxWidth: 320, maxHeight: 220, marginTop: 8, borderRadius: 8 }} />}
              </div>
            ) : (
              <div style={{ color: "#999", fontSize: "0.9rem" }}>点击上传参考视频(MP4/MOV/WebM/M4V/GIF, 3-15s, ≤50MB)</div>
            )}
          </label>
        </Box>

        <Box label="② 参考图(0-9 张,可选,自动按 @Image1 / @Image2 编号)">
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {imageItems.map((it, i) => (
              <div key={i} style={{ position: "relative" }}>
                <img src={it.url} alt={`@Image${i + 1}`} style={{ width: 80, height: 80, objectFit: "cover", borderRadius: 8, border: "1px solid #ddd" }} />
                <button onClick={() => removeImage(i)} style={{ position: "absolute", top: -6, right: -6, width: 22, height: 22, borderRadius: "50%", background: "#dc2626", color: "#fff", border: "none", cursor: "pointer", fontSize: "0.7rem" }}>×</button>
                <div style={{ position: "absolute", bottom: 2, left: 4, fontSize: "0.7rem", color: "#fff", textShadow: "0 1px 2px rgba(0,0,0,0.6)" }}>@Image{i + 1}</div>
              </div>
            ))}
            {imageItems.length < 9 && (
              <label style={{ width: 80, height: 80, border: "2px dashed #ddd", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "#999", fontSize: "0.8rem" }}>
                <input type="file" accept="image/jpeg,image/png,image/webp,image/avif" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) { onPickImage(f); e.target.value = ""; } }} />
                + 加图
              </label>
            )}
          </div>
        </Box>

        <Box label="③ Prompt(必填,用 @Video1 / @Image1 / @Image2 占位符)">
          <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={4}
            placeholder="例:用 @Image1 的人物按 @Video1 的镜头动作复刻,产品换成 @Image2,场景换成厨房"
            style={{ width: "100%", padding: "0.7rem 0.9rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.9rem", lineHeight: 1.5, fontFamily: "monospace", resize: "vertical" }} />
        </Box>

        <Box label="④ 视频参数">
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>时长</div>
              <select value={duration} onChange={e => setDuration(e.target.value)} style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value="5">5 秒</option>
                <option value="10">10 秒</option>
                <option value="15">15 秒</option>
                <option value="auto">自动(按参考视频)</option>
              </select>
            </div>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>宽高比</div>
              <select value={aspectRatio} onChange={e => setAspectRatio(e.target.value)} style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value="9:16">9:16 竖版(抖音)</option>
                <option value="16:9">16:9 横版</option>
                <option value="1:1">1:1 方形</option>
                <option value="auto">auto</option>
              </select>
            </div>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>生成数量</div>
              <select value={count} onChange={e => setCount(parseInt(e.target.value))} style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value={1}>1 条</option>
                <option value={2}>2 条</option>
                <option value={3}>3 条</option>
                <option value={4}>4 条</option>
              </select>
            </div>
            <label style={{ fontSize: "0.85rem", display: "flex", alignItems: "center", gap: 6, marginTop: 24 }}>
              <input type="checkbox" checked={generateAudio} onChange={e => setGenerateAudio(e.target.checked)} />
              生成音频(免费送)
            </label>
          </div>
          {priceInfo && (
            <div style={{ background: "#f0f7fb", padding: "0.6rem 0.8rem", borderRadius: 8, fontSize: "0.85rem", color: "#456" }}>
              💰 预估:¥{priceInfo.price_rmb} · 消耗 {priceInfo.credits} 积分(单价 $0.1452/秒)
            </div>
          )}
        </Box>

        {!resultUrls.length && (
          <button onClick={generate} disabled={generating || !videoUrl || !prompt.trim()}
            style={{ background: "#dc2626", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: generating ? "not-allowed" : "pointer", opacity: (generating || !videoUrl) ? 0.5 : 1, marginBottom: "1rem" }}>
            {generating ? genMsg : `🎬 开始生成${priceInfo ? `(${priceInfo.credits} 积分)` : ""}`}
          </button>
        )}

        {resultUrls.length > 0 && (
          <Box label={`⑤ 生成结果(${resultUrls.length} 条)`}>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              {resultUrls.map((u, i) => (
                <div key={i} style={{ flex: "1 1 280px", maxWidth: 360 }}>
                  <video src={u} controls style={{ width: "100%", borderRadius: 10 }} />
                  <div style={{ marginTop: 6 }}>
                    <a href={u} download style={{ color: "#0d8a3e", fontSize: "0.85rem" }}>⬇ 下载视频 {i + 1}</a>
                  </div>
                </div>
              ))}
            </div>
            <button onClick={() => { setResultUrls([]); setVideoUrl(""); setVideoFile(null); setImageItems([]); }}
              style={{ marginTop: 12, background: "transparent", color: "#0d0d0d", border: "1px solid #ddd", padding: "0.6rem 1.2rem", borderRadius: 8, fontSize: "0.85rem", cursor: "pointer" }}>
              再来一次
            </button>
          </Box>
        )}
      </main>
    </div>
  );
}
