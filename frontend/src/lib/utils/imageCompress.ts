/**
 * 前端图片压缩 — 七十三续
 *
 * 上传图片前先压缩,从 5MB → 500KB 左右,上传时间从 30-50s 降到 3-5s。
 * 走 Web Worker 不阻塞主线程。
 *
 * 默认参数:maxSizeMB=0.8 / maxWidthOrHeight=1920 / useWebWorker=true,
 * 跟产品图 / 模特图 / 参考图等场景适配。GIF 自动跳过(压缩会丢动画)。
 */
import imageCompression from "browser-image-compression";

export interface CompressOptions {
  maxSizeMB?: number;
  maxWidthOrHeight?: number;
  useWebWorker?: boolean;
}

/**
 * 压缩图片;非图片 / GIF / 已经很小的文件直接原样返回
 */
export async function compressImage(file: File, options: CompressOptions = {}): Promise<File> {
  // 不是图片 → 不动(视频 / 音频走专用上传)
  if (!file.type.startsWith("image/")) return file;

  // GIF 压缩会丢动画 → 不动
  if (file.type === "image/gif") return file;

  // 已经够小(< 800KB)→ 不压避免无谓 CPU
  if (file.size < 800 * 1024) return file;

  const opts = {
    maxSizeMB: options.maxSizeMB ?? 0.8,
    maxWidthOrHeight: options.maxWidthOrHeight ?? 1920,
    useWebWorker: options.useWebWorker ?? true,
  };

  try {
    const compressed = await imageCompression(file, opts);
    // 压缩后比原图大或差不多 → 保留原图(罕见,某些 PNG 转 JPEG 反增)
    if (compressed.size >= file.size) return file;
    return compressed;
  } catch {
    // 压缩失败兜底用原图,不阻断业务
    return file;
  }
}

/**
 * 压缩信息,UI 可展示给用户("已压缩 5.2MB → 0.4MB")
 */
export function formatCompressionInfo(originalBytes: number, compressedBytes: number): string {
  const orig = (originalBytes / 1024 / 1024).toFixed(1);
  const comp = (compressedBytes / 1024 / 1024).toFixed(1);
  if (compressedBytes >= originalBytes) return `${orig}MB`;
  const saved = Math.round((1 - compressedBytes / originalBytes) * 100);
  return `${orig}MB → ${comp}MB(省 ${saved}%)`;
}
