"use client";

import { useEffect, useState, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface DetailData {
  image_url: string;
  video_url?: string;
  width: number;
  height: number;
  model: string;
  model_label: string;
  prompt: string;
  style: string;
  content_type: "image" | "video";
}

interface EnhanceData {
  title: string;
  description: string;
  selling_points: string[];
  scenes: string[];
  tags: string[];
}

function DetailContent() {
  const searchParams = useSearchParams();

  const [detail, setDetail] = useState<DetailData | null>(null);
  const [enhance, setEnhance] = useState<EnhanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    const data = searchParams.get("data");
    if (!data) {
      setLoading(false);
      return;
    }

    try {
      const parsed = JSON.parse(decodeURIComponent(data)) as DetailData;
      setDetail(parsed);

      // 获取卖点和场景
      fetch(`${API_BASE}/api/content/enhance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: parsed.prompt,
          style: parsed.style,
          content_type: parsed.content_type,
        }),
      })
        .then((r) => r.json())
        .then((res) => {
          if (res.success) setEnhance(res);
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    } catch {
      setLoading(false);
    }
  }, [searchParams]);

  const handleDownload = async () => {
    if (!detail) return;
    setDownloading(true);
    try {
      const url = detail.video_url || detail.image_url;
      const ext = detail.content_type === "video" ? "mp4" : "png";
      const a = document.createElement("a");
      a.href = url;
      a.download = `ai_creative_${Date.now()}.${ext}`;
      a.target = "_blank";
      a.click();
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="text-zinc-400">加载中...</div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="text-center">
          <p className="text-zinc-400 mb-4">未找到内容</p>
          <a href="/image" className="text-amber-400 hover:underline">返回图片生成</a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* 顶部导航 */}
      <nav className="sticky top-0 z-50 bg-zinc-950/90 backdrop-blur border-b border-zinc-800">
        <div className="max-w-5xl mx-auto px-6 py-3 flex items-center justify-between">
          <Link href="/" className="text-amber-400 font-bold">AI 创意平台</Link>
          <div className="flex gap-3">
            <Link
              href={detail.content_type === "video" ? "/video" : "/image"}
              className="text-sm text-zinc-400 hover:text-white transition-colors"
            >
              继续创作
            </Link>
          </div>
        </div>
      </nav>

      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* 主内容区 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* 左：图片/视频展示 */}
          <div className="space-y-4">
            <div className="rounded-xl overflow-hidden border border-zinc-800 bg-zinc-900">
              {detail.video_url ? (
                <video
                  src={detail.video_url}
                  controls
                  autoPlay
                  loop
                  className="w-full aspect-video object-contain"
                />
              ) : (
                <img
                  src={detail.image_url}
                  alt={enhance?.title || "AI Generated"}
                  className="w-full"
                />
              )}
            </div>

            {/* 操作按钮 */}
            <div className="flex gap-3">
              <button
                onClick={handleDownload}
                disabled={downloading}
                className="flex-1 py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
              >
                {downloading ? "下载中..." : `下载${detail.content_type === "video" ? "视频" : "高清图片"}`}
              </button>
              <button
                onClick={() => {
                  const text = `${enhance?.title || "AI创意内容"}\n\n${enhance?.description || ""}\n\n卖点：\n${(enhance?.selling_points || []).map((s, i) => `${i + 1}. ${s}`).join("\n")}`;
                  navigator.clipboard.writeText(text);
                }}
                className="px-4 py-3 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors text-sm"
              >
                复制文案
              </button>
            </div>

            {/* 生成信息 */}
            <div className="p-4 rounded-lg bg-zinc-900 border border-zinc-800">
              <div className="text-xs text-zinc-500 mb-2">生成信息</div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div className="text-zinc-400">模型</div>
                <div className="text-zinc-200">{detail.model_label}</div>
                <div className="text-zinc-400">尺寸</div>
                <div className="text-zinc-200">{detail.width} x {detail.height}</div>
                <div className="text-zinc-400">风格</div>
                <div className="text-zinc-200">
                  {detail.style === "advertising" ? "广告级" : detail.style === "minimalist" ? "极简风" : "自定义"}
                </div>
              </div>
            </div>
          </div>

          {/* 右：卖点和场景 */}
          <div className="space-y-6">
            {/* 标题 */}
            {enhance && (
              <div>
                <h1 className="text-xl font-bold text-white mb-2">{enhance.title}</h1>
                <p className="text-sm text-zinc-400 leading-relaxed">{enhance.description}</p>
              </div>
            )}

            {/* 标签 */}
            {enhance && enhance.tags.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {enhance.tags.map((tag, i) => (
                  <span
                    key={i}
                    className="px-3 py-1 rounded-full text-xs bg-amber-500/10 text-amber-400 border border-amber-500/20"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {/* 核心卖点 */}
            {enhance && (
              <div className="p-5 rounded-xl bg-zinc-900 border border-zinc-800">
                <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                  <span className="w-1 h-5 bg-amber-500 rounded-full" />
                  核心卖点
                </h2>
                <div className="space-y-3">
                  {enhance.selling_points.map((point, i) => (
                    <div key={i} className="flex gap-3">
                      <div className="w-6 h-6 rounded-full bg-amber-500/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                        <span className="text-amber-400 text-xs font-bold">{i + 1}</span>
                      </div>
                      <p className="text-sm text-zinc-300 leading-relaxed">{point}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 适用场景 */}
            {enhance && (
              <div className="p-5 rounded-xl bg-zinc-900 border border-zinc-800">
                <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                  <span className="w-1 h-5 bg-amber-500 rounded-full" />
                  适用场景
                </h2>
                <div className="grid grid-cols-1 gap-2">
                  {enhance.scenes.map((scene, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-3 p-3 rounded-lg bg-zinc-800/50 border border-zinc-700/50"
                    >
                      <span className="text-amber-400 text-lg">
                        {["🛒", "📱", "📰", "🎨", "🏢", "💡"][i % 6]}
                      </span>
                      <span className="text-sm text-zinc-300">{scene}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 使用建议 */}
            <div className="p-5 rounded-xl bg-amber-500/5 border border-amber-500/10">
              <h2 className="text-lg font-bold text-amber-400 mb-3">使用建议</h2>
              <ul className="space-y-2 text-sm text-zinc-400">
                <li className="flex gap-2">
                  <span className="text-amber-400">-</span>
                  下载后可根据实际投放平台调整尺寸和比例
                </li>
                <li className="flex gap-2">
                  <span className="text-amber-400">-</span>
                  可配合「复制文案」功能，将卖点用于商品描述
                </li>
                <li className="flex gap-2">
                  <span className="text-amber-400">-</span>
                  如需调整风格，返回重新生成即可
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function DetailPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-zinc-950 flex items-center justify-center"><div className="text-zinc-400">加载中...</div></div>}>
      <DetailContent />
    </Suspense>
  );
}
