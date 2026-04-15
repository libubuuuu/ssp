"use client";

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type VideoMode = "image-to-video" | "link" | "workflow" | "replace";

export default function VideoPage() {
  const [mode, setMode] = useState<VideoMode>("image-to-video");
  const [imageUrl, setImageUrl] = useState("");
  const [motionPrompt, setMotionPrompt] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 提交图生视频任务
  const handleImageToVideo = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setTaskId(null);
    setVideoUrl(null);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/api/video/image-to-video`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_url: imageUrl, prompt: motionPrompt }),
      });
      const data = await res.json();

      if (data.task_id) {
        setTaskId(data.task_id);
        setTaskStatus("pending");
        // 自动轮询状态
        pollTaskStatus(data.task_id);
      } else {
        setError(data.detail || "提交失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  };

  // 轮询任务状态
  const pollTaskStatus = async (id: string) => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/video/status/${id}`);
        const data = await res.json();
        setTaskStatus(data.status);

        if (data.status === "completed" && data.video_url) {
          setVideoUrl(data.video_url);
          clearInterval(interval);
        } else if (data.status === "failed") {
          setError(data.error || "视频生成失败");
          clearInterval(interval);
        }
      } catch {
        clearInterval(interval);
      }
    }, 5000); // 每 5 秒轮询一次

    // 3 分钟超时
    setTimeout(() => clearInterval(interval), 180000);
  };

  // 链接改造（暂未实现）
  const handleLinkInit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("链接改造功能尚未实现，请使用图生视频");
  };

  return (
    <div className="max-w-2xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-8">视频生成</h1>

      {/* 模式切换 */}
      <div className="flex flex-wrap gap-2 mb-6">
        {([
          { value: "image-to-video", label: "图生视频" },
          { value: "replace", label: "元素替换" },
          { value: "clone", label: "翻拍复刻", href: "/video/clone" },
          { value: "link", label: "链接改造" },
          { value: "workflow", label: "多镜头工作流" },
        ] as const).map((m) => (
          <button
            key={m.value}
            onClick={() => "href" in m ? window.location.href = m.href : setMode(m.value)}
            className={`px-4 py-2 rounded-lg transition-colors ${
              mode === m.value ? "bg-amber-500 text-black" : "bg-zinc-800 text-zinc-400"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* 图生视频 */}
      {mode === "image-to-video" && (
        <form onSubmit={handleImageToVideo} className="space-y-4">
          <div>
            <label className="block text-sm text-zinc-400 mb-2">首帧图片 URL *</label>
            <input
              type="url"
              value={imageUrl}
              onChange={(e) => setImageUrl(e.target.value)}
              placeholder="输入图片 URL（需要是可公开访问的链接）"
              className="w-full px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none"
              required
            />
          </div>
          <div>
            <label className="block text-sm text-zinc-400 mb-2">运动描述（可选）</label>
            <textarea
              value={motionPrompt}
              onChange={(e) => setMotionPrompt(e.target.value)}
              placeholder="描述视频中期望的运动效果..."
              className="w-full h-24 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none"
            />
          </div>
          <p className="text-sm text-zinc-500">
            使用 Kling 模型生成，预计需要 1-3 分钟
          </p>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
          >
            {loading ? "提交中..." : "生成视频"}
          </button>
        </form>
      )}

      {/* 链接改造（暂未实现） */}
      {mode === "link" && (
        <form onSubmit={handleLinkInit} className="space-y-4">
          <input
            type="url"
            placeholder="输入国内外视频链接"
            className="w-full px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none"
          />
          <p className="text-sm text-zinc-500">
            解析后将询问是否替换人物/背景，产品是否一致等
          </p>
          <button
            type="submit"
            className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 opacity-50 cursor-not-allowed"
            disabled
          >
            功能开发中
          </button>
        </form>
      )}

      {/* 多镜头工作流（暂未实现） */}
      {mode === "workflow" && (
        <div className="p-6 rounded-lg bg-zinc-900 border border-zinc-700">
          <p className="text-zinc-400 mb-4">
            上传首帧或首尾帧，按镜头填写脚本与分镜，保持人物一致与连贯性。
          </p>
          <p className="text-sm text-zinc-500">功能开发中...</p>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="mt-6 p-4 rounded-lg bg-red-900/20 border border-red-700">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}

      {/* 任务状态 */}
      {taskId && !videoUrl && (
        <div className="mt-6 p-4 rounded-lg bg-zinc-900 border border-zinc-700">
          <p className="text-sm text-zinc-400">任务状态</p>
          <p className="font-mono text-amber-400 text-sm mt-1">{taskId}</p>
          <div className="flex items-center gap-2 mt-2">
            <div className={`w-2 h-2 rounded-full ${
              taskStatus === "processing" ? "bg-yellow-400 animate-pulse" :
              taskStatus === "completed" ? "bg-green-400" :
              "bg-zinc-500"
            }`} />
            <span className="text-sm text-zinc-300">
              {taskStatus === "pending" ? "等待处理..." :
               taskStatus === "processing" ? "正在生成中..." :
               taskStatus || "等待中..."}
            </span>
          </div>
        </div>
      )}

      {/* 视频结果 */}
      {videoUrl && (
        <div className="mt-6 space-y-4">
          <div className="rounded-lg overflow-hidden border border-zinc-700">
            <video
              src={videoUrl}
              controls
              className="w-full"
            />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-zinc-400">Kling Video</span>
            <a
              href={videoUrl}
              download
              target="_blank"
              className="px-4 py-2 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors text-sm"
            >
              下载视频
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
