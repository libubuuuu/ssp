"use client";

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function VideoReplacePage() {
  const [videoUrl, setVideoUrl] = useState("");
  const [elementImage, setElementImage] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  const [resultVideoUrl, setResultVideoUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 处理图片上传
  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      setElementImage(e.target?.result as string);
    };
    reader.readAsDataURL(file);
  };

  // 提交替换任务
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!videoUrl || !elementImage || !instruction) {
      setError("请填写所有必填项");
      return;
    }

    setLoading(true);
    setTaskId(null);
    setResultVideoUrl(null);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/api/video/replace/element`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_url: videoUrl,
          element_image_url: elementImage,
          instruction: instruction,
        }),
      });

      const data = await res.json();

      if (data.task_id) {
        setTaskId(data.task_id);
        setTaskStatus("pending");
        // 开始轮询状态
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
          setResultVideoUrl(data.video_url);
          clearInterval(interval);
        } else if (data.status === "failed") {
          setError(data.error || "视频生成失败");
          clearInterval(interval);
        }
      } catch {
        clearInterval(interval);
      }
    }, 5000);

    // 5 分钟超时
    setTimeout(() => clearInterval(interval), 300000);
  };

  return (
    <div className="max-w-3xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-2">视频元素替换</h1>
      <p className="text-zinc-400 mb-8 text-sm">
        上传视频和图片，输入指令让 AI 自动替换视频中的元素
      </p>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* 原视频 URL */}
        <div>
          <label className="block text-sm text-zinc-400 mb-2">
            原视频 URL *
          </label>
          <input
            type="url"
            value={videoUrl}
            onChange={(e) => setVideoUrl(e.target.value)}
            placeholder="https://example.com/video.mp4"
            className="w-full px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none"
            required
          />
        </div>

        {/* 新元素图片上传 */}
        <div>
          <label className="block text-sm text-zinc-400 mb-2">
            新元素图片 *
          </label>
          <div className="space-y-3">
            <label className="block w-full h-32 border-2 border-dashed border-zinc-700 rounded-lg hover:border-amber-500 transition-colors cursor-pointer flex items-center justify-center">
              <input
                type="file"
                accept="image/*"
                onChange={handleImageUpload}
                className="hidden"
              />
              <div className="text-center text-zinc-500">
                <p className="text-2xl mb-2">📁</p>
                <p className="text-sm">点击上传图片</p>
              </div>
            </label>
            {elementImage && (
              <div className="relative">
                <img
                  src={elementImage}
                  alt="替换元素"
                  className="h-32 rounded-lg border border-zinc-700"
                />
                <button
                  type="button"
                  onClick={() => setElementImage(null)}
                  className="absolute top-2 right-2 w-6 h-6 bg-red-500 rounded-full text-white text-sm flex items-center justify-center hover:bg-red-600"
                >
                  ✕
                </button>
              </div>
            )}
          </div>
        </div>

        {/* 指令输入 */}
        <div>
          <label className="block text-sm text-zinc-400 mb-2">
            替换指令 *
          </label>
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="例：把视频里的水杯替换成我上传的图片，保持光影和透视一致"
            className="w-full h-24 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none"
            required
          />
          <p className="text-xs text-zinc-500 mt-2">
            使用 Kling O1 Edit 模型，自动理解指令并精准替换
          </p>
        </div>

        {/* 提交按钮 */}
        <button
          type="submit"
          disabled={loading || !videoUrl || !elementImage || !instruction}
          className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? "提交中..." : "开始替换"}
        </button>
      </form>

      {/* 错误提示 */}
      {error && (
        <div className="mt-6 p-4 rounded-lg bg-red-900/20 border border-red-700">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}

      {/* 任务状态 */}
      {taskId && !resultVideoUrl && (
        <div className="mt-6 p-6 rounded-lg bg-zinc-900 border border-zinc-700">
          <p className="text-sm text-zinc-400 mb-2">任务状态</p>
          <p className="font-mono text-amber-400 text-sm mb-3">{taskId}</p>
          <div className="flex items-center gap-2">
            <div
              className={`w-2 h-2 rounded-full ${
                taskStatus === "processing"
                  ? "bg-yellow-400 animate-pulse"
                  : taskStatus === "completed"
                  ? "bg-green-400"
                  : "bg-zinc-500"
              }`}
            />
            <span className="text-sm text-zinc-300">
              {taskStatus === "pending" ? "等待处理..." : taskStatus === "processing" ? "正在生成中..." : taskStatus || "等待中..."}
            </span>
          </div>
          <div className="mt-4">
            <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-amber-500 animate-pulse"
                style={{ width: "50%" }}
              />
            </div>
          </div>
        </div>
      )}

      {/* 结果视频 */}
      {resultVideoUrl && (
        <div className="mt-6 space-y-4">
          <div className="rounded-lg overflow-hidden border border-zinc-700">
            <video src={resultVideoUrl} controls className="w-full" />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-zinc-400">Kling O1 Edit</span>
            <a
              href={resultVideoUrl}
              download
              target="_blank"
              className="px-4 py-2 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors text-sm"
            >
              下载视频
            </a>
          </div>
        </div>
      )}

      {/* 使用提示 */}
      <div className="mt-12 p-6 rounded-lg bg-zinc-900/50 border border-zinc-800">
        <h3 className="text-sm font-semibold text-zinc-300 mb-3">使用提示</h3>
        <ul className="space-y-2 text-sm text-zinc-500">
          <li>• 视频 URL 需要是可公开访问的链接</li>
          <li>• 指令越具体，替换效果越精准</li>
          <li>• 支持替换人物、商品、背景等元素</li>
          <li>• 生成时间约 1-3 分钟，请耐心等待</li>
        </ul>
      </div>
    </div>
  );
}
