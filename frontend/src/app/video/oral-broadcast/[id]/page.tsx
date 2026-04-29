"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import MaskEditor from "@/components/MaskEditor";
import MediaPicker from "@/components/MediaPicker";
import { compressImage } from "@/lib/utils/imageCompress";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

type Tier = "economy" | "standard" | "premium";

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
  };
  error: string | null;
}

const TIER_PRICE: Record<Tier, { yuan: number; credits: number }> = {
  economy: { yuan: 80, credits: 160 },
  standard: { yuan: 180, credits: 360 },
  premium: { yuan: 350, credits: 700 },
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

  // Step 1 模特/产品(URL 输入 + 从库选两种来源)
  const [modelName, setModelName] = useState("");
  const [modelUrl, setModelUrl] = useState("");
  const [productName, setProductName] = useState("");
  const [productUrl, setProductUrl] = useState("");
  const [pickerOpen, setPickerOpen] = useState<null | "model" | "product">(null);
  const [uploadingKind, setUploadingKind] = useState<null | "model" | "product">(null);

  // Step 2 文案编辑
  const [editedText, setEditedText] = useState("");

  // 行为标志
  const [starting, setStarting] = useState(false);
  const [editingSubmitting, setEditingSubmitting] = useState(false);

  const token = () => (typeof window !== "undefined" ? localStorage.getItem("token") || "" : "");

  // useLang().t 每次渲染都是新引用,塞进 useCallback 依赖会让 loadStatus 引用每次都变 →
  // 下面的 WS effect 反复重连 + 反复 setSess → 父重渲染又触发新一轮。用 ref 锁 t。
  const tRef = useRef(t);
  tRef.current = t;

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/oral/status/${sessionId}`, {
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
      });
      if (!res.ok) {
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

  // WS 实时进度推送(取代 4s 轮询);WS 失败 / 关闭(非终态)自动 fallback 到轮询
  useEffect(() => {
    if (!sessionId) return;
    loadStatus();

    let pollInterval: ReturnType<typeof setInterval> | null = null;
    let ws: WebSocket | null = null;
    let stopped = false;

    const startPolling = () => {
      if (pollInterval || stopped) return;
      pollInterval = setInterval(loadStatus, 4000);
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
      ws.onclose = (e) => { if (e.code !== 1000) startPolling(); };
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
    if (!sess?.products.person_mask_uploaded) { setError(t("oral.errMaskRequired")); return; }
    if ((productName || productUrl) && !sess?.products.product_mask_uploaded) {
      setError(t("oral.errProductMaskRequired"));
      return;
    }

    setStarting(true);
    try {
      const models = [{ name: modelName, image_url: modelUrl }];
      const products = productName && productUrl ? [{ name: productName, image_url: productUrl }] : [];
      const res = await fetch(`${API_BASE}/api/oral/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId, tier, models, products, legal_consent: legalConsent }),
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
      <main style={{ flex: 1, padding: "2rem 3rem", maxWidth: 900 }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <button onClick={() => router.push("/video/oral-broadcast")}
            style={{ background: "none", border: "none", color: "#888", cursor: "pointer", fontSize: "0.9rem" }}>
            ← {t("oral.backToList")}
          </button>
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
              {(["economy", "standard", "premium"] as Tier[]).map(opt => {
                // standard / premium 走 ElevenLabs,P6 接入前不可选(防止用户
                // 跑到 audio swap 阶段才发现挂掉,前置 disabled 避免 fail-late)
                const disabled = opt !== "economy";
                return (
                  <label key={opt} style={{
                    display: "block", padding: "0.8rem 1rem",
                    border: tier === opt ? "2px solid #0d0d0d" : "1px solid #ddd",
                    background: tier === opt ? "#f9f7f2" : "#fff",
                    borderRadius: 10, marginBottom: "0.5rem",
                    cursor: disabled ? "not-allowed" : "pointer",
                    opacity: disabled ? 0.5 : 1,
                  }}>
                    <input type="radio" name="tier" value={opt} checked={tier === opt}
                      disabled={disabled}
                      onChange={() => setTier(opt)} style={{ marginRight: "0.5rem" }} />
                    <strong>{t(`oral.tier.${opt}`)}</strong>
                    {disabled && (
                      <span style={{ marginLeft: "0.5rem", color: "#c33", fontSize: "0.75rem" }}>
                        {t("oral.tierComingSoon")}
                      </span>
                    )}
                    <span style={{ marginLeft: "0.8rem", color: "#888", fontSize: "0.85rem" }}>
                      ¥{TIER_PRICE[opt].yuan}/分钟 · {Math.ceil(TIER_PRICE[opt].credits / 60 * sess.duration_seconds)} 积分
                    </span>
                    <div style={{ fontSize: "0.75rem", color: "#999", marginTop: 4 }}>
                      {t(`oral.tierDesc.${opt}`)}
                    </div>
                  </label>
                );
              })}
            </div>

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
                <div style={{ marginTop: "0.5rem" }}>
                  <img src={modelUrl} alt="" style={{ height: 60, borderRadius: 6, objectFit: "cover" }} />
                </div>
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
                <div style={{ marginTop: "0.5rem" }}>
                  <img src={productUrl} alt="" style={{ height: 60, borderRadius: 6, objectFit: "cover" }} />
                </div>
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

            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.5rem" }}>
                {t("oral.mask.personTitle")}
              </div>
              <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.8rem" }}>
                {t("oral.mask.canvasHint")}
              </div>
              {sess.products.original_video_url ? (
                <MaskEditor
                  videoUrl={sess.products.original_video_url}
                  sessionId={sessionId}
                  kind="person"
                  initialDone={sess.products.person_mask_uploaded}
                  onUploaded={() => loadStatus()}
                />
              ) : (
                <div style={{ padding: "1rem", color: "#888", background: "#f9f7f2", borderRadius: 8 }}>
                  {t("oral.mask.loading")}
                </div>
              )}
            </div>

            {(productName || productUrl) && (
              <div style={{ marginBottom: "1.5rem" }}>
                <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.5rem" }}>
                  {t("oral.mask.productTitle")}
                </div>
                <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.8rem" }}>
                  {t("oral.mask.productHint")}
                </div>
                {sess.products.original_video_url ? (
                  <MaskEditor
                    videoUrl={sess.products.original_video_url}
                    sessionId={sessionId}
                    kind="product"
                    initialDone={sess.products.product_mask_uploaded}
                    onUploaded={() => loadStatus()}
                  />
                ) : (
                  <div style={{ padding: "1rem", color: "#888", background: "#f9f7f2", borderRadius: 8 }}>
                    {t("oral.mask.loading")}
                  </div>
                )}
              </div>
            )}

            <label style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", marginBottom: "1rem", padding: "0.8rem", background: "#fffaeb", borderRadius: 8 }}>
              <input type="checkbox" checked={legalConsent} onChange={e => setLegalConsent(e.target.checked)}
                style={{ marginTop: 4 }} />
              <span style={{ fontSize: "0.85rem", color: "#666" }}>
                {t("oral.legalConsent")}
              </span>
            </label>

            {(() => {
              const hasProduct = Boolean(productName || productUrl);
              const blocked =
                starting ||
                !legalConsent ||
                !modelName ||
                !modelUrl ||
                !sess.products.person_mask_uploaded ||
                (hasProduct && !sess.products.product_mask_uploaded);
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

        {/* ============ Step 4: 等待 ============ */}
        {isRunning && status !== "asr_done" && (
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
            <a href={sess.products.final_video_url} download
              style={{ display: "inline-block", padding: "0.6rem 1.2rem", background: "#0d0d0d", color: "#fff", borderRadius: 8, textDecoration: "none" }}>
              ↓ {t("oral.download")}
            </a>
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
