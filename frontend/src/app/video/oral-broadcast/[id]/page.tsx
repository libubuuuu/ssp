"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import MediaPicker from "@/components/MediaPicker";
import { compressImage } from "@/lib/utils/imageCompress";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

type Tier = "economy";

interface SessionStatus {
  session_id: string;
  status: string;
  tier: string | null;
  duration_seconds: number;
  credits_charged: number;
  credits_refunded: number;
  step_progress: { step1: string; step2: string; step3: string; step4: string; step5: string };
  products: {
    original_video_url: string | null;
    asr_transcript: string | null;
    edited_transcript: string | null;
    new_audio_url: string | null;
    swap1_video_url: string | null;
    swapped_video_url: string | null;
    final_video_url: string | null;
    mask_uploaded: boolean;          // legacy:= person_mask_uploaded
    person_mask_uploaded: boolean;
    product_mask_uploaded: boolean;
    // P72 — qwen-vl 自动分镜 prompt + 用户编辑版
    auto_video_prompt?: string | null;
    user_video_prompt?: string | null;
    // P81 — vace-mask 手动按段生成的段列表 JSON
    segments_json?: string | null;
  };
  error: string | null;
}

const TIER_PRICE: Record<Tier, { yuan: number; credits: number }> = {
  economy: { yuan: 80, credits: 160 },
};

export default function OralBroadcastWorkbench() {
  const { t } = useLang();
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const sessionId = params.id;

  const [sess, setSess] = useState<SessionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Step 1 输入。standard / premium 走 ElevenLabs(P6 待接入),目前不可选,
  // 默认 economy 是当前唯一全链路可用的档位。
  const [tier, setTier] = useState<Tier>("economy");
  const [legalConsent, setLegalConsent] = useState(false);
  // P16:成片比例(空字符串表示跟随原视频)
  const [aspectRatio, setAspectRatio] = useState<"" | "9:16" | "16:9" | "1:1">("");
  // P41:Step B 引擎覆盖(空字符串=后端默认 kling-o1-edit;其余多个用户实测对比)
  type StepBEngine = "" | "auto-cheap" | "vace-mask" | "catvton-pixverse" | "aliyun-wan2.7-r2v" | "kling-o3-standard-v2v"
    // 老引擎(后端兼容,UI 不露)
    | "auto" | "auto-best" | "pixverse-swap" | "kling-o1-edit" | "i2v" | "seedance-2-r2v" | "kling-o3-r2v" | "kling-o3-v2v" | "kling-2-6-i2v";
  // P47-C:默认 "auto-cheap"(阿里 wan 主路 + fal 兜底,180 天免费 ¥0,慢但白嫖)
  // 商家用户要快可改 "auto"(fal 主路,~¥17/30s 但 5min 出片)
  const [stepBEngine, setStepBEngine] = useState<StepBEngine>("auto-cheap");
  // P43-2:可选 Topaz 超分到 1440p(默认关,+$0.02/秒)
  const [useTopazUpscale, setUseTopazUpscale] = useState(false);
  // P45:模特图过 codeformer 修脸预处理(默认开,补 fal r2v 真人保身份残差)
  const [useFaceEnhance, setUseFaceEnhance] = useState(true);
  // P43-3:模特/产品多角度图(每个最多 2 张额外,+ 主图共 3 张,Kling O1 elements ref 上限)
  // P53:加 angle 标签(正面/反面/侧面/材质/logo),让模型 prompt 里明确"图N 是 模特/产品 的 角度"
  const [modelExtraUrls, setModelExtraUrls] = useState<string[]>([]);
  const [modelExtraAngles, setModelExtraAngles] = useState<string[]>([]);  // P53 并行数组,默认"侧面"
  const [productExtraUrls, setProductExtraUrls] = useState<string[]>([]);
  const [productExtraAngles, setProductExtraAngles] = useState<string[]>([]);  // P53 并行数组,默认"反面"
  const [uploadingExtraIdx, setUploadingExtraIdx] = useState<"model_extra" | "product_extra" | null>(null);
  // P42:多素材编排 MVP — 仅 seedance-2-r2v 引擎使用,其他引擎忽略
  // scene_ref:场景定调参考图(背景/光感),shot_ref:运镜参考视频(独立于 driving)
  const [sceneRefUrl, setSceneRefUrl] = useState("");
  const [shotRefUrl, setShotRefUrl] = useState("");
  const [uploadingExtra, setUploadingExtra] = useState<"scene" | "shot" | null>(null);

  // Step 1 模特/产品(URL 输入 + 从库选两种来源)
  const [modelName, setModelName] = useState("");
  const [modelUrl, setModelUrl] = useState("");
  const [productName, setProductName] = useState("");
  const [productUrl, setProductUrl] = useState("");
  const [pickerOpen, setPickerOpen] = useState<null | "model" | "product">(null);
  const [uploadingKind, setUploadingKind] = useState<null | "model" | "product">(null);

  // Step 2 文案编辑
  const [editedText, setEditedText] = useState("");

  // P72:视频复刻 prompt(分镜脚本)
  const [videoPrompt, setVideoPrompt] = useState("");
  const [videoPromptGen, setVideoPromptGen] = useState(false);
  const [videoPromptSaving, setVideoPromptSaving] = useState(false);

  // P81:vace-mask 段列表 + 各段生成中标志
  type SegmentMeta = {
    idx: number;
    start_s: number;
    end_s: number;
    duration_s: number;
    prompt: string;
    fal_url: string | null;
    status: "pending" | "generating" | "generated" | "failed";
    summary?: string;
    error?: string;
  };
  const [genSegIdx, setGenSegIdx] = useState<number | null>(null);
  const [merging, setMerging] = useState(false);
  const segments: SegmentMeta[] = (() => {
    try {
      return sess?.products.segments_json ? JSON.parse(sess.products.segments_json) : [];
    } catch { return []; }
  })();

  // 行为标志
  const [starting, setStarting] = useState(false);
  const [editingSubmitting, setEditingSubmitting] = useState(false);

  const token = () => (typeof window !== "undefined" ? localStorage.getItem("token") || "" : "");

  // useLang().t 每次渲染都是新引用,塞进 useCallback 依赖会让 loadStatus 引用每次都变 →
  // 下面的 WS effect 反复重连 + 反复 setSess → 父重渲染又触发新一轮。用 ref 锁 t。
  const tRef = useRef(t);
  tRef.current = t;

  // session 被认定不存在 / 不归属(status 404/403 或 WS 4403)→ 终态,停止轮询 + 不重连
  const goneRef = useRef(false);

  const loadStatus = useCallback(async () => {
    if (goneRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/api/oral/status/${sessionId}`, {
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
      });
      if (!res.ok) {
        if (res.status === 404 || res.status === 403) {
          goneRef.current = true;
        }
        setError(tRef.current("oral.errStatus"));
        return;
      }
      const data: SessionStatus = await res.json();
      setSess(data);
    } catch {} finally { setLoading(false); }
  }, [sessionId]);

  // ASR 完成后自动把原文案灌进编辑框(从 loadStatus / WS handler 抽出,
  // 避免 editedText 进 loadStatus 闭包导致 WS effect 频繁重连)
  useEffect(() => {
    if (sess?.products.asr_transcript && !editedText) {
      setEditedText(sess.products.edited_transcript ?? sess.products.asr_transcript);
    }
  }, [sess?.products.asr_transcript, sess?.products.edited_transcript, editedText]);

  // P72:视频 prompt 初值同步(用户编辑过用 user,否则用 auto)
  useEffect(() => {
    const u = sess?.products.user_video_prompt ?? "";
    const a = sess?.products.auto_video_prompt ?? "";
    if (u && !videoPrompt) setVideoPrompt(u);
    else if (!u && a && !videoPrompt) setVideoPrompt(a);
  }, [sess?.products.user_video_prompt, sess?.products.auto_video_prompt, videoPrompt]);

  // WS 实时进度推送(取代 4s 轮询);WS 失败 / 关闭(非终态)自动 fallback 到轮询
  useEffect(() => {
    if (!sessionId) return;
    loadStatus();

    let pollInterval: ReturnType<typeof setInterval> | null = null;
    let ws: WebSocket | null = null;
    let stopped = false;

    const startPolling = () => {
      if (pollInterval || stopped || goneRef.current) return;
      pollInterval = setInterval(() => {
        if (goneRef.current) {
          if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
          return;
        }
        loadStatus();
      }, 4000);
    };

    try {
      const wsToken = (typeof window !== "undefined" && localStorage.getItem("token")) ?? "";
      ws = new WebSocket(`${WS_BASE}/api/oral/ws/${sessionId}?token=${encodeURIComponent(wsToken)}`);
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as SessionStatus;
          setSess(data);
          setLoading(false);
        } catch {}
      };
      ws.onerror = () => { startPolling(); };
      ws.onclose = (e) => {
        // 4401 token / 4403 not your session → 终态,不再 fallback 轮询
        if (e.code === 4401 || e.code === 4403) { goneRef.current = true; return; }
        if (e.code !== 1000) startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      stopped = true;
      if (pollInterval) clearInterval(pollInterval);
      if (ws) try { ws.close(); } catch {}
    };
  }, [sessionId, loadStatus]);

  const startPipeline = async () => {
    setError("");
    if (!legalConsent) { setError(t("oral.errLegal")); return; }
    if (!modelName || !modelUrl) { setError(t("oral.errModel")); return; }
    // 八十四续 V3:VTON 管线不需要用户涂 mask,删 person/product mask 校验

    setStarting(true);
    try {
      const models = [{ name: modelName, image_url: modelUrl }];
      const products = productName && productUrl ? [{ name: productName, image_url: productUrl }] : [];
      // P42:组装 assets(空数组 = 走老路单素材)
      const assets: Array<{ role: string; type: string; url: string; alias?: string; ord?: number }> = [];
      if (sceneRefUrl) assets.push({ role: "scene_ref", type: "image", url: sceneRefUrl, alias: "scene", ord: 0 });
      if (shotRefUrl) assets.push({ role: "shot_ref", type: "video", url: shotRefUrl, alias: "shot", ord: 1 });
      // P43-3:模特/产品多角度图(后端 Kling o1 edit 用到 elements.reference_image_urls)
      // P53:alias 存 angle("正面/反面/侧面/材质/logo"),后端 prompt 用此拼"图N 是模特/产品的 X"
      modelExtraUrls.forEach((u, i) => assets.push({ role: "anchor_model", type: "image", url: u, alias: modelExtraAngles[i] || "侧面", ord: 10 + i }));
      productExtraUrls.forEach((u, i) => assets.push({ role: "anchor_product", type: "image", url: u, alias: productExtraAngles[i] || "反面", ord: 20 + i }));
      const res = await fetch(`${API_BASE}/api/oral/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId, tier, models, products, legal_consent: legalConsent, aspect_ratio: aspectRatio || null, step_b_engine: stepBEngine || null, assets: assets.length ? assets : null, use_topaz_upscale: useTopazUpscale, use_face_enhance: useFaceEnhance }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || t("oral.errStartFail")); return; }
      await loadStatus();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errStartFail"));
    } finally {
      setStarting(false);
    }
  };

  const submitEditedText = async () => {
    if (!editedText.trim()) { setError(t("oral.errEditEmpty")); return; }
    setEditingSubmitting(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/oral/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId, edited_transcript: editedText }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || t("oral.errEditFail")); return; }
      await loadStatus();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errEditFail"));
    } finally {
      setEditingSubmitting(false);
    }
  };

  // P81:生成单段
  const generateSegment = async (segIdx: number) => {
    setGenSegIdx(segIdx);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/oral/generate-segment`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId, seg_idx: segIdx }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || t("oral.errSegGenFail")); return; }
      await loadStatus();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errSegGenFail"));
    } finally {
      setGenSegIdx(null);
    }
  };

  // P81:合并所有段
  const mergeSegments = async () => {
    setMerging(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/oral/merge-segments`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || t("oral.errMergeFail")); return; }
      await loadStatus();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errMergeFail"));
    } finally {
      setMerging(false);
    }
  };

  // P72/P75:自动生成视频复刻分镜 prompt(force=true 强制重新调 qwen-vl,不读缓存)
  const generateVideoPrompt = async (force: boolean = false) => {
    setVideoPromptGen(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/oral/generate-video-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId, force }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || t("oral.errVideoPromptGenFail")); return; }
      setVideoPrompt(data.auto_prompt || "");
      await loadStatus();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errVideoPromptGenFail"));
    } finally {
      setVideoPromptGen(false);
    }
  };

  // P72:保存用户编辑后的视频 prompt
  const saveVideoPrompt = async () => {
    setVideoPromptSaving(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/oral/update-video-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId, user_video_prompt: videoPrompt }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || t("oral.errVideoPromptSaveFail")); return; }
      await loadStatus();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errVideoPromptSaveFail"));
    } finally {
      setVideoPromptSaving(false);
    }
  };

  const handleImageUpload = async (kind: "model" | "product", originalFile: File) => {
    if (!originalFile.type.startsWith("image/")) { setError(t("oral.errVideoOnly")); return; }
    setUploadingKind(kind);
    setError("");
    try {
      // 七十三续:前端压缩,5MB → 500KB,跨境 fal 上传时间显著降
      const file = await compressImage(originalFile);
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/video/upload/image`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: fd,
      });
      const data = await res.json();
      if (!res.ok || !data.url) { setError(data.detail ?? t("oral.picker.uploadFail")); return; }
      const baseName = file.name.replace(/\.[^.]+$/, "").slice(0, 32);
      if (kind === "model") {
        if (!modelName) setModelName(baseName);
        setModelUrl(data.url);
      } else {
        if (!productName) setProductName(baseName);
        setProductUrl(data.url);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.picker.uploadFail"));
    } finally {
      setUploadingKind(null);
    }
  };

  // P42 MVP:场景图(image)/ 运镜参考视频(video)单独上传,不入 model/product 槽
  const handleExtraUpload = async (slot: "scene" | "shot", originalFile: File) => {
    const wantImage = slot === "scene";
    if (wantImage && !originalFile.type.startsWith("image/")) { setError(t("oral.errVideoOnly")); return; }
    if (!wantImage && !originalFile.type.startsWith("video/")) { setError(t("oral.errVideoOnly")); return; }
    setUploadingExtra(slot);
    setError("");
    try {
      const file = wantImage ? await compressImage(originalFile) : originalFile;
      const fd = new FormData();
      fd.append("file", file);
      const endpoint = wantImage ? "/api/video/upload/image" : "/api/video/upload/video";
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: fd,
      });
      const data = await res.json();
      if (!res.ok || !data.url) { setError(data.detail ?? t("oral.picker.uploadFail")); return; }
      if (slot === "scene") setSceneRefUrl(data.url);
      else setShotRefUrl(data.url);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.picker.uploadFail"));
    } finally {
      setUploadingExtra(null);
    }
  };

  // P43-3:多角度图上传(模特或产品,push 到对应数组)
  const handleAngleUpload = async (kind: "model_extra" | "product_extra", originalFile: File) => {
    if (!originalFile.type.startsWith("image/")) { setError(t("oral.errVideoOnly")); return; }
    setUploadingExtraIdx(kind);
    setError("");
    try {
      const file = await compressImage(originalFile);
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/video/upload/image`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: fd,
      });
      const data = await res.json();
      if (!res.ok || !data.url) { setError(data.detail ?? t("oral.picker.uploadFail")); return; }
      if (kind === "model_extra") {
        setModelExtraUrls(prev => [...prev, data.url].slice(0, 2));
        // P53:默认 angle 标签 — 第一张额外图默认"侧面",第二张默认"全身"
        setModelExtraAngles(prev => [...prev, prev.length === 0 ? "侧面" : "全身"].slice(0, 2));
      } else {
        setProductExtraUrls(prev => [...prev, data.url].slice(0, 2));
        // P53:产品默认 angle — 第一张默认"反面",第二张默认"材质"
        setProductExtraAngles(prev => [...prev, prev.length === 0 ? "反面" : "材质"].slice(0, 2));
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.picker.uploadFail"));
    } finally {
      setUploadingExtraIdx(null);
    }
  };

  const cancelSession = async () => {
    if (!confirm(t("oral.confirmCancel"))) return;
    try {
      const res = await fetch(`${API_BASE}/api/oral/cancel/${sessionId}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
      });
      const data = await res.json();
      if (res.ok) {
        alert(`${t("oral.cancelled")} (退 ${data.credits_refunded} 积分)`);
        await loadStatus();
      }
    } catch {}
  };

  if (loading) return <div style={{ padding: "2rem" }}>{t("oral.loading")}</div>;
  if (!sess) return <div style={{ padding: "2rem", color: "#c33" }}>{error || t("oral.errNotFound")}</div>;

  const status = sess.status;
  const isInitial = status === "uploaded";
  const isAsrDone = status === "asr_done";
  const isRunning = ["asr_running", "edit_submitted", "tts_running", "swap_running", "lipsync_running"].includes(status);
  const isFailed = status.startsWith("failed_");
  const isCancelled = status === "cancelled";
  const isCompleted = status === "completed";

  const renderProgressBar = () => {
    const steps = ["step1", "step2", "step3", "step4", "step5"] as const;
    const labels = [t("oral.s1"), t("oral.s2"), t("oral.s3"), t("oral.s4"), t("oral.s5")];
    const doneCount = steps.filter(k => sess.step_progress[k] === "done").length;
    return (
      <div style={{ marginBottom: "2rem" }}>
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
          {steps.map((k, i) => {
            const st = sess.step_progress[k];
            const color = st === "done" ? "#0a8" : st === "running" ? "#f80" : "#ccc";
            return (
              <div key={k} style={{ flex: 1 }}>
                <div style={{ height: 6, background: color, borderRadius: 3 }} />
                <div style={{ fontSize: "0.7rem", marginTop: 4, color: "#666", textAlign: "center" }}>
                  {st === "running" ? "⏳" : st === "done" ? "✓" : ""} {labels[i]}
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ fontSize: "0.85rem", color: "#666" }}>
          {t("oral.overallProgress")}: {doneCount}/5
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#fbfaf6" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 3rem", maxWidth: 900, width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <button onClick={() => router.push("/video/oral-broadcast")}
              style={{ background: "none", border: "none", color: "#888", cursor: "pointer", fontSize: "0.9rem" }}>
              ← {t("oral.backToList")}
            </button>
            {/* P21:任何状态都显示"+ 新建",方便批量做视频 */}
            <button onClick={() => router.push("/video/oral-broadcast")}
              style={{ padding: "0.4rem 1rem", background: "#0d0d0d", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", fontSize: "0.85rem", fontWeight: 500 }}>
              + {t("oral.newSession")}
            </button>
          </div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 600, margin: "0.5rem 0 0" }}>
            🎤 {t("oral.title")}
          </h1>
          <div style={{ fontSize: "0.85rem", color: "#888", marginTop: 4 }}>
            session: {sessionId.slice(0, 8)}... · {sess.duration_seconds.toFixed(1)}s
            {sess.tier && ` · ${t(`oral.tier.${sess.tier}`)}`}
          </div>
        </div>

        {error && (
          <div style={{ padding: "0.8rem 1rem", background: "#fee", color: "#c33", borderRadius: 8, marginBottom: "1rem" }}>
            {error}
          </div>
        )}

        {!isInitial && renderProgressBar()}

        {/* ============ Step 1: 选档位 + 模特/产品 + 法律确认 + mask 上传 ============ */}
        {isInitial && (
          <section style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginTop: 0 }}>① {t("oral.s1Setup")}</h2>

            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.5rem" }}>{t("oral.tierTitle")}</div>
              {(["economy"] as Tier[]).map(opt => {
                // standard / premium 走 ElevenLabs,P6 接入前不可选(防止用户
                // 跑到 audio swap 阶段才发现挂掉,前置 disabled 避免 fail-late)
                return (
                  <label key={opt} style={{
                    display: "block", padding: "0.8rem 1rem",
                    border: tier === opt ? "2px solid #0d0d0d" : "1px solid #ddd",
                    background: tier === opt ? "#f9f7f2" : "#fff",
                    borderRadius: 10, marginBottom: "0.5rem",
                    cursor: "pointer",
                  }}>
                    <input type="radio" name="tier" value={opt} checked={tier === opt}
                      onChange={() => setTier(opt)} style={{ marginRight: "0.5rem" }} />
                    <strong>{t(`oral.tier.${opt}`)}</strong>
                  </label>
                );
              })}
            </div>

            {/* P16:成片比例选择 */}
            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.5rem" }}>{t("oral.aspectTitle")}</div>
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                {(["", "9:16", "16:9", "1:1"] as const).map(opt => (
                  <label key={opt || "auto"} style={{
                    flex: "1 1 120px",
                    padding: "0.6rem 0.8rem",
                    border: aspectRatio === opt ? "2px solid #0d0d0d" : "1px solid #ddd",
                    background: aspectRatio === opt ? "#f9f7f2" : "#fff",
                    borderRadius: 10,
                    cursor: "pointer",
                    textAlign: "center",
                  }}>
                    <input type="radio" name="aspect" value={opt} checked={aspectRatio === opt}
                      onChange={() => setAspectRatio(opt)} style={{ marginRight: "0.4rem" }} />
                    <strong>{opt || t("oral.aspectAuto")}</strong>
                    <div style={{ fontSize: "0.7rem", color: "#999", marginTop: 2 }}>
                      {opt === "" ? t("oral.aspectAutoDesc") :
                       opt === "9:16" ? t("oral.aspectVertical") :
                       opt === "16:9" ? t("oral.aspectHorizontal") :
                                        t("oral.aspectSquare")}
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* P41:Step B 引擎选择(实测对比用) */}
            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.5rem" }}>{t("oral.engineTitle")}</div>
              <select value={stepBEngine} onChange={e => setStepBEngine(e.target.value as StepBEngine)}
                style={{ width: "100%", padding: "0.6rem 0.8rem", border: "1px solid #ddd", borderRadius: 10, background: "#fff", fontSize: "0.9rem", cursor: "pointer" }}>
                {/* P70 真分层:VACE+SAM2+中文 prompt — 行业唯一近似分层 */}
                <option value="vace-mask">{t("oral.engine.vaceMask")}</option>
                {/* P66 推荐主路:catvton + pixverse — probe 实测最对路 */}
                <option value="catvton-pixverse">{t("oral.engine.catvtonPixverse")}</option>
                <option value="auto-cheap">{t("oral.engine.autoCheap")}</option>
                <option value="aliyun-wan2.7-r2v">{t("oral.engine.aliyunWan27R2v")}</option>
                <option value="kling-o3-standard-v2v">{t("oral.engine.klingO3StandardV2v")}</option>
                <option value="pixverse-swap">{t("oral.engine.pixverseSwap")}</option>
              </select>
              <div style={{ fontSize: "0.7rem", color: "#999", marginTop: 4 }}>
                {t("oral.engine.note")}
              </div>
            </div>

            {/* P43-2:Topaz 超分到 1440p */}
            <div style={{ marginBottom: "1.5rem" }}>
              <label style={{
                display: "flex", alignItems: "center", padding: "0.6rem 0.8rem",
                border: useTopazUpscale ? "2px solid #0d0d0d" : "1px solid #ddd",
                background: useTopazUpscale ? "#f9f7f2" : "#fff",
                borderRadius: 10, cursor: "pointer",
              }}>
                <input type="checkbox" checked={useTopazUpscale}
                  onChange={e => setUseTopazUpscale(e.target.checked)}
                  style={{ marginRight: "0.5rem" }} />
                <div style={{ flex: 1 }}>
                  <strong style={{ fontSize: "0.85rem" }}>{t("oral.topaz.title")}</strong>
                  <div style={{ fontSize: "0.7rem", color: "#999", marginTop: 2 }}>
                    {t("oral.topaz.desc")}
                  </div>
                </div>
              </label>
            </div>

            {/* P45:模特图 codeformer 修脸预处理 */}
            <div style={{ marginBottom: "1.5rem" }}>
              <label style={{
                display: "flex", alignItems: "center", padding: "0.6rem 0.8rem",
                border: useFaceEnhance ? "2px solid #0d0d0d" : "1px solid #ddd",
                background: useFaceEnhance ? "#f9f7f2" : "#fff",
                borderRadius: 10, cursor: "pointer",
              }}>
                <input type="checkbox" checked={useFaceEnhance}
                  onChange={e => setUseFaceEnhance(e.target.checked)}
                  style={{ marginRight: "0.5rem" }} />
                <div style={{ flex: 1 }}>
                  <strong style={{ fontSize: "0.85rem" }}>{t("oral.faceEnhance.title")}</strong>
                  <div style={{ fontSize: "0.7rem", color: "#999", marginTop: 2 }}>
                    {t("oral.faceEnhance.desc")}
                  </div>
                </div>
              </label>
            </div>

            {/* P42 MVP:多素材编排(仅 seedance-2-r2v 引擎使用,其他引擎忽略此输入) */}
            {stepBEngine === "seedance-2-r2v" && (
              <div style={{ marginBottom: "1.5rem", padding: "0.8rem", background: "#fafaf7", border: "1px solid #e8e6dc", borderRadius: 10 }}>
                <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.5rem" }}>{t("oral.assets.title")}</div>
                <div style={{ fontSize: "0.7rem", color: "#999", marginBottom: "0.8rem" }}>{t("oral.assets.note")}</div>

                {/* 场景参考图 */}
                <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.6rem" }}>
                  <div style={{ flex: 1, fontSize: "0.8rem" }}>
                    <strong>{t("oral.assets.sceneTitle")}</strong>
                    <div style={{ fontSize: "0.7rem", color: "#999" }}>{t("oral.assets.sceneDesc")}</div>
                    {sceneRefUrl && <div style={{ fontSize: "0.7rem", color: "#0a8" }}>✓ {sceneRefUrl.slice(-40)}</div>}
                  </div>
                  <label style={{
                    border: "1px solid #ddd", borderRadius: 6, padding: "0.3rem 0.7rem",
                    fontSize: "0.75rem", cursor: uploadingExtra ? "not-allowed" : "pointer",
                    background: "#fff",
                  }}>
                    {uploadingExtra === "scene" ? t("oral.picker.uploading") : (sceneRefUrl ? t("oral.assets.replaceBtn") : t("oral.picker.uploadBtn"))}
                    <input type="file" accept="image/*" disabled={uploadingExtra !== null}
                      onChange={e => { const f = e.target.files?.[0]; if (f) handleExtraUpload("scene", f); e.target.value = ""; }}
                      style={{ display: "none" }} />
                  </label>
                  {sceneRefUrl && (
                    <button type="button" onClick={() => setSceneRefUrl("")}
                      style={{ background: "none", border: "1px solid #ddd", borderRadius: 6, padding: "0.3rem 0.6rem", fontSize: "0.75rem", cursor: "pointer", color: "#888" }}>
                      ×
                    </button>
                  )}
                </div>

                {/* 运镜参考视频 */}
                <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                  <div style={{ flex: 1, fontSize: "0.8rem" }}>
                    <strong>{t("oral.assets.shotTitle")}</strong>
                    <div style={{ fontSize: "0.7rem", color: "#999" }}>{t("oral.assets.shotDesc")}</div>
                    {shotRefUrl && <div style={{ fontSize: "0.7rem", color: "#0a8" }}>✓ {shotRefUrl.slice(-40)}</div>}
                  </div>
                  <label style={{
                    border: "1px solid #ddd", borderRadius: 6, padding: "0.3rem 0.7rem",
                    fontSize: "0.75rem", cursor: uploadingExtra ? "not-allowed" : "pointer",
                    background: "#fff",
                  }}>
                    {uploadingExtra === "shot" ? t("oral.picker.uploading") : (shotRefUrl ? t("oral.assets.replaceBtn") : t("oral.picker.uploadBtn"))}
                    <input type="file" accept="video/*" disabled={uploadingExtra !== null}
                      onChange={e => { const f = e.target.files?.[0]; if (f) handleExtraUpload("shot", f); e.target.value = ""; }}
                      style={{ display: "none" }} />
                  </label>
                  {shotRefUrl && (
                    <button type="button" onClick={() => setShotRefUrl("")}
                      style={{ background: "none", border: "1px solid #ddd", borderRadius: 6, padding: "0.3rem 0.6rem", fontSize: "0.75rem", cursor: "pointer", color: "#888" }}>
                      ×
                    </button>
                  )}
                </div>
              </div>
            )}

            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <div style={{ fontSize: "0.85rem", color: "#666" }}>{t("oral.modelTitle")}</div>
                <div style={{ display: "flex", gap: "0.4rem" }}>
                  <label style={{
                    background: "none", border: "1px solid #ddd", borderRadius: 6,
                    padding: "0.3rem 0.7rem", fontSize: "0.75rem",
                    cursor: uploadingKind ? "not-allowed" : "pointer",
                    color: uploadingKind === "model" ? "#888" : "#0d0d0d",
                    opacity: uploadingKind && uploadingKind !== "model" ? 0.5 : 1,
                  }}>
                    {uploadingKind === "model" ? t("oral.picker.uploading") : t("oral.picker.uploadBtn")}
                    <input type="file" accept="image/*" disabled={uploadingKind !== null}
                      onChange={e => { const f = e.target.files?.[0]; if (f) handleImageUpload("model", f); e.target.value = ""; }}
                      style={{ display: "none" }} />
                  </label>
                  <button type="button" onClick={() => setPickerOpen("model")}
                    style={{ background: "none", border: "1px solid #ddd", borderRadius: 6, padding: "0.3rem 0.7rem", fontSize: "0.75rem", cursor: "pointer", color: "#0d0d0d" }}>
                    {t("oral.picker.fromHistoryBtn")}
                  </button>
                </div>
              </div>
              <input type="text" placeholder={t("oral.modelNamePh")} value={modelName}
                onChange={e => setModelName(e.target.value)}
                style={{ width: "100%", padding: "0.6rem", border: "1px solid #ddd", borderRadius: 8, marginBottom: "0.5rem" }} />
              <input type="url" placeholder={t("oral.modelUrlPh")} value={modelUrl}
                onChange={e => setModelUrl(e.target.value)}
                style={{ width: "100%", padding: "0.6rem", border: "1px solid #ddd", borderRadius: 8 }} />
              {modelUrl && (
                <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                  <img src={modelUrl} alt="" style={{ height: 60, borderRadius: 6, objectFit: "cover" }} />
                  {/* P43-3 + P53:多角度模特图(每张可选角度),最多 2 张 */}
                  {modelExtraUrls.map((u, i) => (
                    <div key={i} style={{ position: "relative", display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
                      <img src={u} alt="" style={{ height: 60, borderRadius: 6, objectFit: "cover", opacity: 0.85 }} />
                      <select value={modelExtraAngles[i] || "侧面"}
                        onChange={e => setModelExtraAngles(prev => { const c = [...prev]; c[i] = e.target.value; return c; })}
                        style={{ fontSize: "0.65rem", padding: "1px 2px", border: "1px solid #ddd", borderRadius: 4, width: 60 }}>
                        <option value="侧面">侧面</option>
                        <option value="全身">全身</option>
                        <option value="背面">背面</option>
                        <option value="特写">特写</option>
                      </select>
                      <button type="button" onClick={() => {
                        setModelExtraUrls(prev => prev.filter((_, idx) => idx !== i));
                        setModelExtraAngles(prev => prev.filter((_, idx) => idx !== i));
                      }}
                        style={{ position: "absolute", top: -6, right: -6, width: 18, height: 18, borderRadius: "50%", border: "1px solid #ddd", background: "#fff", fontSize: "0.7rem", cursor: "pointer", lineHeight: 1, padding: 0 }}>×</button>
                    </div>
                  ))}
                  {modelExtraUrls.length < 2 && (
                    <label style={{
                      height: 60, width: 60, border: "1px dashed #ddd", borderRadius: 6,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      cursor: uploadingExtraIdx ? "not-allowed" : "pointer",
                      fontSize: "1rem", color: "#888",
                    }} title={t("oral.angle.addModelTip")}>
                      {uploadingExtraIdx === "model_extra" ? "..." : "+"}
                      <input type="file" accept="image/*" disabled={uploadingExtraIdx !== null}
                        onChange={e => { const f = e.target.files?.[0]; if (f) handleAngleUpload("model_extra", f); e.target.value = ""; }}
                        style={{ display: "none" }} />
                    </label>
                  )}
                </div>
              )}
              {modelUrl && modelExtraUrls.length === 0 && (
                <div style={{ fontSize: "0.7rem", color: "#aaa", marginTop: 4 }}>{t("oral.angle.modelHint")}</div>
              )}
            </div>

            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <div style={{ fontSize: "0.85rem", color: "#666" }}>{t("oral.productTitle")}</div>
                <div style={{ display: "flex", gap: "0.4rem" }}>
                  <label style={{
                    background: "none", border: "1px solid #ddd", borderRadius: 6,
                    padding: "0.3rem 0.7rem", fontSize: "0.75rem",
                    cursor: uploadingKind ? "not-allowed" : "pointer",
                    color: uploadingKind === "product" ? "#888" : "#0d0d0d",
                    opacity: uploadingKind && uploadingKind !== "product" ? 0.5 : 1,
                  }}>
                    {uploadingKind === "product" ? t("oral.picker.uploading") : t("oral.picker.uploadBtn")}
                    <input type="file" accept="image/*" disabled={uploadingKind !== null}
                      onChange={e => { const f = e.target.files?.[0]; if (f) handleImageUpload("product", f); e.target.value = ""; }}
                      style={{ display: "none" }} />
                  </label>
                  <button type="button" onClick={() => setPickerOpen("product")}
                    style={{ background: "none", border: "1px solid #ddd", borderRadius: 6, padding: "0.3rem 0.7rem", fontSize: "0.75rem", cursor: "pointer", color: "#0d0d0d" }}>
                    {t("oral.picker.fromProductsBtn")}
                  </button>
                </div>
              </div>
              <input type="text" placeholder={t("oral.productNamePh")} value={productName}
                onChange={e => setProductName(e.target.value)}
                style={{ width: "100%", padding: "0.6rem", border: "1px solid #ddd", borderRadius: 8, marginBottom: "0.5rem" }} />
              <input type="url" placeholder={t("oral.productUrlPh")} value={productUrl}
                onChange={e => setProductUrl(e.target.value)}
                style={{ width: "100%", padding: "0.6rem", border: "1px solid #ddd", borderRadius: 8 }} />
              {productUrl && (
                <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                  <img src={productUrl} alt="" style={{ height: 60, borderRadius: 6, objectFit: "cover" }} />
                  {/* P43-3 + P53:多角度产品图(每张可选角度),最多 2 张 */}
                  {productExtraUrls.map((u, i) => (
                    <div key={i} style={{ position: "relative", display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
                      <img src={u} alt="" style={{ height: 60, borderRadius: 6, objectFit: "cover", opacity: 0.85 }} />
                      <select value={productExtraAngles[i] || "反面"}
                        onChange={e => setProductExtraAngles(prev => { const c = [...prev]; c[i] = e.target.value; return c; })}
                        style={{ fontSize: "0.65rem", padding: "1px 2px", border: "1px solid #ddd", borderRadius: 4, width: 60 }}>
                        <option value="反面">反面</option>
                        <option value="侧面">侧面</option>
                        <option value="材质">材质</option>
                        <option value="logo">logo</option>
                        <option value="标签">标签</option>
                        <option value="细节">细节</option>
                      </select>
                      <button type="button" onClick={() => {
                        setProductExtraUrls(prev => prev.filter((_, idx) => idx !== i));
                        setProductExtraAngles(prev => prev.filter((_, idx) => idx !== i));
                      }}
                        style={{ position: "absolute", top: -6, right: -6, width: 18, height: 18, borderRadius: "50%", border: "1px solid #ddd", background: "#fff", fontSize: "0.7rem", cursor: "pointer", lineHeight: 1, padding: 0 }}>×</button>
                    </div>
                  ))}
                  {productExtraUrls.length < 2 && (
                    <label style={{
                      height: 60, width: 60, border: "1px dashed #ddd", borderRadius: 6,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      cursor: uploadingExtraIdx ? "not-allowed" : "pointer",
                      fontSize: "1rem", color: "#888",
                    }} title={t("oral.angle.addProductTip")}>
                      {uploadingExtraIdx === "product_extra" ? "..." : "+"}
                      <input type="file" accept="image/*" disabled={uploadingExtraIdx !== null}
                        onChange={e => { const f = e.target.files?.[0]; if (f) handleAngleUpload("product_extra", f); e.target.value = ""; }}
                        style={{ display: "none" }} />
                    </label>
                  )}
                </div>
              )}
              {productUrl && productExtraUrls.length === 0 && (
                <div style={{ fontSize: "0.7rem", color: "#aaa", marginTop: 4 }}>{t("oral.angle.productHint")}</div>
              )}
            </div>

            <MediaPicker
              source={pickerOpen === "product" ? "products" : "history"}
              open={pickerOpen !== null}
              onClose={() => setPickerOpen(null)}
              onPick={(it) => {
                if (pickerOpen === "model") {
                  setModelName(it.name);
                  setModelUrl(it.image_url);
                } else if (pickerOpen === "product") {
                  setProductName(it.name);
                  setProductUrl(it.image_url);
                }
              }}
            />

            {/* 八十四续 V3:VTON 管线下用户无需手动涂 mask,旧 MaskEditor UI 隐藏。
                后端 _run_inpainting_step 已切到 cat-vton + kling/reference 路径。 */}

            <label style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", marginBottom: "1rem", padding: "0.8rem", background: "#fffaeb", borderRadius: 8 }}>
              <input type="checkbox" checked={legalConsent} onChange={e => setLegalConsent(e.target.checked)}
                style={{ marginTop: 4 }} />
              <span style={{ fontSize: "0.85rem", color: "#666" }}>
                {t("oral.legalConsent")}
              </span>
            </label>

            {(() => {
              // 八十四续 V3 VTON 管线:删 mask 校验,start 只校验模特图 + 法律确认
              const blocked =
                starting ||
                !legalConsent ||
                !modelName ||
                !modelUrl;
              return (
                <button onClick={startPipeline} disabled={blocked}
                  style={{
                    padding: "0.8rem 1.5rem",
                    background: blocked ? "#ccc" : "#0d0d0d",
                    color: "#fff", border: "none", borderRadius: 10,
                    cursor: blocked ? "not-allowed" : "pointer",
                    fontSize: "1rem", fontWeight: 500,
                  }}>
                  {starting ? t("oral.starting") : t("oral.startBtn")}
                </button>
              );
            })()}
          </section>
        )}

        {/* ============ Step 2: ASR 完成,文案编辑 ============ */}
        {isAsrDone && sess.products.asr_transcript && (
          <section style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginTop: 0 }}>② {t("oral.s2Edit")}</h2>
            <div style={{ marginBottom: "1rem" }}>
              <div style={{ fontSize: "0.8rem", color: "#888", marginBottom: 4 }}>{t("oral.asrOriginal")}</div>
              <div style={{ padding: "0.8rem", background: "#f9f7f2", borderRadius: 8, fontSize: "0.9rem", color: "#666" }}>
                {sess.products.asr_transcript}
              </div>
            </div>
            <div style={{ marginBottom: "1rem" }}>
              <div style={{ fontSize: "0.8rem", color: "#888", marginBottom: 4 }}>{t("oral.editPrompt")}</div>
              <textarea value={editedText} onChange={e => setEditedText(e.target.value)}
                rows={6}
                style={{ width: "100%", padding: "0.8rem", border: "1px solid #ddd", borderRadius: 8, fontFamily: "inherit", resize: "vertical" }} />
              {/* 八十三:fal-ai/minimax/voice-clone 硬上限 1000 字符 */}
              <div style={{ fontSize: "0.75rem", color: editedText.length > 1000 ? "#c33" : "#999", marginTop: 4 }}>
                {editedText.length} / 1000
                {editedText.length > 1000 && <span style={{ marginLeft: "0.5rem" }}>{t("oral.textTooLong")}</span>}
              </div>
            </div>
            <button onClick={submitEditedText}
              disabled={editingSubmitting || !editedText.trim() || editedText.length > 1000}
              style={{
                padding: "0.8rem 1.5rem",
                background: editingSubmitting || !editedText.trim() || editedText.length > 1000 ? "#ccc" : "#0d0d0d",
                color: "#fff", border: "none", borderRadius: 10,
                cursor: editingSubmitting || !editedText.trim() || editedText.length > 1000 ? "not-allowed" : "pointer",
                fontWeight: 500,
              }}>
              {editingSubmitting ? t("oral.submitting") : t("oral.startGen")}
            </button>
          </section>
        )}

        {/* ============ P72: 视频复刻分镜 prompt(Step 2 副,所有阶段都显示)============ */}
        {/* P73:任何阶段都显示分镜面板,asr_done 阶段可编辑/重新生成,其他阶段只读展示 */}
        {(sess.products.original_video_url || sess.products.asr_transcript || videoPrompt) && (
          <section style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginTop: 0 }}>
              {t("oral.videoPromptTitle")}
              {!isAsrDone && (
                <span style={{ marginLeft: "0.6rem", fontSize: "0.7rem", padding: "0.15rem 0.5rem", background: "#f0f0f0", color: "#888", borderRadius: 6, fontWeight: 400 }}>
                  {t("oral.videoPromptReadonly")}
                </span>
              )}
            </h2>
            <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: "0.8rem", lineHeight: 1.6 }}>
              {t("oral.videoPromptHint")}
            </div>
            {!videoPrompt && isAsrDone && (
              <button onClick={() => generateVideoPrompt(false)} disabled={videoPromptGen}
                style={{
                  padding: "0.7rem 1.2rem",
                  background: videoPromptGen ? "#ccc" : "#0d6efd",
                  color: "#fff", border: "none", borderRadius: 10,
                  cursor: videoPromptGen ? "not-allowed" : "pointer",
                  fontSize: "0.9rem", fontWeight: 500,
                  marginBottom: "0.8rem",
                }}>
                {videoPromptGen ? t("oral.videoPromptGenerating") : t("oral.videoPromptGenerate")}
              </button>
            )}
            {!videoPrompt && !isAsrDone && (
              <div style={{ fontSize: "0.85rem", color: "#999", padding: "1rem", background: "#f9f7f2", borderRadius: 8 }}>
                {t("oral.videoPromptPending")}
              </div>
            )}
            {videoPrompt && (
              <>
                <textarea value={videoPrompt} onChange={e => setVideoPrompt(e.target.value)}
                  readOnly={!isAsrDone}
                  rows={12}
                  placeholder={t("oral.videoPromptPlaceholder")}
                  style={{ width: "100%", padding: "0.8rem", border: "1px solid #ddd", borderRadius: 8, fontFamily: "inherit", fontSize: "0.85rem", resize: "vertical", marginBottom: "0.6rem", background: !isAsrDone ? "#f9f7f2" : "#fff", color: !isAsrDone ? "#555" : "#000" }} />
                <div style={{ fontSize: "0.7rem", color: "#999", marginBottom: "0.6rem" }}>
                  {videoPrompt.length} / 5000
                </div>
                {isAsrDone && (
                  <div style={{ display: "flex", gap: "0.6rem" }}>
                    <button onClick={() => generateVideoPrompt(true)} disabled={videoPromptGen}
                      style={{
                        padding: "0.6rem 1rem",
                        background: videoPromptGen ? "#ccc" : "#fff",
                        color: "#0d6efd", border: "1px solid #0d6efd", borderRadius: 10,
                        cursor: videoPromptGen ? "not-allowed" : "pointer",
                        fontSize: "0.85rem", fontWeight: 500,
                      }}>
                      {videoPromptGen ? t("oral.videoPromptGenerating") : t("oral.videoPromptRegenerate")}
                    </button>
                    <button onClick={saveVideoPrompt} disabled={videoPromptSaving}
                      style={{
                        padding: "0.6rem 1rem",
                        background: videoPromptSaving ? "#ccc" : "#0d0d0d",
                        color: "#fff", border: "none", borderRadius: 10,
                        cursor: videoPromptSaving ? "not-allowed" : "pointer",
                        fontSize: "0.85rem", fontWeight: 500,
                      }}>
                      {videoPromptSaving ? t("oral.submitting") : t("oral.videoPromptSave")}
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
        )}

        {/* ============ P81: vace-mask 段列表(用户手动按段生成 + 合并)============ */}
        {segments.length > 0 && !sess.products.swapped_video_url && (
          <section style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginTop: 0 }}>
              🎬 {t("oral.segmentsTitle")} ({segments.filter(s => s.status === "generated").length}/{segments.length})
            </h2>
            <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: "1rem", lineHeight: 1.6 }}>
              {t("oral.segmentsHint")}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.8rem", marginBottom: "1rem" }}>
              {segments.map(seg => (
                <div key={seg.idx} style={{
                  border: "1px solid #ddd", borderRadius: 10, padding: "0.8rem",
                  background: seg.status === "generated" ? "#f0fdf4" :
                    seg.status === "generating" ? "#fef9c3" :
                    seg.status === "failed" ? "#fef2f2" : "#fafaf7",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.4rem" }}>
                    <div style={{ fontSize: "0.95rem", fontWeight: 600 }}>
                      {t("oral.segmentLabel")} {seg.idx + 1}({seg.start_s.toFixed(1)}s - {seg.end_s.toFixed(1)}s)
                    </div>
                    <div style={{ fontSize: "0.75rem", padding: "0.15rem 0.5rem", borderRadius: 6,
                      background: seg.status === "generated" ? "#16a34a" :
                        seg.status === "generating" ? "#eab308" :
                        seg.status === "failed" ? "#dc2626" : "#888",
                      color: "#fff" }}>
                      {seg.status === "generated" ? t("oral.segStatusGenerated") :
                        seg.status === "generating" ? t("oral.segStatusGenerating") :
                        seg.status === "failed" ? t("oral.segStatusFailed") : t("oral.segStatusPending")}
                    </div>
                  </div>
                  {seg.summary && (
                    <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: "0.5rem", whiteSpace: "pre-wrap" }}>
                      {seg.summary}
                    </div>
                  )}
                  {seg.fal_url && (
                    <video src={seg.fal_url} controls style={{ width: "100%", maxWidth: 360, borderRadius: 8, marginBottom: "0.5rem" }} />
                  )}
                  {seg.error && (
                    <div style={{ fontSize: "0.75rem", color: "#dc2626", marginBottom: "0.5rem" }}>{seg.error}</div>
                  )}
                  <button onClick={() => generateSegment(seg.idx)}
                    disabled={genSegIdx !== null || merging}
                    style={{
                      padding: "0.5rem 1rem",
                      background: (genSegIdx === seg.idx) ? "#ccc" :
                        seg.status === "generated" ? "#fff" : "#0d6efd",
                      color: seg.status === "generated" ? "#0d6efd" : "#fff",
                      border: seg.status === "generated" ? "1px solid #0d6efd" : "none",
                      borderRadius: 8,
                      cursor: (genSegIdx !== null || merging) ? "not-allowed" : "pointer",
                      fontSize: "0.85rem", fontWeight: 500,
                    }}>
                    {genSegIdx === seg.idx ? t("oral.segGenerating") :
                      seg.status === "generated" ? t("oral.segRegenerate") : t("oral.segGenerate")}
                  </button>
                </div>
              ))}
            </div>
            {segments.every(s => s.status === "generated") && (
              <button onClick={mergeSegments} disabled={merging || genSegIdx !== null}
                style={{
                  padding: "0.8rem 1.5rem",
                  background: merging ? "#ccc" : "#0d0d0d",
                  color: "#fff", border: "none", borderRadius: 10,
                  cursor: (merging || genSegIdx !== null) ? "not-allowed" : "pointer",
                  fontWeight: 500, fontSize: "0.95rem",
                }}>
                {merging ? t("oral.merging") : t("oral.mergeBtn")}
              </button>
            )}
            <button onClick={cancelSession}
              style={{ marginLeft: "0.6rem", padding: "0.5rem 1rem", background: "#fff", color: "#c33", border: "1px solid #c33", borderRadius: 8, cursor: "pointer", fontSize: "0.85rem" }}>
              {t("oral.cancelBtn")}
            </button>
          </section>
        )}

        {/* ============ Step 4: 等待(非 vace-mask 引擎走原路径)============ */}
        {isRunning && status !== "asr_done" && segments.length === 0 && (
          <section style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginTop: 0 }}>⏳ {t("oral.s4Waiting")}</h2>
            <div style={{ color: "#666", marginBottom: "1rem" }}>{t("oral.waitHint")}</div>
            <button onClick={cancelSession}
              style={{ padding: "0.6rem 1.2rem", background: "#fff", color: "#c33", border: "1px solid #c33", borderRadius: 8, cursor: "pointer" }}>
              {t("oral.cancelBtn")}
            </button>
          </section>
        )}

        {/* ============ Step 5: 完成 ============ */}
        {isCompleted && sess.products.final_video_url && (
          <section style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginTop: 0 }}>🎉 {t("oral.s5Done")}</h2>
            <video src={sess.products.final_video_url} controls
              style={{ width: "100%", borderRadius: 8, marginBottom: "1rem" }} />
            <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
              <a href={sess.products.final_video_url} download
                style={{ display: "inline-block", padding: "0.6rem 1.2rem", background: "#0d0d0d", color: "#fff", borderRadius: 8, textDecoration: "none" }}>
                ↓ {t("oral.download")}
              </a>
              <button onClick={() => router.push("/video/oral-broadcast")}
                style={{ padding: "0.6rem 1.2rem", background: "#fff", color: "#0d0d0d", border: "1px solid #0d0d0d", borderRadius: 8, cursor: "pointer", fontWeight: 500 }}>
                + {t("oral.makeAnother")}
              </button>
            </div>
            <div style={{ fontSize: "0.8rem", color: "#888", marginTop: "1rem" }}>
              {t("oral.consumed")}: {sess.credits_charged} 积分
            </div>
          </section>
        )}

        {/* ============ 失败 / 取消 ============ */}
        {(isFailed || isCancelled) && (
          <section style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, marginBottom: "1rem", border: isFailed ? "1px solid #fcc" : "1px solid #ddd" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginTop: 0, color: isFailed ? "#c33" : "#888" }}>
              {isFailed ? `❌ ${t("oral.failedTitle")}` : `🚫 ${t("oral.cancelled")}`}
            </h2>
            {sess.error && <div style={{ color: "#c33", marginBottom: "0.5rem" }}>{sess.error}</div>}
            <div style={{ fontSize: "0.85rem", color: "#666" }}>
              {t("oral.refunded")}: {sess.credits_refunded} 积分 / {sess.credits_charged}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
