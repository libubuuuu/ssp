"use client";
import { useState } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";
import { distributeSpeechToScenes } from "@/lib/scriptMarkdown";
import { useLang } from "@/lib/i18n/LanguageContext";

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

// hoist Box 到模块顶层(避免 React remount → IME 断码)
const Box = ({ children, label }: { children: React.ReactNode; label: string }) => (
  <div style={{ background: "#fff", borderRadius: 14, padding: "1.2rem 1.4rem", marginBottom: "1.2rem", boxShadow: "0 2px 8px rgba(0,0,0,0.04)" }}>
    <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.6rem", fontWeight: 500 }}>{label}</div>
    {children}
  </div>
);

const ImagePicker = ({ label, url, onPick, required = false }: {
  label: string;
  url: string;
  onPick: (f: File | null) => void;
  required?: boolean;
}) => (
  <label style={{ display: "block", border: "2px dashed #ddd", borderRadius: 10, padding: "0.8rem", textAlign: "center", cursor: "pointer", background: url ? "#f9f7f2" : "#fff", marginBottom: 8 }}>
    <input type="file" accept="image/*" style={{ display: "none" }} onChange={e => onPick(e.target.files?.[0] ?? null)} />
    <div style={{ fontSize: "0.82rem", color: "#666", marginBottom: 4 }}>
      {label}{required && <span style={{ color: "#c33" }}> *</span>}
    </div>
    {url ? (
      <img src={url} alt={label} style={{ maxWidth: 140, maxHeight: 140, borderRadius: 6 }} />
    ) : (
      <div style={{ color: "#999", fontSize: "0.78rem" }}>点击上传(JPG/PNG,≤10MB)</div>
    )}
  </label>
);

export default function VideoFrameExtractPage() {
  const { t } = useLang();
  // 第一步:视频提取
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [videoDuration, setVideoDuration] = useState<number>(0);
  const [scenes, setScenes] = useState<Scene[] | null>(null);
  const [gridUrls, setGridUrls] = useState<string[]>([]);
  const [detectedRatio, setDetectedRatio] = useState<string>("9:16");
  const [originalSpeech, setOriginalSpeech] = useState<string>("");
  const [speechAudioUrl, setSpeechAudioUrl] = useState<string>("");
  const [hasBackgroundMusic, setHasBackgroundMusic] = useState<boolean>(false);
  const [modelIdentity, setModelIdentity] = useState<string>("");
  const [productCategory, setProductCategory] = useState<string>("");

  // 第二步:素材上传 + 替换
  const [productImageUrl, setProductImageUrl] = useState<string>("");
  const [modelImageUrl, setModelImageUrl] = useState<string>("");
  const [sceneImageUrl, setSceneImageUrl] = useState<string>("");
  const [replacedGridUrls, setReplacedGridUrls] = useState<string[]>([]);

  // 第三步:视频提示词 + 生成
  const [userPrompt, setUserPrompt] = useState<string>("");
  const [videoOutputUrl, setVideoOutputUrl] = useState<string>("");

  // UI
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("");
  const [error, setError] = useState("");

  // 上传 helper(XHR + 退避 retry,跟现有同套)
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
    setVideoFile(f); setError("");
    setLoading(true); setLoadingMsg("上传视频...");
    try {
      const r = await uploadWithRetry(
        `${API_BASE}/api/video/frame-extract/upload/video`,
        f, token(),
        (pct) => setLoadingMsg(`上传视频... ${pct}%`),
      );
      if (!r.ok) {
        if (r.status === 401) throw new Error("登录已过期,请刷新页面重新登录");
        if (r.status === 413) throw new Error("视频太大(>100MB)");
        if (r.status === 415) throw new Error("视频格式不支持(请用 MP4/MOV/WebM)");
        let msg = `上传失败 HTTP ${r.status}`;
        try { const d = JSON.parse(r.text); if (d?.detail) msg = String(d.detail); } catch {}
        throw new Error(msg);
      }
      const d = JSON.parse(r.text);
      // 上传成功才清掉旧的分析结果
      setScenes(null); setGridUrls([]); setOriginalSpeech(""); setSpeechAudioUrl("");
      setReplacedGridUrls([]); setVideoOutputUrl("");
      setVideoUrl(d.video_url);
    } catch (e) { setError(errMsg(e, "上传视频失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };

  const uploadImage = async (f: File, label: string): Promise<string> => {
    const r = await uploadWithRetry(
      `${API_BASE}/api/video/frame-extract/upload/image`,
      f, token(),
    );
    if (!r.ok) {
      let msg = `${label}上传失败 HTTP ${r.status}`;
      try { const d = JSON.parse(r.text); if (d?.detail) msg = String(d.detail); } catch {}
      throw new Error(msg);
    }
    const d = JSON.parse(r.text);
    return d.image_url;
  };

  const onPickProduct = async (f: File | null) => {
    if (!f) return;
    setError(""); setLoading(true); setLoadingMsg("上传产品图...");
    try { setProductImageUrl(await uploadImage(f, "产品图")); }
    catch (e) { setError(errMsg(e, "产品图上传失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };
  const onPickModel = async (f: File | null) => {
    if (!f) return;
    setError(""); setLoading(true); setLoadingMsg("上传人物图...");
    try { setModelImageUrl(await uploadImage(f, "人物图")); }
    catch (e) { setError(errMsg(e, "人物图上传失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };
  const onPickScene = async (f: File | null) => {
    if (!f) return;
    setError(""); setLoading(true); setLoadingMsg("上传场景图...");
    try { setSceneImageUrl(await uploadImage(f, "场景图")); }
    catch (e) { setError(errMsg(e, "场景图上传失败")); }
    finally { setLoading(false); setLoadingMsg(""); }
  };

  // 第一步:提取
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
      setLoadingMsg("AI 分析中(skill 切镜头 + qwen-vl 看九宫格 + wizper 转口播,40-120s)...");
      let elapsed = 0;
      let pollFails = 0;
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
            // 兼容旧字段 grid_url:若后端只返一个,包成数组
            const urls: string[] = Array.isArray(sd.grid_urls) ? sd.grid_urls : (sd.grid_url ? [sd.grid_url] : []);
            setGridUrls(urls);
            setDetectedRatio(sd.detected_aspect_ratio ?? "9:16");
            setOriginalSpeech(fullSpeech);
            setSpeechAudioUrl(sd.speech_audio_url ?? "");
            setHasBackgroundMusic(!!sd.has_background_music);
            setModelIdentity(sd.model_identity ?? "");
            setProductCategory(sd.product_category ?? "");
            setLoading(false); setLoadingMsg("");
          } else if (sd.status === "failed") {
            clearInterval(interval);
            adjustLocalUserCredits(+5); // 退还分析积分
            setError((sd.error ?? "提取失败") + "（已退还 5 积分）");
            setLoading(false); setLoadingMsg("");
          } else {
            pollFails = 0;
            setLoadingMsg(`AI 分析中... 已用 ${elapsed}s`);
          }
        } catch { if (++pollFails >= 5) { clearInterval(interval); setError("分析状态查询失败,请刷新重试"); setLoading(false); setLoadingMsg(""); } }
      }, 6000);
    } catch (e) { setError(errMsg(e, "提交失败")); setLoading(false); setLoadingMsg(""); }
  };

  // 第二步:替换九宫格
  const doReplace = async () => {
    if (gridUrls.length === 0) { setError("请先完成视频提取"); return; }
    if (!productImageUrl && !modelImageUrl && !sceneImageUrl) {
      setError("产品图 / 人物图 / 场景图 至少上传 1 张");
      return;
    }
    setError(""); setLoading(true); setLoadingMsg("提交替换任务...");
    try {
      const r = await fetch(`${API_BASE}/api/video/frame-extract/replace`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({
          grid_urls: gridUrls,
          product_image_url: productImageUrl,
          model_image_url: modelImageUrl,
          scene_image_url: sceneImageUrl || undefined,
          model_identity: modelIdentity,
          product_category: productCategory,
          scenes,  // P237:后端用 qwen-vl 看替换后的图重写 visual_prompt
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      const rid = d.replace_job_id;
      if (!rid) throw new Error("没拿到 replace_job_id");
      const replaceCost = d.cost ?? 3;
      adjustLocalUserCredits(-replaceCost);
      setLoadingMsg("GPT-Image 2 重画九宫格(高峰期 3-5 分钟)...");
      let elapsed = 0;
      let pollFails = 0;
      const interval = setInterval(async () => {
        elapsed += 8;
        try {
          const sr = await fetch(`${API_BASE}/api/video/frame-extract/replace/status/${rid}`, {
            headers: { Authorization: `Bearer ${token()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(interval);
            const urls: string[] = Array.isArray(sd.replaced_grid_urls) ? sd.replaced_grid_urls : (sd.replaced_grid_url ? [sd.replaced_grid_url] : []);
            setReplacedGridUrls(urls);
            // P237:后端用 qwen-vl 看替换后图重写了 visual_prompt,覆盖前端 scenes
            if (Array.isArray(sd.updated_scenes) && sd.updated_scenes.length > 0) {
              setScenes(sd.updated_scenes);
            }
            setLoading(false); setLoadingMsg("");
          } else if (sd.status === "failed") {
            clearInterval(interval);
            adjustLocalUserCredits(+replaceCost);
            const errMsg = sd.error ?? "替换失败";
            const isNsfw = errMsg.includes("content_policy") || errMsg.includes("content_checker") || errMsg.includes("内容审核拒");
            setError(isNsfw
              ? `替换失败：产品图含敏感内容被审核拒绝，已退还 ${replaceCost} 积分。`
              : `${errMsg}（已退还 ${replaceCost} 积分）`
            );
            setLoading(false); setLoadingMsg("");
          } else {
            pollFails = 0;
            setLoadingMsg(`GPT-2 重画中... 已用 ${elapsed}s`);
          }
        } catch { if (++pollFails >= 5) { clearInterval(interval); setError("替换状态查询失败,请刷新重试"); setLoading(false); setLoadingMsg(""); } }
      }, 8000);
    } catch (e) { setError(errMsg(e, "替换失败")); setLoading(false); setLoadingMsg(""); }
  };

  // 第三步:生成视频
  const doGenerate = async () => {
    if (replacedGridUrls.length === 0 || !scenes) {
      setError("请先完成替换九宫格");
      return;
    }
    setError(""); setLoading(true); setLoadingMsg("提交视频生成...");
    try {
      const r = await fetch(`${API_BASE}/api/video/frame-extract/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({
          replaced_grid_urls: replacedGridUrls,
          scenes,
          product_image_url: productImageUrl,
          model_image_url: modelImageUrl,
          scene_image_url: sceneImageUrl || undefined,
          user_prompt: userPrompt,
          aspect_ratio: detectedRatio,
          video_url: videoUrl,  // P238:后端用此叠加原音轨到新视频
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      const gid = d.generate_job_id;
      if (!gid) throw new Error("没拿到 generate_job_id");
      const generateCost = d.cost ?? 30;
      adjustLocalUserCredits(-generateCost);
      setLoadingMsg(`Seedance r2v 并发 ${scenes.length} 段(3-5 分钟)...`);
      let elapsed = 0;
      let pollFails = 0;
      const interval = setInterval(async () => {
        elapsed += 10;
        try {
          const sr = await fetch(`${API_BASE}/api/video/frame-extract/generate/status/${gid}`, {
            headers: { Authorization: `Bearer ${token()}` },
          });
          if (!sr.ok) return;
          const sd = await sr.json();
          if (sd.status === "completed") {
            clearInterval(interval);
            setVideoOutputUrl(sd.video_url ?? "");
            setLoading(false); setLoadingMsg("");
          } else if (sd.status === "failed") {
            clearInterval(interval);
            adjustLocalUserCredits(+generateCost);
            setError((sd.error ?? "生成失败") + `（已退还 ${generateCost} 积分）`);
            setLoading(false); setLoadingMsg("");
          } else {
            pollFails = 0;
            setLoadingMsg(`视频生成中... 已用 ${elapsed}s`);
          }
        } catch { if (++pollFails >= 5) { clearInterval(interval); setError("生成状态查询失败,请刷新重试"); setLoading(false); setLoadingMsg(""); } }
      }, 10000);
    } catch (e) { setError(errMsg(e, "生成失败")); setLoading(false); setLoadingMsg(""); }
  };

  const updateScene = (idx: number, key: keyof Scene, val: string) => {
    if (!scenes) return;
    setScenes(scenes.map((s, i) => i === idx ? { ...s, [key]: val } : s));
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
          <div>
            <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>AI 创作工具</div>
            <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>{t("frameExtract.titleMain")}<span style={{ fontStyle: "italic" }}> {t("frameExtract.titleAccent")}</span></h1>
            <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
              上传视频 → AI 拆分镜九宫格 → 上传你的产品/人物 → 替换九宫格元素 → 生成完整新视频
            </div>
          </div>
          <button
            onClick={() => window.open("/video/frame-extract", "_blank", "noopener")}
            title="开新窗口并行批量生成"
            style={{ background: "#fff", border: "1px solid #d1d5db", borderRadius: 8, padding: "0.55rem 0.95rem", fontSize: "0.85rem", fontWeight: 500, color: "#374151", cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0 }}
          >
            🆕 新建窗口(批量生成)
          </button>
        </div>

        {error && (
          <div style={{ background: "#fff3f3", border: "1px solid #fcc", color: "#c33", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.9rem" }}>{error}</div>
        )}

        <Box label="① 上传参考视频">
          <div style={{ fontSize: "0.78rem", color: "#f59e0b", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8, padding: "0.45rem 0.75rem", marginBottom: 10 }}>
            💡 建议上传 <b>15 秒以内</b>的视频，分析更快、效果更好；超过 30 秒可能较慢
          </div>
          <label style={{ display: "block", border: "2px dashed #ddd", borderRadius: 10, padding: "1rem", textAlign: "center", cursor: "pointer", background: videoFile ? "#f9f7f2" : "#fff" }}>
            <input type="file" accept="video/*" style={{ display: "none" }} onChange={e => onPickVideo(e.target.files?.[0] ?? null)} />
            {videoFile ? (
              <div>
                <div style={{ fontSize: "0.85rem", color: "#0d0d0d", marginBottom: 6 }}>✓ {videoFile.name}</div>
                {videoUrl && <video src={videoUrl} controls style={{ maxWidth: 280, maxHeight: 200, marginTop: 6, borderRadius: 8 }}
                  onLoadedMetadata={e => setVideoDuration(Math.round((e.target as HTMLVideoElement).duration))} />}
              </div>
            ) : (
              <div style={{ color: "#999", fontSize: "0.9rem" }}>点击上传视频(MP4/MOV,≤100MB)</div>
            )}
          </label>
        </Box>

        {videoUrl && !scenes && (
          <div style={{ marginBottom: "1rem" }}>
            {videoDuration > 0 && !loading && (
              <div style={{ fontSize: "0.8rem", color: "#888", marginBottom: 8, padding: "0.5rem 0.8rem", background: "#f5f3ed", borderRadius: 8 }}>
                📹 视频时长 {videoDuration}s · 预计分析需要 {Math.max(60, Math.round(videoDuration * 1.5 + 40))}s，请耐心等待
              </div>
            )}
            <button onClick={extract} disabled={loading}
              style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1 }}>
              {loading ? loadingMsg || "提取中..." : "🔍 提取 storyboard(消耗 5 积分)"}
            </button>
          </div>
        )}

        {gridUrls.length > 0 && (
          <Box label={`② 原视频九宫格 storyboard${gridUrls.length > 1 ? ` · ${gridUrls.length} 张(长视频自动分页)` : ""}`}>
            <div style={{ fontSize: "0.78rem", color: "#999", marginBottom: 8 }}>
              检测视频比例:{detectedRatio} · 每格 1 个镜头,按时间顺序排列{gridUrls.length > 1 ? `(图 1 是前 9 镜,图 2 接下来 9 镜,以此类推)` : ""}
            </div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              {gridUrls.map((u, i) => (
                <div key={i} style={{ flex: gridUrls.length > 1 ? "1 1 320px" : "1 1 100%", minWidth: 280 }}>
                  {gridUrls.length > 1 && (
                    <div style={{ display: "inline-block", fontSize: "1rem", color: "#fff", background: "#0d0d0d", padding: "0.3rem 0.7rem", borderRadius: 6, marginBottom: 6, fontWeight: 600 }}>
                      📷 图 {i + 1} / 共 {gridUrls.length}
                    </div>
                  )}
                  <img src={u} alt={`storyboard ${i+1}`} style={{ width: "100%", borderRadius: 8, border: "1px solid #eee" }} />
                </div>
              ))}
            </div>
            <div style={{ marginTop: 8, fontSize: "0.78rem", color: "#666" }}>
              {modelIdentity && <span>检测人物: {modelIdentity.slice(0, 60)}{modelIdentity.length > 60 ? "..." : ""}</span>}
              {productCategory && <span style={{ marginLeft: 12 }}>· 检测类目: {productCategory}</span>}
            </div>
          </Box>
        )}

        {scenes && (originalSpeech || speechAudioUrl || hasBackgroundMusic) && (
          <Box label={`③ 原视频口播${hasBackgroundMusic ? " · 检测到背景音乐(已分离)" : " · 无背景音乐"}`}>
            {originalSpeech ? (
              <div style={{ background: "#f9f7f2", padding: "0.7rem 0.9rem", borderRadius: 8, fontSize: "0.88rem", lineHeight: 1.6, whiteSpace: "pre-wrap", marginBottom: speechAudioUrl ? 10 : 0 }}>
                {originalSpeech}
              </div>
            ) : (
              <div style={{ fontSize: "0.85rem", color: "#999" }}>原视频未识别到说话内容</div>
            )}
            {speechAudioUrl && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>纯人声音轨:</div>
                <audio src={speechAudioUrl} controls style={{ width: "100%", maxWidth: 480 }} />
              </div>
            )}
          </Box>
        )}

        {scenes && (
          <Box label={`④ 检测到的 ${scenes.length} 个分镜(可手动修改)`}>
            <div style={{ fontSize: "0.78rem", color: "#999", marginBottom: 10 }}>
              修改 visual prompt 会影响最终视频生成
            </div>
            {scenes.map((sc, idx) => (
              <div key={sc.id} style={{ borderTop: idx > 0 ? "1px solid #eee" : "none", paddingTop: idx > 0 ? "1rem" : 0, marginTop: idx > 0 ? "1rem" : 0 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6, flexWrap: "wrap" }}>
                  <strong style={{ fontSize: "0.9rem" }}>镜 {sc.id}</strong>
                  <span style={{ fontSize: "0.78rem", color: "#666", padding: "0.2rem 0.5rem", background: "#f5f3ed", borderRadius: 4 }}>{sc.time_range}</span>
                  <span style={{ fontSize: "0.78rem", color: "#666", padding: "0.2rem 0.5rem", background: "#f5f3ed", borderRadius: 4 }}>{sc.shot}</span>
                  <span style={{ fontSize: "0.78rem", color: "#999" }}>{sc.duration_sec}s</span>
                </div>
                <div style={{ marginBottom: 6 }}>
                  <div style={{ fontSize: "0.75rem", color: "#666", marginBottom: 2 }}>画面 prompt:</div>
                  <textarea value={sc.visual_prompt} onChange={e => updateScene(idx, "visual_prompt", e.target.value)} rows={2}
                    style={{ width: "100%", padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.82rem", fontFamily: "monospace", resize: "vertical" }} />
                </div>
                {sc.speech && (
                  <div style={{ fontSize: "0.78rem", color: "#888" }}>口播:{sc.speech}</div>
                )}
              </div>
            ))}
          </Box>
        )}

        {scenes && (
          <Box label="⑤ 上传你的素材(产品 / 人物 / 场景 — 三选至少 1 张)">
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <ImagePicker label="产品图(可选)" url={productImageUrl} onPick={onPickProduct} />
              </div>
              <div style={{ flex: 1, minWidth: 200 }}>
                <ImagePicker label="人物图(可选)" url={modelImageUrl} onPick={onPickModel} />
              </div>
              <div style={{ flex: 1, minWidth: 200 }}>
                <ImagePicker label="场景图(可选)" url={sceneImageUrl} onPick={onPickScene} />
              </div>
            </div>
            <div style={{ fontSize: "0.78rem", color: "#888", marginTop: 6 }}>
              💡 三种素材**至少传 1 张**。不传的元素 AI 会保留原视频里的(例如只传产品图 → 人物保留原模特,只换产品)
            </div>
          </Box>
        )}

        {scenes && (productImageUrl || modelImageUrl || sceneImageUrl) && replacedGridUrls.length === 0 && (
          <button onClick={doReplace} disabled={loading}
            style={{ background: "#0d0d0d", color: "#fff", border: "none", padding: "0.9rem 1.6rem", borderRadius: 10, fontSize: "0.95rem", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, marginBottom: "1rem" }}>
            {loading ? loadingMsg || "替换中..." : `🎨 替换九宫格元素(消耗 20 积分/张 · 共 ${gridUrls.length} 张)`}
          </button>
        )}

        {replacedGridUrls.length > 0 && (
          <Box label={`⑥ 替换后的新九宫格 storyboard(预览${replacedGridUrls.length > 1 ? ` · ${replacedGridUrls.length} 张` : ""})`}>
            {replacedGridUrls.map((replacedU, idx) => {
              const origU = gridUrls[idx];
              return (
                <div key={idx} style={{ marginBottom: idx < replacedGridUrls.length - 1 ? "1.2rem" : 0, paddingBottom: idx < replacedGridUrls.length - 1 ? "1.2rem" : 0, borderBottom: idx < replacedGridUrls.length - 1 ? "1px solid #eee" : "none" }}>
                  {replacedGridUrls.length > 1 && (
                    <div style={{ display: "inline-block", fontSize: "1rem", color: "#fff", background: "#0d8a3e", padding: "0.3rem 0.7rem", borderRadius: 6, marginBottom: 8, fontWeight: 600 }}>
                      📷 图 {idx + 1} / 共 {replacedGridUrls.length}
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                    <div style={{ flex: 1, minWidth: 280 }}>
                      <div style={{ fontSize: "0.78rem", color: "#999", marginBottom: 4 }}>原九宫格</div>
                      {origU && <img src={origU} alt={`orig ${idx+1}`} style={{ width: "100%", borderRadius: 6, border: "1px solid #eee" }} />}
                    </div>
                    <div style={{ flex: 1, minWidth: 280 }}>
                      <div style={{ fontSize: "0.78rem", color: "#0d8a3e", marginBottom: 4 }}>替换后九宫格</div>
                      <img src={replacedU} alt={`replaced ${idx+1}`} style={{ width: "100%", borderRadius: 6, border: "2px solid #0d8a3e" }} />
                    </div>
                  </div>
                </div>
              );
            })}
            <div style={{ marginTop: 10, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <button onClick={() => { setReplacedGridUrls([]); setVideoOutputUrl(""); }}
                style={{ background: "transparent", border: "1px solid #ddd", padding: "0.3rem 0.8rem", borderRadius: 6, fontSize: "0.82rem", cursor: "pointer" }}>
                🔄 重新替换(再消耗积分)
              </button>
            </div>
          </Box>
        )}

        {replacedGridUrls.length > 0 && (
          <Box label="⑦ 视频提示词(可选,强调产品功能 / 卖点)">
            <textarea value={userPrompt} onChange={e => setUserPrompt(e.target.value)} rows={2}
              placeholder="例:突出产品的防水特性 / 展示充电指示灯 / 强调瘦腿效果..."
              style={{ width: "100%", padding: "0.6rem 0.8rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.88rem", resize: "vertical", fontFamily: "inherit" }} />
            <div style={{ fontSize: "0.78rem", color: "#888", marginTop: 6 }}>
              💡 这段文字会拼到每一段视频生成的 prompt 里,告诉 AI 要重点强调什么
            </div>
          </Box>
        )}

        {replacedGridUrls.length > 0 && !videoOutputUrl && scenes && (
          <button onClick={doGenerate} disabled={loading}
            style={{ background: "#0d8a3e", color: "#fff", border: "none", padding: "1rem 1.8rem", borderRadius: 10, fontSize: "1rem", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, marginBottom: "1rem", fontWeight: 600 }}>
            {loading ? loadingMsg || "生成中..." : `🎬 生成完整视频(65 积分/秒 · ${scenes.length} 段并发 ~3-5 分钟)`}
          </button>
        )}

        {videoOutputUrl && (
          <Box label="⑧ 生成的视频 ✓">
            <video src={videoOutputUrl} controls style={{ width: "100%", maxWidth: 720, borderRadius: 8 }} />
            <div style={{ marginTop: 10, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <a href={videoOutputUrl} download style={{ background: "#0d0d0d", color: "#fff", textDecoration: "none", padding: "0.6rem 1.2rem", borderRadius: 8, fontSize: "0.88rem" }}>
                ⬇ 下载视频
              </a>
              <a href={videoOutputUrl} target="_blank" rel="noreferrer" style={{ background: "transparent", color: "#0d0d0d", border: "1px solid #ddd", padding: "0.6rem 1.2rem", borderRadius: 8, fontSize: "0.88rem", textDecoration: "none" }}>
                在新标签打开
              </a>
              <button onClick={() => setVideoOutputUrl("")}
                style={{ background: "transparent", border: "1px solid #ddd", padding: "0.6rem 1.2rem", borderRadius: 8, fontSize: "0.88rem", cursor: "pointer" }}>
                🔄 重新生成视频
              </button>
            </div>
          </Box>
        )}
      </main>
    </div>
  );
}
