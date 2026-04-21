"use client";

import { useState, useRef, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://43.134.71.189:8000";

export default function VideoClonePage() {
  const [referenceVideoUrl, setReferenceVideoUrl] = useState("");
  const [videoUploading, setVideoUploading] = useState(false);
  const [modelImage, setModelImage] = useState<string | null>(null);
  const [productImage, setProductImage] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  const [resultVideoUrl, setResultVideoUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pollIntervalRef = useRef<number | null>(null);
  const pollTimeoutRef = useRef<number | null>(null);

  const clearPolling = () => {
    if (pollIntervalRef.current !== null) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    if (pollTimeoutRef.current !== null) {
      clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
    }
  };

  useEffect(() => {
    return clearPolling;
  }, []);

  // 处理图片上传
  const handleImageUpload = async (
    e: React.ChangeEvent<HTMLInputElement>,
    setImage: (url: string | null) => void
  ) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/api/video/upload/image`, {
      method: "POST",
      headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      body: formData,
    });
    const data = await res.json();
    if (data.url) setImage(data.url);
    else setError("图片上传失败");
  };

  // 上传参考视频
  const handleVideoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setVideoUploading(true);
    setError(null);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch(`${API_BASE}/api/video/upload/video`, {
        method: "POST",
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
        body: formData,
      });
      const data = await res.json();
      if (data.url) setReferenceVideoUrl(data.url);
      else setError("视频上传失败: " + (data.detail || "未知错误"));
    } catch (err) {
      setError("视频上传失败");
    } finally {
      setVideoUploading(false);
    }
  };

  // 提交翻拍任务
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!referenceVideoUrl || !modelImage) {
      setError("请填写所有必填项");
      return;
    }

    setLoading(true);
    setTaskId(null);
    setResultVideoUrl(null);
    setError(null);
    clearPolling();

    try {
      const res = await fetch(`${API_BASE}/api/video/clone`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${localStorage.getItem("token")}` },
        body: JSON.stringify({
          reference_video_url: referenceVideoUrl,
          model_image_url: modelImage,
          product_image_url: productImage || undefined,
        }),
      });

      const data = await res.json();

      if (data.task_id) {
        setTaskId(data.task_id);
        setTaskStatus("pending");
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
    clearPolling();

    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/video/status/${id}`);
        const data = await res.json();
        setTaskStatus(data.status);

        if (data.status === "completed" && data.video_url) {
          setResultVideoUrl(data.video_url);
          clearPolling();
        } else if (data.status === "failed") {
          setError(data.error || "视频生成失败");
          clearPolling();
        }
      } catch {
        clearPolling();
      }
    };

    pollIntervalRef.current = setInterval(poll, 5000) as unknown as number;
    pollTimeoutRef.current = setTimeout(() => {
      clearPolling();
    }, 300000) as unknown as number;

    poll();
  };

  return (
    <div className="max-w-3xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-2">视频翻拍复刻</h1>
      <p className="text-zinc-400 mb-8 text-sm">
        输入爆款视频链接，上传你的模特和产品图，AI 自动翻拍出相同运镜和节奏的新视频
      </p>

      {/* 核心卖点 */}
      <div className="mb-8 p-4 rounded-lg bg-amber-500/10 border border-amber-500/30">
        <h3 className="text-sm font-semibold text-amber-400 mb-2">降维打击式创作</h3>
        <ul className="space-y-1 text-sm text-zinc-300">
          <li>• 提取原视频的运镜、节奏、动作</li>
          <li>• 模特和产品完全替换成你的素材</li>
          <li>• 生成高度逼真的翻拍视频</li>
          <li>• 商用级别，直接投放广告</li>
        </ul>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* 参考视频链接 */}
        <div>
          <label className="block text-sm text-zinc-400 mb-2">
            参考爆款视频 *
          </label>
          <label className="block w-full h-32 border-2 border-dashed border-zinc-700 rounded-lg hover:border-amber-500 transition-colors cursor-pointer flex items-center justify-center">
            <input type="file" accept="video/*" onChange={handleVideoUpload} className="hidden" />
            <div className="text-center text-zinc-500">
              <p className="text-2xl mb-2">🎬</p>
              <p className="text-sm">{videoUploading ? "上传中..." : referenceVideoUrl ? "✅ 视频已上传" : "点击上传参考视频"}</p>
            </div>
          </label>
          <p className="text-xs text-zinc-500 mt-2">支持 mp4、mov 等格式，建议 100MB 以内</p>
        </div>

        {/* 模特图片上传 */}
        <div>
          <label className="block text-sm text-zinc-400 mb-2">
            模特图片 *
          </label>
          <div className="space-y-3">
            <label className="block w-full h-32 border-2 border-dashed border-zinc-700 rounded-lg hover:border-amber-500 transition-colors cursor-pointer flex items-center justify-center">
              <input
                type="file"
                accept="image/*"
                onChange={(e) => handleImageUpload(e, setModelImage)}
                className="hidden"
              />
              <div className="text-center text-zinc-500">
                <p className="text-2xl mb-2">👤</p>
                <p className="text-sm">点击上传模特图</p>
              </div>
            </label>
            {modelImage && (
              <div className="relative inline-block">
                <img
                  src={modelImage}
                  alt="模特"
                  className="h-32 rounded-lg border border-zinc-700"
                />
                <button
                  type="button"
                  onClick={() => setModelImage(null)}
                  className="absolute top-2 right-2 w-6 h-6 bg-red-500 rounded-full text-white text-sm flex items-center justify-center hover:bg-red-600"
                >
                  ✕
                </button>
              </div>
            )}
          </div>
        </div>

        {/* 产品图片上传（可选） */}
        <div>
          <label className="block text-sm text-zinc-400 mb-2">
            产品图片 <span className="text-zinc-600">（可选）</span>
          </label>
          <div className="space-y-3">
            <label className="block w-full h-32 border-2 border-dashed border-zinc-700 rounded-lg hover:border-amber-500 transition-colors cursor-pointer flex items-center justify-center">
              <input
                type="file"
                accept="image/*"
                onChange={(e) => handleImageUpload(e, setProductImage)}
                className="hidden"
              />
              <div className="text-center text-zinc-500">
                <p className="text-2xl mb-2">📦</p>
                <p className="text-sm">点击上传产品图</p>
              </div>
            </label>
            {productImage && (
              <div className="relative inline-block">
                <img
                  src={productImage}
                  alt="产品"
                  className="h-32 rounded-lg border border-zinc-700"
                />
                <button
                  type="button"
                  onClick={() => setProductImage(null)}
                  className="absolute top-2 right-2 w-6 h-6 bg-red-500 rounded-full text-white text-sm flex items-center justify-center hover:bg-red-600"
                >
                  ✕
                </button>
              </div>
            )}
          </div>
        </div>

        {/* 提交按钮 */}
        <button
          type="submit"
          disabled={loading || !referenceVideoUrl || !modelImage}
          className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? "提交中..." : "开始翻拍"}
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
          <p className="text-xs text-zinc-500 mt-4">
            翻拍视频需要提取原视频的运镜和动作，预计需要 2-5 分钟
          </p>
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
          <li>• 选择运镜清晰、动作明显的爆款视频效果更好</li>
          <li>• 模特图建议为全身或半身照，背景简洁</li>
          <li>• 产品图清晰、无水印，主体突出</li>
          <li>• 生成时间约 2-5 分钟，请耐心等待</li>
          <li>• 生成的视频可直接用于广告投放</li>
        </ul>
      </div>
    </div>
  );
}
