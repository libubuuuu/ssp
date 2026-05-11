"use client";
import { useState } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";
import { serializeReplicateScenes, distributeSpeechToScenes } from "@/lib/scriptMarkdown";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface Scene {
  id: number;
  time_range: string;
  duration_sec: number;
  shot: string;
  action: string;
  framing: string;
  visual_prompt: string;
  speech?: string;
}

function token() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") ?? "";
}

// Box 组件 hoist 到模块顶层(内联会触发 React remount → IME 断码)
const Box = ({ children, label }: { children: React.ReactNode; label: string }) => (
  <div style={{ background: "#fff", borderRadius: 14, padding: "1.2rem 1.4rem", marginBottom: "1.2rem", boxShadow: "0 2px 8px rgba(0,0,0,0.04)" }}>
    <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.6rem", fontWeight: 500 }}>{label}</div>
    {children}
  </div>
);

export default function VideoFrameExtractPage() {
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [scenes, setScenes] = useState<Scene[] | null>(null);
  const [gridUrl, setGridUrl] = useState<string>("");
  const [detectedRatio, setDetectedRatio] = useState<string>("9:16");
  const [originalSpeech, setOriginalSpeech] = useState<string>("");
  const [speechAudioUrl, setSpeechAudioUrl] = useState<string>("");
  const [hasBackgroundMusic, setHasBackgroundMusic] = useState<boolean>(false);
  const [overallSetting, setOverallSetting] = useState<string>("");

  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  // 上传视频(XHR + 4 次指数退避 retry,跟 /video/extract 同一套)
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
      xhr.onabort = () => reject(new Error("abort"));
      const fd = new FormData();
      fd.append("file", file);
      xhr.send(fd);
    });
    return (async () => {
      const max = 4;
      let lastErr: unknown = null;
      for (let i = 1; i <= max; i++) {
        try {
          const r = await tryOnce();
          if (r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429) return r;
          if (r.ok) return r;
          lastErr = new Error(`HTTP ${r.status}`);
        } catch (e) { lastErr = e; }
        if (i < max) {
          const wait = Math.min(1000 * Math.pow(2, i - 1), 8000);
          setLoadingMsg(`上传断了,${wait/1000}s 后重试(第 ${i + 1} 次,共 ${max} 次)...`);
          await new Promise((r) => setTimeout(r, wait));
        }
      }
      throw lastErr instanceof Error ? lastErr : new Error("上传失败");
    })();
  };

  const onPickVideo = async (f: File | null) => {
    if (!f) return;
    setVideoFile(f); setError(""); setScenes(null); setGridUrl(""); setOriginalSpeech(""); setSpeechAudioUrl("");
    setLoading(true); setLoadingMsg("上传视频...");
    try {
      const r = await uploadWithRetry(
        `${API_BASE}/api/video/frame-extract/upload/video`,
        f,
        token(),
        (pct) => setLoadingMsg(`上传视频... ${pct}%`),
      );
      if (!r.ok) {
        if (r.status === 401) throw new Error("登录已过期,请刷新页面重新登录");
        if (r.status === 413) throw new Error("视频太大(>100MB)");
        if (r.status === 415) throw new Error("视频格式不支持(请用 MP4/MOV/WebM)");
        let msg = `上传失败 HTTP ${r.status}`;
        try {
          const d = JSON.parse(r.text);
          if (d?.detail) msg = String(d.detail);
        } catch {}
        throw new Error(msg);
      }
      const d = JSON.parse(r.text);
      setVideoUrl(d.video_url);
    } catch (e) { setError(errMsg(e, "上传视频失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };

  const extract = async () => {
    if (!videoUrl) return;
    setError(""); setLoading(true); setLoadingMsg("提交分析任务...");
    try {
      const r = await fetch(`${API_BASE}/api/video/frame-extract/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({ video_url: videoUrl }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      const aid = d.analyze_job_id;
      if (!aid) throw new Error("没拿到 analyze_job_id");
      adjustLocalUserCredits(-1);
      setLoadingMsg("AI 分析中(PySceneDetect 切镜头 + qwen-vl 看九宫格 + wizper 转口播,40-120s)...");
      let elapsed = 0;
      const interval = setInterval(async () => {
        elapsed += 6;
        try {
          const sr = await fetch(`${API_BASE}/api/video/frame-extract/analyze/status/${aid}`, {
            headers: { Authorization: `Bearer ${token()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(interval);
            const rawScenes: Scene[] = sd.scenes ?? [];
            const fullSpeech = sd.original_speech ?? "";
            let scenesWithSpeech: Scene[] = rawScenes;
            const hasBackendSpeech = rawScenes.some(s => (s.speech ?? "").trim().length > 0);
            if (hasBackendSpeech) {
              scenesWithSpeech = rawScenes;
            } else if (fullSpeech && rawScenes.length > 0) {
              const distributed = distributeSpeechToScenes(fullSpeech, rawScenes);
              scenesWithSpeech = rawScenes.map((s: Scene, i: number) => ({ ...s, speech: distributed[i] ?? "" }));
            }
            setScenes(scenesWithSpeech);
            setGridUrl(sd.grid_url ?? "");
            setDetectedRatio(sd.detected_aspect_ratio ?? "9:16");
            setOriginalSpeech(fullSpeech);
            setSpeechAudioUrl(sd.speech_audio_url ?? "");
            setHasBackgroundMusic(!!sd.has_background_music);
            setLoading(false); setLoadingMsg("");
          } else if (sd.status === "failed") {
            clearInterval(interval);
            setError(sd.error ?? "提取失败");
            setLoading(false); setLoadingMsg("");
          } else {
            setLoadingMsg(`AI 分析中... 已用 ${elapsed}s`);
          }
        } catch {}
      }, 6000);
    } catch (e) { setError(errMsg(e, "提交失败")); setLoading(false); setLoadingMsg(""); }
  };

  const updateScene = (idx: number, key: keyof Scene, val: string) => {
    if (!scenes) return;
    setScenes(scenes.map((s, i) => i === idx ? { ...s, [key]: val } : s));
  };

  const buildMarkdown = () => {
    if (!scenes) return "";
    return serializeReplicateScenes(scenes, {
      total_duration_sec: scenes.reduce((a, s) => a + (s.duration_sec ?? 5), 0),
      overall_setting: overallSetting,
      original_speech: originalSpeech || undefined,
    });
  };

  const onCopy = async () => {
    const md = buildMarkdown();
    await navigator.clipboard.writeText(md);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  const onDownload = () => {
    const md = buildMarkdown();
    const blob = new Blob([md], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "video-storyboard.md";
    a.click();
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>AI 创作工具</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>视频拆帧<span style={{ fontStyle: "italic" }}> storyboard</span></h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            上传任意视频 → 本地切镜头 + 九宫格预览 → AI 提取分镜 / 景别 / 动作 / 口播文字 → 一键复制成 markdown 粘贴到&ldquo;AI 带货视频&rdquo;快速建脚本
          </div>
        </div>

        {error && (
          <div style={{ background: "#fff3f3", border: "1px solid #fcc", color: "#c33", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.9rem" }}>{error}</div>
        )}

        <Box label="① 上传视频">
          <label style={{ display: "block", border: "2px dashed #ddd", borderRadius: 10, padding: "1rem", textAlign: "center", cursor: "pointer", background: videoFile ? "#f9f7f2" : "#fff" }}>
            <input type="file" accept="video/*" style={{ display: "none" }} onChange={e => onPickVideo(e.target.files?.[0] ?? null)} />
            {videoFile ? (
              <div>
                <div style={{ fontSize: "0.85rem", color: "#0d0d0d", marginBottom: 6 }}>✓ {videoFile.name}</div>
                {videoUrl && <video src={videoUrl} controls style={{ maxWidth: 280, maxHeight: 200, marginTop: 6, borderRadius: 8 }} />}
              </div>
            ) : (
              <div style={{ color: "#999", fontSize: "0.9rem" }}>点击上传视频(MP4/MOV,≤100MB)</div>
            )}
          </label>
        </Box>

        {videoUrl && (
          <button onClick={extract} disabled={loading}
            style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, marginBottom: "1rem" }}>
            {loading ? loadingMsg || "提取中..." : (scenes ? "🔄 重新提取(消耗 1 积分)" : "🔍 提取 storyboard(消耗 1 积分)")}
          </button>
        )}

        {gridUrl && (
          <Box label="② 九宫格 storyboard(本地切镜头自动拼合)">
            <div style={{ fontSize: "0.78rem", color: "#999", marginBottom: 8 }}>
              检测视频比例:{detectedRatio} · 每格 1 个镜头,按时间顺序排列,左上角编号对应下面的镜头表
            </div>
            <img src={gridUrl} alt="storyboard grid"
              style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid #eee" }} />
            <div style={{ marginTop: 8, fontSize: "0.78rem", color: "#666" }}>
              <a href={gridUrl} target="_blank" rel="noreferrer" style={{ color: "#0d0d0d" }}>在新标签打开原图</a>
            </div>
          </Box>
        )}

        {scenes && (originalSpeech || speechAudioUrl || hasBackgroundMusic) && (
          <Box label={`③ 提取的口播${hasBackgroundMusic ? " · 检测到背景音乐(已分离)" : " · 无背景音乐"}`}>
            {originalSpeech ? (
              <div style={{ marginBottom: speechAudioUrl ? 10 : 0 }}>
                <div style={{ background: "#f9f7f2", padding: "0.7rem 0.9rem", borderRadius: 8, fontSize: "0.88rem", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                  {originalSpeech}
                </div>
              </div>
            ) : (
              <div style={{ fontSize: "0.85rem", color: "#999" }}>原视频未识别到说话内容</div>
            )}
            {speechAudioUrl && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>纯人声音轨(已剥离背景音乐):</div>
                <audio src={speechAudioUrl} controls style={{ width: "100%", maxWidth: 480 }} />
              </div>
            )}
          </Box>
        )}

        {scenes && (
          <>
            <Box label={`④ 提取的 ${scenes.length} 个分镜(可手动修改)`}>
              <div style={{ fontSize: "0.78rem", color: "#999", marginBottom: 10 }}>
                修改下面任意字段,复制时会带上你的修改
              </div>
              {scenes.map((sc, idx) => (
                <div key={sc.id} style={{ borderTop: idx > 0 ? "1px solid #eee" : "none", paddingTop: idx > 0 ? "1rem" : 0, marginTop: idx > 0 ? "1rem" : 0 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6, flexWrap: "wrap" }}>
                    <strong style={{ fontSize: "0.9rem" }}>镜 {sc.id}</strong>
                    <input value={sc.time_range} onChange={e => updateScene(idx, "time_range", e.target.value)}
                      style={{ width: 100, padding: "0.3rem 0.5rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.78rem" }} />
                    <input value={sc.shot} onChange={e => updateScene(idx, "shot", e.target.value)} placeholder="景别"
                      style={{ width: 130, padding: "0.3rem 0.5rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.78rem" }} />
                    <span style={{ fontSize: "0.78rem", color: "#999" }}>{sc.duration_sec}s</span>
                  </div>
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ fontSize: "0.75rem", color: "#666", marginBottom: 2 }}>动作:</div>
                    <input value={sc.action} onChange={e => updateScene(idx, "action", e.target.value)}
                      style={{ width: "100%", padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.82rem" }} />
                  </div>
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ fontSize: "0.75rem", color: "#666", marginBottom: 2 }}>画面 prompt:</div>
                    <textarea value={sc.visual_prompt} onChange={e => updateScene(idx, "visual_prompt", e.target.value)} rows={2}
                      style={{ width: "100%", padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.82rem", fontFamily: "monospace", resize: "vertical" }} />
                  </div>
                  <div>
                    <div style={{ fontSize: "0.75rem", color: "#666", marginBottom: 2 }}>口播文字 <span style={{ color: "#999" }}>(已按时间戳自动分配,可手动调整)</span>:</div>
                    <textarea value={sc.speech ?? ""} onChange={e => updateScene(idx, "speech" as keyof Scene, e.target.value)} rows={2}
                      placeholder="这一段模特要说的话…"
                      style={{ width: "100%", padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.82rem", lineHeight: 1.5, resize: "vertical" }} />
                  </div>
                </div>
              ))}
              {originalSpeech && (
                <div style={{ marginTop: 12, padding: "0.6rem 0.8rem", background: "#f0f7fb", borderRadius: 8, fontSize: "0.78rem", color: "#456" }}>
                  💡 整段口播已按时间戳自动切到各段。如果切得不准,直接改上面的「口播文字」就行;<strong>每段必须有口播</strong>(空段会让 AI 带货视频生成失败)。
                </div>
              )}
            </Box>

            <Box label="⑤ 整体场景描述(可选,会写到 markdown 头部)">
              <textarea value={overallSetting} onChange={e => setOverallSetting(e.target.value)} rows={2}
                placeholder="例:室内客厅,白天自然光,简洁台面"
                style={{ width: "100%", padding: "0.5rem 0.7rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.85rem", resize: "vertical" }} />
            </Box>

            <Box label="⑥ 复制 / 下载 markdown 脚本">
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
                <button onClick={onCopy}
                  style={{ background: copied ? "#0d8a3e" : "#0d0d0d", color: "#fff", border: "none", padding: "0.7rem 1.4rem", borderRadius: 8, fontSize: "0.9rem", cursor: "pointer", transition: "background 0.2s" }}>
                  {copied ? "✓ 已复制" : "📋 复制全部 markdown"}
                </button>
                <button onClick={onDownload}
                  style={{ background: "transparent", color: "#0d0d0d", border: "1px solid #ddd", padding: "0.7rem 1.4rem", borderRadius: 8, fontSize: "0.9rem", cursor: "pointer" }}>
                  ⬇ 下载 .md 文件
                </button>
              </div>
              <details style={{ fontSize: "0.82rem" }}>
                <summary style={{ cursor: "pointer", color: "#666", marginBottom: 6 }}>预览 markdown 内容</summary>
                <pre style={{ background: "#f9f7f2", padding: "0.8rem", borderRadius: 8, overflow: "auto", maxHeight: 400, fontSize: "0.78rem", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
                  {buildMarkdown()}
                </pre>
              </details>
              <div style={{ marginTop: 12, padding: "0.7rem 0.9rem", background: "#f0f7fb", borderRadius: 8, fontSize: "0.82rem", color: "#1a4068" }}>
                💡 复制后可直接粘贴到「AI 带货视频」的&ldquo;粘贴脚本&rdquo;模式,系统会自动解析填到分镜表里。
              </div>
            </Box>
          </>
        )}
      </main>
    </div>
  );
}
