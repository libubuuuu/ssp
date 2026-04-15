"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ModelInfo {
  endpoint: string;
  label: string;
  desc: string;
}

const DEFAULT_MODELS: Record<string, ModelInfo> = {
  "nano-banana-2": {
    endpoint: "fal-ai/nano-banana-2",
    label: "经济模式",
    desc: "最低成本，速度较慢",
  },
  "flux/schnell": {
    endpoint: "fal-ai/flux/schnell",
    label: "快速模式",
    desc: "生成速度快，质量高",
  },
};

export default function ImagePage() {
  const router = useRouter();
  const [mode, setMode] = useState<"style" | "realistic" | "multi-reference">("style");
  const [prompt, setPrompt] = useState("");
  const [style, setStyle] = useState("advertising");
  const [model, setModel] = useState("nano-banana-2");
  const [models, setModels] = useState(DEFAULT_MODELS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/image/models`)
      .then((r) => r.json())
      .then((data) => {
        if (data.models) setModels(data.models);
      })
      .catch(() => {});
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const endpoint = mode === "style" ? "/api/image/style" : "/api/image/realistic";
      const body: Record<string, unknown> = { prompt, model };
      if (mode === "style") {
        body.style = style;
        body.size = "1024x1024";
      }

      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();

      if (data.success) {
        // 跳转到详情页
        const detailData = {
          image_url: data.image_url,
          width: data.width,
          height: data.height,
          model: data.model,
          model_label: data.model_label,
          prompt,
          style,
          content_type: "image",
        };
        const encoded = encodeURIComponent(JSON.stringify(detailData));
        router.push(`/detail?data=${encoded}`);
      } else {
        setError(data.detail || data.error || "生成失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-8">图片生成</h1>

      {/* 模型选择 */}
      <div className="flex gap-3 mb-6">
        {Object.entries(models).map(([key, info]) => (
          <button
            key={key}
            onClick={() => setModel(key)}
            className={`px-4 py-2 rounded-lg transition-colors text-left ${
              model === key
                ? "bg-amber-500 text-black"
                : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
            }`}
          >
            <div className="font-medium text-sm">{info.label}</div>
            <div className={`text-xs ${model === key ? "text-black/60" : "text-zinc-500"}`}>
              {info.desc}
            </div>
          </button>
        ))}
      </div>

      {/* 模式切换 */}
      <div className="flex flex-wrap gap-3 mb-6">
        <button
          onClick={() => setMode("style")}
          className={`px-4 py-2 rounded-lg transition-colors ${
            mode === "style" ? "bg-amber-500 text-black" : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
          }`}
        >
          风格化 / 广告级
        </button>
        <button
          onClick={() => setMode("realistic")}
          className={`px-4 py-2 rounded-lg transition-colors ${
            mode === "realistic" ? "bg-amber-500 text-black" : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
          }`}
        >
          写实 / 可控
        </button>
        <button
          onClick={() => router.push("/image/multi-reference")}
          className={`px-4 py-2 rounded-lg transition-colors ${
            mode === "multi-reference" ? "bg-amber-500 text-black" : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
          }`}
        >
          多参考图生图
        </button>
      </div>

      {/* 风格选择（仅风格化模式） */}
      {mode === "style" && (
        <div className="flex gap-3 mb-6">
          {[
            { value: "advertising", label: "广告视觉效果" },
            { value: "minimalist", label: "精致简约风" },
            { value: "custom", label: "仅提示词" },
          ].map((s) => (
            <button
              key={s.value}
              onClick={() => setStyle(s.value)}
              className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                style === s.value
                  ? "bg-amber-500/20 text-amber-400 border border-amber-500"
                  : "bg-zinc-900 text-zinc-400 border border-zinc-700"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}

      {/* 输入表单 */}
      <form onSubmit={handleSubmit} className="space-y-4">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="描述你想要的图片..."
          className="w-full h-32 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none"
          required
        />
        <button
          type="submit"
          disabled={loading}
          className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
        >
          {loading ? "生成中..." : "生成图片"}
        </button>
      </form>

      {/* 错误提示 */}
      {error && (
        <div className="mt-6 p-4 rounded-lg bg-red-900/20 border border-red-700">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}
    </div>
  );
}
