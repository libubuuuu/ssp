/**
 * 视频复刻专用压缩 — P218.2 (2026-05-08)
 *
 * 跟 videoCompress.ts 区别:
 *  - video.muted = true(autoplay policy 一定通过,不会卡 video.play())
 *  - 不抓 audio track(Seedance r2v 自己生成音频,源音轨不用)
 *  - 严格 60s 总超时(加载 / 播放 / 编码任一阶段超时立刻 fallback 原文件)
 *  - 支持 AbortSignal 用户主动取消
 *
 * 使用约束:
 *  - 必须从 user click handler 链里调(React onChange 选文件即满足)
 *  - 不支持的浏览器(老 Safari / IE)直接 fallback 原文件
 */
export interface SilentCompressOptions {
  maxWidth?: number;
  videoBitrate?: number;
  onProgress?: (pct: number) => void;
  signal?: AbortSignal;
  /** 总超时(ms),含加载 + 播放 + 编码,超时返回原文件。默认 60000 */
  totalTimeoutMs?: number;
}

export interface SilentCompressResult {
  file: File;
  originalSize: number;
  compressedSize: number;
  compressed: boolean;
  reason?: string; // 失败/跳过原因(用户可看)
}

const _isMR = typeof MediaRecorder !== "undefined";
const _hasCapture = typeof HTMLCanvasElement !== "undefined" &&
  typeof HTMLCanvasElement.prototype.captureStream === "function";

export async function compressVideoSilent(
  file: File,
  opts: SilentCompressOptions = {},
): Promise<SilentCompressResult> {
  const {
    maxWidth = 1280,
    videoBitrate = 1_500_000,
    onProgress,
    signal,
    totalTimeoutMs = 60_000,
  } = opts;

  if (!_isMR || !_hasCapture) {
    return { file, originalSize: file.size, compressedSize: file.size, compressed: false, reason: "浏览器不支持压缩(MediaRecorder/captureStream)" };
  }
  const mime = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
    ? "video/webm;codecs=vp9"
    : MediaRecorder.isTypeSupported("video/webm;codecs=vp8")
      ? "video/webm;codecs=vp8"
      : MediaRecorder.isTypeSupported("video/webm") ? "video/webm" : "";
  if (!mime) {
    return { file, originalSize: file.size, compressedSize: file.size, compressed: false, reason: "浏览器不支持 WebM 编码" };
  }

  const videoUrl = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.src = videoUrl;
  video.muted = true;     // 关键:必须 muted 才能 autoplay
  video.playsInline = true;
  video.preload = "auto";
  video.setAttribute("muted", "");

  const cleanup = () => {
    try { URL.revokeObjectURL(videoUrl); } catch {}
  };

  const fail = (reason: string): SilentCompressResult => {
    cleanup();
    return { file, originalSize: file.size, compressedSize: file.size, compressed: false, reason };
  };

  let timeoutId: number | null = null;
  const timeoutPromise = new Promise<never>((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(`压缩超时(${totalTimeoutMs / 1000}s)`)), totalTimeoutMs);
  });
  const abortPromise = new Promise<never>((_, reject) => {
    if (!signal) return;
    if (signal.aborted) reject(new Error("用户取消压缩"));
    signal.addEventListener("abort", () => reject(new Error("用户取消压缩")), { once: true });
  });

  try {
    const compressed = await Promise.race([
      _compressInner(video, file, mime, maxWidth, videoBitrate, onProgress),
      timeoutPromise,
      abortPromise,
    ]);
    if (timeoutId !== null) clearTimeout(timeoutId);
    cleanup();
    return compressed;
  } catch (err) {
    if (timeoutId !== null) clearTimeout(timeoutId);
    try { (video as HTMLVideoElement).pause(); } catch {}
    return fail(err instanceof Error ? err.message : "压缩失败");
  }
}

async function _compressInner(
  video: HTMLVideoElement,
  file: File,
  mime: string,
  maxWidth: number,
  videoBitrate: number,
  onProgress?: (pct: number) => void,
): Promise<SilentCompressResult> {
  await new Promise<void>((resolve, reject) => {
    if (video.readyState >= 1 && video.videoWidth > 0) { resolve(); return; }
    video.onloadedmetadata = () => resolve();
    video.onerror = () => reject(new Error("视频元数据加载失败(格式不支持?)"));
  });

  const srcW = video.videoWidth;
  const srcH = video.videoHeight;
  if (!srcW || !srcH) throw new Error("视频分辨率读取失败");

  const scale = srcW > maxWidth ? maxWidth / srcW : 1;
  const targetW = Math.floor(srcW * scale / 2) * 2;
  const targetH = Math.floor(srcH * scale / 2) * 2;

  const canvas = document.createElement("canvas");
  canvas.width = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 2d context 获取失败");

  const stream = canvas.captureStream(30);
  const recorder = new MediaRecorder(stream, {
    mimeType: mime,
    videoBitsPerSecond: videoBitrate,
  });
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunks.push(e.data); };

  const blob = await new Promise<Blob>((resolve, reject) => {
    let raf = 0;
    const draw = () => {
      if (video.ended || video.paused) return;
      ctx.drawImage(video, 0, 0, targetW, targetH);
      if (onProgress && video.duration > 0) {
        onProgress(Math.min(99, Math.round((video.currentTime / video.duration) * 100)));
      }
      raf = requestAnimationFrame(draw);
    };
    recorder.onstop = () => { cancelAnimationFrame(raf); resolve(new Blob(chunks, { type: "video/webm" })); };
    recorder.onerror = (e) => { cancelAnimationFrame(raf); reject((e as ErrorEvent).error || new Error("MediaRecorder 错误")); };
    video.onended = () => { setTimeout(() => { if (recorder.state !== "inactive") recorder.stop(); }, 100); };
    video.onplay = () => { try { recorder.start(1000); draw(); } catch (err) { reject(err); } };
    // muted=true,autoplay policy 直接通过
    video.play().catch(reject);
  });

  // patch EBML duration(ffprobe 后端要)
  let final = blob;
  try {
    const fixWebmDuration = (await import("webm-duration-fix")).default;
    final = await fixWebmDuration(blob);
  } catch { /* 失败用原 blob */ }

  const baseName = file.name.replace(/\.[^.]+$/, "");
  const compressedFile = new File([final], `${baseName}.webm`, { type: "video/webm", lastModified: Date.now() });

  if (compressedFile.size >= file.size) {
    // 压缩反增 → 用原文件
    return { file, originalSize: file.size, compressedSize: file.size, compressed: false, reason: "压缩后反增,用原文件" };
  }

  onProgress?.(100);
  return {
    file: compressedFile,
    originalSize: file.size,
    compressedSize: compressedFile.size,
    compressed: true,
  };
}
