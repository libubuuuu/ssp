"use client";

import { useState, useRef } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export default function DigitalHumanPage() {
  const [script, setScript] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const imageRef = useRef<HTMLInputElement>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const imageFile = imageRef.current?.files?.[0];
    if (!imageFile) {
      alert(t("errors.uploadCharacter"));
      return;
    }
    setLoading(true);
    setTaskId(null);
    try {
      const formData = new FormData();
      formData.append("image", imageFile);
      formData.append("script", script);
      const res = await fetch(`${API_BASE}/api/digital-human/generate`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      setTaskId(data.task_id);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-2">数字人 AI</h1>
      <p className="text-zinc-500 text-sm mb-8">口型精准，无多余动作</p>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label className="block text-sm font-medium text-zinc-400 mb-2">
            人物图片
          </label>
          <input
            ref={imageRef}
            type="file"
            accept="image/*"
            className="w-full px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:bg-amber-500 file:text-black"
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-zinc-400 mb-2">
            脚本
          </label>
          <textarea
            value={script}
            onChange={(e) => setScript(e.target.value)}
            placeholder="输入数字人要说的内容..."
            className="w-full h-40 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none"
            required
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50"
        >
          {loading ? "生成中..." : "生成数字人视频"}
        </button>
      </form>

      {taskId && (
        <div className="mt-6 p-4 rounded-lg bg-zinc-900 border border-zinc-700">
          <p className="text-sm text-zinc-400">任务已提交</p>
          <p className="font-mono text-amber-400">{taskId}</p>
          <a
            href={`/tasks?id=${taskId}`}
            className="text-sm text-amber-400 hover:underline mt-2 inline-block"
          >
            查看任务状态 →
          </a>
        </div>
      )}
    </div>
  );
}
