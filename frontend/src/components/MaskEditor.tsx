"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import { useLang } from "@/lib/i18n/LanguageContext";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

type Tool = "brush" | "erase" | "rect";
type MaskKind = "person" | "product";

interface Props {
  videoUrl: string;
  sessionId: string;
  kind?: MaskKind;        // P9b 双 mask:默认 person
  initialDone?: boolean;  // 父组件已知 mask 已上传(刷新时复原 done 态)
  onUploaded?: () => void;
}

/**
 * 七十七续 P4b:口播带货 mask 编辑器(HTML5 canvas 自实现,不依赖第三方库)。
 *
 * - 视频首帧通过 <video> currentTime=0 抽出,drawImage 到背景 canvas
 * - 前景 canvas 跟随鼠标涂抹白色 mask(白=换,黑=保留 — wan-vace 标准)
 * - 工具栏:笔/橡皮/矩形/清除/完成
 * - 完成时合成黑底+白 mask PNG → POST /api/oral/upload-mask
 *
 * 关键点:
 *  - 视频走同源 /uploads/ 路径,无 CORS 问题
 *  - 双画布分离背景与 mask 编辑,清除时只清前景
 *  - mask PNG 输出尺寸跟视频原始分辨率一致(fal salient tracking 要求 mask 跟 video 对齐)
 */
export default function MaskEditor({ videoUrl, sessionId, kind = "person", initialDone = false, onUploaded }: Props) {
  const { t } = useLang();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const bgCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const maskCanvasRef = useRef<HTMLCanvasElement | null>(null);

  const [ready, setReady] = useState(false);
  const [tool, setTool] = useState<Tool>("brush");
  const [brushSize, setBrushSize] = useState(40);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [done, setDone] = useState(initialDone);

  const drawingRef = useRef(false);
  const lastPosRef = useRef<{ x: number; y: number } | null>(null);
  const rectStartRef = useRef<{ x: number; y: number } | null>(null);
  const dprRef = useRef(1);

  // 把视频首帧画到背景 canvas
  const captureFirstFrame = useCallback(() => {
    const video = videoRef.current;
    const bg = bgCanvasRef.current;
    const mask = maskCanvasRef.current;
    if (!video || !bg || !mask) return;

    const W = video.videoWidth;
    const H = video.videoHeight;
    if (!W || !H) return;

    // 用真实视频分辨率作 canvas 内部尺寸(mask 输出跟原片对齐)
    bg.width = W; bg.height = H;
    mask.width = W; mask.height = H;

    const bgCtx = bg.getContext("2d");
    bgCtx?.drawImage(video, 0, 0, W, H);

    // mask 初始全黑(= 全部保留)
    const mctx = mask.getContext("2d");
    if (mctx) {
      mctx.fillStyle = "#000";
      mctx.fillRect(0, 0, W, H);
    }
    setReady(true);
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    // 用 loadeddata(HAVE_CURRENT_DATA)直接画首帧 — 不需要 seek 那一跳。
    // 之前用 loadedmetadata + 设 currentTime + 等 seeked,但 React 渲染时
    // <video src> 已经挂载并开始 load,如果事件在 useEffect 执行前已 fire,
    // listener 永远收不到 → ready 永远 false → 卡死无限 loading。修法:
    //   1. 注册 listener 前先检查 readyState(已就绪直接画)
    //   2. 用 loadeddata 不用 loadedmetadata+seek 组合(少一跳)
    //   3. 10s timeout fallback,超时显示错误而不是无限转圈
    const draw = () => captureFirstFrame();
    const onErr = () => setError(t("oral.mask.errVideoLoad"));
    video.addEventListener("loadeddata", draw);
    video.addEventListener("error", onErr);

    if (video.readyState >= 2 /* HAVE_CURRENT_DATA */) {
      draw();
    }

    const timeoutId = window.setTimeout(() => {
      if (!videoRef.current) return;
      if (videoRef.current.readyState < 2) {
        setError(t("oral.mask.errVideoLoad"));
      }
    }, 10000);

    return () => {
      window.clearTimeout(timeoutId);
      video.removeEventListener("loadeddata", draw);
      video.removeEventListener("error", onErr);
    };
  }, [captureFirstFrame, t, videoUrl]);

  // 屏幕坐标 → canvas 内部坐标(应对 CSS 缩放)
  const toCanvas = (e: React.PointerEvent) => {
    const mask = maskCanvasRef.current;
    if (!mask) return { x: 0, y: 0 };
    const rect = mask.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * mask.width;
    const y = ((e.clientY - rect.top) / rect.height) * mask.height;
    return { x, y };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (!ready) return;
    drawingRef.current = true;
    const p = toCanvas(e);
    lastPosRef.current = p;
    if (tool === "rect") rectStartRef.current = p;
    if (tool === "brush" || tool === "erase") drawDot(p);
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drawingRef.current || !ready) return;
    const p = toCanvas(e);
    if (tool === "brush" || tool === "erase") drawLine(lastPosRef.current!, p);
    lastPosRef.current = p;
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    if (tool === "rect" && rectStartRef.current) {
      const end = toCanvas(e);
      const start = rectStartRef.current;
      const ctx = maskCanvasRef.current?.getContext("2d");
      if (ctx) {
        ctx.fillStyle = "#fff";
        ctx.fillRect(
          Math.min(start.x, end.x), Math.min(start.y, end.y),
          Math.abs(end.x - start.x), Math.abs(end.y - start.y),
        );
      }
      rectStartRef.current = null;
    }
    lastPosRef.current = null;
  };

  const drawDot = ({ x, y }: { x: number; y: number }) => {
    const ctx = maskCanvasRef.current?.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = tool === "erase" ? "#000" : "#fff";
    ctx.beginPath();
    ctx.arc(x, y, brushSize, 0, Math.PI * 2);
    ctx.fill();
  };

  const drawLine = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const ctx = maskCanvasRef.current?.getContext("2d");
    if (!ctx) return;
    ctx.strokeStyle = tool === "erase" ? "#000" : "#fff";
    ctx.lineWidth = brushSize * 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  };

  const clearMask = () => {
    const mask = maskCanvasRef.current;
    if (!mask) return;
    const ctx = mask.getContext("2d");
    if (ctx) {
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, mask.width, mask.height);
    }
  };

  const submitMask = async () => {
    const mask = maskCanvasRef.current;
    if (!mask) return;
    setUploading(true);
    setError("");
    try {
      const blob: Blob = await new Promise((resolve, reject) =>
        mask.toBlob(b => b ? resolve(b) : reject(new Error("toBlob 失败")), "image/png")
      );
      const fd = new FormData();
      fd.append("session_id", sessionId);
      fd.append("kind", kind);
      fd.append("file", new File([blob], `${kind}_mask.png`, { type: "image/png" }));
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/oral/upload-mask`, {
        method: "POST",
        body: fd,
        headers: { Authorization: `Bearer ${token}` },
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || t("oral.mask.errUpload"));
        return;
      }
      setDone(true);
      onUploaded?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.mask.errUpload"));
    } finally {
      setUploading(false);
    }
  };

  if (done) {
    return (
      <div style={{ padding: "0.8rem 1rem", background: "#e6f5ee", color: "#0a8", borderRadius: 8 }}>
        ✓ {t("oral.mask.done")}
        <button onClick={() => { setDone(false); clearMask(); }}
          style={{ marginLeft: "1rem", background: "none", border: "1px solid #0a8", color: "#0a8", padding: "0.3rem 0.8rem", borderRadius: 6, cursor: "pointer" }}>
          {t("oral.mask.redo")}
        </button>
      </div>
    );
  }

  const btnStyle = (active: boolean) => ({
    padding: "0.4rem 0.8rem",
    background: active ? "#0d0d0d" : "#fff",
    color: active ? "#fff" : "#333",
    border: "1px solid #ddd",
    borderRadius: 6,
    cursor: "pointer",
    fontSize: "0.85rem",
  });

  return (
    <div>
      {/* 隐藏 video 用于抽首帧 */}
      <video ref={videoRef} src={videoUrl} preload="auto" style={{ display: "none" }} />

      {!ready && (
        <div style={{ padding: "1rem", background: "#f9f7f2", borderRadius: 8, color: "#888" }}>
          {error || t("oral.mask.loading")}
        </div>
      )}

      {ready && (
        <div>
          <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.8rem", flexWrap: "wrap", alignItems: "center" }}>
            <button onClick={() => setTool("brush")} style={btnStyle(tool === "brush")}>🖌 {t("oral.mask.brush")}</button>
            <button onClick={() => setTool("erase")} style={btnStyle(tool === "erase")}>🧽 {t("oral.mask.erase")}</button>
            <button onClick={() => setTool("rect")} style={btnStyle(tool === "rect")}>▭ {t("oral.mask.rect")}</button>
            <span style={{ marginLeft: "0.5rem", fontSize: "0.85rem", color: "#666" }}>{t("oral.mask.size")}:</span>
            <input type="range" min={5} max={120} value={brushSize}
              onChange={e => setBrushSize(parseInt(e.target.value))}
              style={{ width: 100 }} />
            <span style={{ fontSize: "0.85rem", color: "#666", width: 30 }}>{brushSize}</span>
            <button onClick={clearMask}
              style={{ ...btnStyle(false), color: "#c33", borderColor: "#fcc" }}>{t("oral.mask.clear")}</button>
          </div>

          <div style={{ position: "relative", lineHeight: 0, background: "#000", borderRadius: 8, overflow: "hidden", maxWidth: "100%" }}>
            <canvas ref={bgCanvasRef} style={{ width: "100%", display: "block" }} />
            <canvas ref={maskCanvasRef}
              style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", opacity: 0.5, cursor: "crosshair", touchAction: "none" }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
            />
          </div>

          <div style={{ fontSize: "0.75rem", color: "#999", marginTop: "0.5rem" }}>
            💡 {t("oral.mask.hint")}
          </div>

          {error && <div style={{ color: "#c33", marginTop: "0.5rem" }}>{error}</div>}

          <button onClick={submitMask} disabled={uploading}
            style={{
              marginTop: "0.8rem", padding: "0.6rem 1.2rem",
              background: uploading ? "#ccc" : "#0d0d0d", color: "#fff",
              border: "none", borderRadius: 8, cursor: uploading ? "not-allowed" : "pointer",
            }}>
            {uploading ? t("oral.mask.uploading") : `✓ ${t("oral.mask.confirm")}`}
          </button>
        </div>
      )}
    </div>
  );
}
