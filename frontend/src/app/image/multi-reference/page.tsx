"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface ReferenceImage {
  id: string;
  url: string;
  weight: number;
}

export default function MultiReferencePage() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [referenceImages, setReferenceImages] = useState<ReferenceImage[]>([]);
  const [style, setStyle] = useState("custom");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);

  // 添加参考图
  const handleAddImage = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      const url = e.target?.result as string;
      setReferenceImages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          url,
          weight: 0, // 权重根据顺序动态计算
        },
      ]);
    };
    reader.readAsDataURL(file);
  };

  // 删除参考图
  const handleRemoveImage = (id: string) => {
    setReferenceImages((prev) => prev.filter((img) => img.id !== id));
  };

  // 拖拽开始
  const handleDragStart = (index: number) => {
    setDraggedIndex(index);
  };

  // 拖拽结束
  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
  };

  // 拖拽放置
  const handleDrop = (e: React.DragEvent, dropIndex: number) => {
    e.preventDefault();
    if (draggedIndex === null || draggedIndex === dropIndex) return;

    const newImages = [...referenceImages];
    const draggedItem = newImages[draggedIndex];
    newImages.splice(draggedIndex, 1);
    newImages.splice(dropIndex, 0, draggedItem);

    setReferenceImages(newImages);
    setDraggedIndex(null);
  };

  // 计算权重（顺序越靠前权重越高）
  const calculateWeights = () => {
    const total = referenceImages.length;
    return referenceImages.map((_, index) => {
      // 简单权重分配：第一张 50%, 第二张 30%, 第三张 20%, 其余平均
      if (index === 0) return 0.5;
      if (index === 1) return 0.3;
      if (index === 2) return 0.2;
      return 0.1 / (total - 3);
    });
  };

  // 提交生成
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || referenceImages.length === 0) {
      setError(t("errors.noPromptOrRef"));
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/api/image/multi-reference`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt,
          reference_images: referenceImages.map((img) => img.url),
          style,
          size: "1024x1024",
          model: "nano-banana-2",
        }),
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
          reference_count: data.reference_count,
          content_type: "image",
        };
        const encoded = encodeURIComponent(JSON.stringify(detailData));
        router.push(`/detail?data=${encoded}`);
      } else {
        setError(data.detail || data.error || t("errors.generationFailed"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errors.networkError"));
    } finally {
      setLoading(false);
    }
  };

  const weights = calculateWeights();

  return (
    <div className="max-w-4xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-8">多参考图生图</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* 左侧：参考图上传 */}
        <div>
          <h2 className="text-lg font-semibold mb-4 text-zinc-300">参考图</h2>

          {/* 上传区域 */}
          <label className="block w-full h-40 border-2 border-dashed border-zinc-700 rounded-lg hover:border-amber-500 transition-colors cursor-pointer flex items-center justify-center mb-4">
            <input
              type="file"
              accept="image/*"
              onChange={handleAddImage}
              className="hidden"
            />
            <div className="text-center text-zinc-500">
              <p className="text-2xl mb-2">📁</p>
              <p className="text-sm">点击上传图片</p>
              <p className="text-xs mt-1">支持拖拽排序，越靠前权重越高</p>
            </div>
          </label>

          {/* 参考图列表 */}
          {referenceImages.length > 0 && (
            <div className="space-y-2">
              {referenceImages.map((img, index) => (
                <div
                  key={img.id}
                  draggable
                  onDragStart={() => handleDragStart(index)}
                  onDragOver={(e) => handleDragOver(e, index)}
                  onDrop={(e) => handleDrop(e, index)}
                  className={`flex items-center gap-3 p-2 rounded-lg border ${
                    draggedIndex === index
                      ? "border-amber-500 bg-amber-500/10"
                      : "border-zinc-700 bg-zinc-900"
                  } cursor-grab active:cursor-grabbing`}
                >
                  <span className="text-zinc-500 text-sm w-6">{index + 1}</span>
                  <img
                    src={img.url}
                    alt={`参考图 ${index + 1}`}
                    className="w-12 h-12 object-cover rounded"
                  />
                  <span className="text-xs text-amber-400 w-20">
                    权重 {(weights[index] * 100).toFixed(0)}%
                  </span>
                  <button
                    onClick={() => handleRemoveImage(img.id)}
                    className="ml-auto text-zinc-500 hover:text-red-400 transition-colors"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右侧：提示词和设置 */}
        <div>
          <h2 className="text-lg font-semibold mb-4 text-zinc-300">提示词设置</h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* 风格选择 */}
            <div>
              <label className="block text-sm text-zinc-400 mb-2">风格</label>
              <div className="flex flex-wrap gap-2">
                {[
                  { value: "custom", label: "仅参考图" },
                  { value: "advertising", label: "广告视觉效果" },
                  { value: "minimalist", label: "精致简约风" },
                ].map((s) => (
                  <button
                    key={s.value}
                    type="button"
                    onClick={() => setStyle(s.value)}
                    className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                      style === s.value
                        ? "bg-amber-500 text-black"
                        : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>

            {/* 提示词输入 */}
            <div>
              <label className="block text-sm text-zinc-400 mb-2">
                提示词（可选，补充描述）
              </label>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="描述你希望在参考图基础上做出的改变..."
                className="w-full h-32 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none"
              />
            </div>

            {/* 信息提示 */}
            <p className="text-sm text-zinc-500">
              系统会根据参考图的顺序自动分配权重，第一张图片影响最大（50%）
            </p>

            {/* 提交按钮 */}
            <button
              type="submit"
              disabled={loading || referenceImages.length === 0}
              className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "生成中..." : "生成图片"}
            </button>
          </form>

          {/* 错误提示 */}
          {error && (
            <div className="mt-4 p-4 rounded-lg bg-red-900/20 border border-red-700">
              <p className="text-red-400 text-sm">{error}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
