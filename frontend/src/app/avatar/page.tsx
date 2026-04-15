"use client";

import { useState, useRef, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function AvatarPage() {
  const [characterImage, setCharacterImage] = useState<string | null>(null);
  const [audioFile, setAudioFile] = useState<string | null>(null);
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

  // 组件卸载时清理轮询
  useEffect(() => {
    return clearPolling;
  }, []);

  // 处理图片上传
  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      setCharacterImage(e.target?.result as string);
    };
    reader.readAsDataURL(file);
  };

  // 处理音频上传
  const handleAudioUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      setAudioFile(e.target?.result as string);
    };
    reader.readAsDataURL(file);
  };

  // 提交生成任务
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!characterImage || !audioFile) {
      setError("请上传人物图片和音频文件");
      return;
    }

    setLoading(true);
    setTaskId(null);
    setResultVideoUrl(null);
    setError(null);
    clearPolling();

    try {
      const res = await fetch(`${API_BASE}/api/avatar/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          character_image_url: characterImage,
          audio_url: audioFile,
          model: "hunyuan-avatar",
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
    }, 180000) as unknown as number;

    // 立即执行一次
    poll();
  };

  return (
    <div className="max-w-3xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-2">数字人 AI</h1>
      <p className="text-zinc-400 mb-8 text-sm">
        上传人物半身照和音频，AI 生成精准口型同步的数字人视频
      </p>

      {/* 核心卖点 */}
      <div className="mb-8 p-4 rounded-lg bg-amber-500/10 border border-amber-500/30">
        <h3 className="text-sm font-semibold text-amber-400 mb-2">克制型数字人</h3>
        <ul className="space-y-1 text-sm text-zinc-300">
          <li>• 仅驱动面部表情和唇形，无多余手势</li>
          <li>• 口型与音频精准同步</li>
          <li>• 适合知识付费、口播带货</li>
          <li>• 专业感强，不浮夸</li>
        </ul>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* 人物图片上传 */}
        <div>
          <label className="block text-sm text-zinc-400 mb-2">
            人物半身照 *
          </label>
          <div className="space-y-3">
            <label className="block w-full h-40 border-2 border-dashed border-zinc-700 rounded-lg hover:border-amber-500 transition-colors cursor-pointer flex items-center justify-center">
              <input
                type="file"
                accept="image/*"
                onChange={handleImageUpload}
                className="hidden"
              />
              <div className="text-center text-zinc-500">
                <p className="text-2xl mb-2">👤</p>
                <p className="text-sm">点击上传人物图片</p>
                <p className="text-xs mt-1">建议：正面或 45 度半身照，背景简洁</p>
              </div>
            </label>
            {characterImage && (
              <div className="relative inline-block">
                <img
                  src={characterImage}
                  alt="人物"
                  className="h-40 rounded-lg border border-zinc-700"
                />
                <button
                  type="button"
                  onClick={() => setCharacterImage(null)}
                  className="absolute top-2 right-2 w-6 h-6 bg-red-500 rounded-full text-white text-sm flex items-center justify-center hover:bg-red-600"
                >
                  ✕
                </button>
              </div>
            )}
          </div>
        </div>

        {/* 音频上传 */}
        <div>
          <label className="block text-sm text-zinc-400 mb-2">
            音频文件 *
          </label>
          <div className="space-y-3">
            <label className="block w-full h-32 border-2 border-dashed border-zinc-700 rounded-lg hover:border-amber-500 transition-colors cursor-pointer flex items-center justify-center">
              <input
                type="file"
                accept="audio/*"
                onChange={handleAudioUpload}
                className="hidden"
              />
              <div className="text-center text-zinc-500">
                <p className="text-2xl mb-2">🎙️</p>
                <p className="text-sm">点击上传音频</p>
                <p className="text-xs mt-1">支持 MP3、WAV 格式</p>
              </div>
            </label>
            {audioFile && (
              <div className="p-3 rounded-lg bg-zinc-900 border border-zinc-700">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">🔊</span>
                  <span className="text-sm text-zinc-400 flex-1">
                    已上传音频文件
                  </span>
                  <button
                    type="button"
                    onClick={() => setAudioFile(null)}
                    className="w-6 h-6 bg-red-500 rounded-full text-white text-sm flex items-center justify-center hover:bg-red-600"
                  >
                    ✕
                  </button>
                </div>
                <audio src={audioFile} controls className="w-full mt-2" />
              </div>
            )}
          </div>
        </div>

        {/* 提交按钮 */}
        <button
          type="submit"
          disabled={loading || !characterImage || !audioFile}
          className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? "提交中..." : "生成数字人视频"}
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
            数字人视频生成需要 1-3 分钟，请耐心等待
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
            <span className="text-sm text-zinc-400">Hunyuan Avatar</span>
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
          <li>• 人物图片建议为正面或 45 度半身照</li>
          <li>• 背景简洁，光线均匀，面部清晰</li>
          <li>• 音频质量越高，口型同步效果越好</li>
          <li>• 适合口播、知识付费、产品讲解场景</li>
          <li>• 生成时间约 1-3 分钟</li>
        </ul>
      </div>
    </div>
  );
}
