/**
 * 七十七续 P5 fix:浏览器侧视频压缩(原生 MediaRecorder,无第三方依赖)。
 *
 * 用户反馈"上传特别慢"的根因之一:60s 1080p 视频 50-100MB,用户上行典型 5-20Mbps,
 * 走 CF 免费版上传不优化 → 1-3 分钟。压缩到 10-20MB 后只要 5-10 秒。
 *
 * 实现:
 *  - <video> 元素加载用户文件
 *  - canvas drawImage 跟随 video.currentTime,降分辨率到 1280px 宽
 *  - canvas.captureStream(30) → MediaStream
 *  - 从 video.captureStream() 拿 audio track 加入(保音轨,后端 ASR 要)
 *  - MediaRecorder vp9+opus,1.5Mbps 视频 + 128kbps 音频
 *  - video.play() 跑完整 → recorder.stop() → Blob → File(.webm)
 *
 * 兼容性:
 *  - Chrome / Edge / Firefox / Safari 14+:支持
 *  - 移动 Safari:14+ 支持
 *  - 不支持的浏览器走 fallback:返回原文件
 *
 * 注意:
 *  - 浏览器禁止 unmuted autoplay,所以必须用户交互(按钮 click)后才能调本函数
 *  - 输出 webm 格式,后端 LONG_VIDEO_MIMES 已经包含 video/webm
 *  - 输出文件名 .webm 后缀,后端 ffmpeg 提取音轨正常
 */

interface CompressOptions {
  maxWidth?: number;       // 目标最大宽度,等比缩放(默认 1280)
  videoBitrate?: number;   // 视频码率(默认 1.5 Mbps)
  audioBitrate?: number;   // 音频码率(默认 128 kbps)
  onProgress?: (pct: number) => void;  // 0-100
}

export interface CompressResult {
  file: File;
  originalSize: number;
  compressedSize: number;
  ratio: number;            // 0-1,小于 1 才有压缩效果
  compressed: boolean;      // false = 走 fallback 返原文件
}

/**
 * 压缩视频。失败或浏览器不支持时返回原文件(compressed=false)。
 */
export async function compressVideo(
  file: File,
  opts: CompressOptions = {},
): Promise<CompressResult> {
  const {
    maxWidth = 1280,
    videoBitrate = 1_500_000,
    audioBitrate = 128_000,
    onProgress,
  } = opts;

  // 浏览器能力检测
  if (typeof MediaRecorder === "undefined") {
    return { file, originalSize: file.size, compressedSize: file.size, ratio: 1, compressed: false };
  }
  // captureStream 检测(HTMLVideoElement 要支持)
  if (typeof HTMLCanvasElement.prototype.captureStream !== "function") {
    return { file, originalSize: file.size, compressedSize: file.size, ratio: 1, compressed: false };
  }

  const mime = MediaRecorder.isTypeSupported("video/webm;codecs=vp9,opus")
    ? "video/webm;codecs=vp9,opus"
    : MediaRecorder.isTypeSupported("video/webm;codecs=vp8,opus")
      ? "video/webm;codecs=vp8,opus"
      : MediaRecorder.isTypeSupported("video/webm")
        ? "video/webm"
        : "";
  if (!mime) {
    return { file, originalSize: file.size, compressedSize: file.size, ratio: 1, compressed: false };
  }

  const videoUrl = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.src = videoUrl;
  // playsInline 避免移动 Safari 全屏播放
  video.playsInline = true;
  video.preload = "auto";
  // muted=false:必须保留 audio track(后端 ASR 要从音轨提取文案)
  video.muted = false;
  video.volume = 0;  // 静音播放(用户体验,不出声)

  try {
    await new Promise<void>((resolve, reject) => {
      const onLoaded = () => resolve();
      const onErr = () => reject(new Error("视频元数据加载失败"));
      video.onloadedmetadata = onLoaded;
      video.onerror = onErr;
      // 超时 30s
      setTimeout(() => reject(new Error("视频加载超时(30s)")), 30000);
    });

    const srcW = video.videoWidth;
    const srcH = video.videoHeight;
    if (!srcW || !srcH) {
      throw new Error("视频分辨率读取失败");
    }

    // 等比缩放
    const scale = srcW > maxWidth ? maxWidth / srcW : 1;
    const targetW = Math.floor(srcW * scale / 2) * 2;  // 偶数(编码器要求)
    const targetH = Math.floor(srcH * scale / 2) * 2;

    const canvas = document.createElement("canvas");
    canvas.width = targetW;
    canvas.height = targetH;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas 2d context 获取失败");

    // 视频流:canvas + audio track
    const videoStream = canvas.captureStream(30);
    // 从 video element 拿 audio track 合并到 stream
    const videoElCaptureStream = (video as HTMLVideoElement & { captureStream?: () => MediaStream }).captureStream;
    if (typeof videoElCaptureStream === "function") {
      try {
        const elStream = videoElCaptureStream.call(video);
        elStream.getAudioTracks().forEach(t => videoStream.addTrack(t));
      } catch {
        // 部分浏览器(老 Safari)不支持 video.captureStream(),走无音轨 fallback
      }
    }

    const recorder = new MediaRecorder(videoStream, {
      mimeType: mime,
      videoBitsPerSecond: videoBitrate,
      audioBitsPerSecond: audioBitrate,
    });

    const chunks: Blob[] = [];
    recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };

    const compressedBlob = await new Promise<Blob>((resolve, reject) => {
      let rafId = 0;

      const render = () => {
        if (video.ended || video.paused) return;
        ctx.drawImage(video, 0, 0, targetW, targetH);
        if (onProgress && video.duration > 0) {
          const pct = Math.min(99, (video.currentTime / video.duration) * 100);
          onProgress(pct);
        }
        rafId = requestAnimationFrame(render);
      };

      recorder.onstop = () => {
        cancelAnimationFrame(rafId);
        const blob = new Blob(chunks, { type: "video/webm" });
        resolve(blob);
      };
      recorder.onerror = (e: Event) => {
        cancelAnimationFrame(rafId);
        reject((e as ErrorEvent).error || new Error("MediaRecorder 错误"));
      };

      video.onended = () => {
        // 给 recorder 100ms 收完最后一帧
        setTimeout(() => {
          if (recorder.state !== "inactive") recorder.stop();
        }, 100);
      };

      video.onplay = () => {
        try {
          recorder.start(1000);  // 每秒 ondataavailable
          render();
        } catch (err) {
          reject(err);
        }
      };

      // 用户交互后才能 play(unmuted),调用方必须保证此函数在 click 处理中调
      video.play().catch(reject);
    });

    URL.revokeObjectURL(videoUrl);

    const baseName = file.name.replace(/\.[^.]+$/, "");
    const compressedFile = new File(
      [compressedBlob],
      `${baseName}.webm`,
      { type: "video/webm", lastModified: Date.now() },
    );

    onProgress?.(100);

    return {
      file: compressedFile,
      originalSize: file.size,
      compressedSize: compressedFile.size,
      ratio: compressedFile.size / file.size,
      compressed: true,
    };
  } catch (err) {
    URL.revokeObjectURL(videoUrl);
    console.warn("[videoCompress] 压缩失败,走 fallback 上传原文件:", err);
    return { file, originalSize: file.size, compressedSize: file.size, ratio: 1, compressed: false };
  }
}
