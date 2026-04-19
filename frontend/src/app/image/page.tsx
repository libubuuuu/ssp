"use client";
import { useState, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://43.134.71.189:8000";

const STYLES = [
  { key: "advertising", label: "广告视觉" },
  { key: "minimalist", label: "精致简约" },
  { key: "custom", label: "仅提示词" },
];

const MODELS = [
  { key: "nano-banana-2", label: "经济模式", desc: "最低成本，速度较慢" },
  { key: "flux/schnell", label: "快速模式", desc: "生成速度快，质量高" },
  { key: "flux/dev", label: "专业模式", desc: "更高质量的生成效果" },
];

const SIZES = [
  { key: "1024x1024", label: "正方形 1:1" },
  { key: "768x1024", label: "竖版 3:4" },
  { key: "1024x768", label: "横版 4:3" },
];

export default function ImagePage() {
  const [activeTab, setActiveTab] = useState<"generate" | "gallery">("generate");
  const [prompt, setPrompt] = useState("");
  const [style, setStyle] = useState("advertising");
  const [model, setModel] = useState("nano-banana-2");
  const [size, setSize] = useState("1024x1024");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [gallery, setGallery] = useState<{ url: string; prompt: string; time: number }[]>([]);
  const [latestImage, setLatestImage] = useState<string | null>(null);

  useEffect(() => {
    const saved = localStorage.getItem("img_gallery");
    if (saved) {
      try {
        setGallery(JSON.parse(saved));
      } catch {}
    }
  }, []);

  const saveGallery = (g: { url: string; prompt: string; time: number }[]) => {
    setGallery(g);
    localStorage.setItem("img_gallery", JSON.stringify(g.slice(0, 50)));
  };

  const generate = async () => {
    if (!prompt.trim()) { setError("请输入提示词"); return; }
    setError(""); setLoading(true); setLatestImage(null);
    try {
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/image/style`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ prompt, style, model, size }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "生成失败");
      const url = data.image_url || data.url || data.data?.image_url;
      if (!url) throw new Error("未返回图片");
      const newEntry = { url, prompt, time: Date.now() };
      setLatestImage(url);
      saveGallery([newEntry, ...gallery]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-2">图片创作引擏</h1>
      <p className="text-zinc-400 mb-8 text-sm">
        输入提示词，选择风格与模式，一键生成专属图片
      </p>

      <div className="flex gap-3 mb-8">
        <button
          onClick={() => setActiveTab("generate")}
          className={`px-4 py-2 rounded-lg transition-colors ${
            activeTab === "generate"
              ? "bg-amber-500 text-black font-medium"
              : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
          }`}
        >
          图片生成
        </button>
        <button
          onClick={() => setActiveTab("gallery")}
          className={`px-4 py-2 rounded-lg transition-colors ${
            activeTab === "gallery"
              ? "bg-amber-500 text-black font-medium"
              : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
          }`}
        >
          我的画廊{gallery.length > 0 ? ` (${gallery.length})` : ""}
        </button>
      </div>

      {/* 图片生成 */}
      {activeTab === "generate" && (
        <div className="space-y-6">
          {/* 生成模式 */}
          <div>
            <label className="block text-sm text-zinc-400 mb-2">生成模式 *</label>
            <div className="flex flex-col gap-2">
              {MODELS.map((m) => (
                <button
                  key={m.key}
                  onClick={() => setModel(m.key)}
                  className={`text-left px-4 py-3 rounded-lg border transition-colors ${
                    model === m.key
                      ? "border-amber-500 bg-amber-500/10"
                      : "border-zinc-700 bg-zinc-900 hover:border-zinc-500"
                  }`}
                >
                  <div className="font-medium text-zinc-200 text-sm">{m.label}</div>
                  <div className="text-xs text-zinc-500 mt-0.5">{m.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* 风格选择 */}
          <div>
            <label className="block text-sm text-zinc-400 mb-2">风格</label>
            <div className="flex gap-2 flex-wrap">
              {STYLES.map((s) => (
                <button
                  key={s.key}
                  onClick={() => setStyle(s.key)}
                  className={`px-4 py-2 rounded-full border text-sm transition-colors ${
                    style === s.key
                      ? "border-amber-500 bg-amber-500/10 text-amber-400"
                      : "border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-500"
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          {/* 尺寸选择 */}
          <div>
            <label className="block text-sm text-zinc-400 mb-2">尺寸</label>
            <div className="flex gap-2 flex-wrap">
              {SIZES.map((s) => (
                <button
                  key={s.key}
                  onClick={() => setSize(s.key)}
                  className={`px-4 py-2 rounded-full border text-sm transition-colors ${
                    size === s.key
                      ? "border-amber-500 bg-amber-500/10 text-amber-400"
                      : "border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-500"
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          {/* 提示词 */}
          <div>
            <label className="block text-sm text-zinc-400 mb-2">提示词 *</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="描述你想要的图片，例如：一只在星空下奔跑的狐狸，吉卜力风格..."
              className="w-full h-32 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none text-sm"
            />
          </div>

          {/* 错误提示 */}
          {error && (
            <div className="p-4 rounded-lg bg-red-900/20 border border-red-700">
              <p className="text-red-400 text-sm">{error}</p>
            </div>
          )}

          {/* 生成按钮 */}
          <button
            onClick={generate}
            disabled={loading || !prompt.trim()}
            className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? "AI 正在创作..." : "生成图片"}
          </button>

          {/* 加载动画 */}
          {loading && (
            <div className="flex flex-col items-center py-6">
              <div className="w-10 h-10 border-[3px] border-zinc-700 border-t-amber-500 rounded-full animate-spin" />
              <p className="text-zinc-500 text-sm mt-3">正在为您创作，请稍候...</p>
            </div>
          )}

          {/* 最新生成结果 */}
          {latestImage && !loading && (
            <div className="p-6 rounded-lg bg-zinc-900 border border-zinc-700">
              <p className="text-sm text-zinc-400 mb-3">生成结果</p>
              <img
                src={latestImage}
                alt="生成的图片"
                className="w-full rounded-lg object-contain max-h-[500px]"
              />
              <div className="flex items-center justify-between mt-4">
                <span className="text-sm text-zinc-500 truncate flex-1 mr-4">
                  {prompt.slice(0, 60)}{prompt.length > 60 ? "..." : ""}
                </span>
                <a
                  href={latestImage}
                  download="generated.png"
                  target="_blank"
                  className="px-4 py-2 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors text-sm whitespace-nowrap"
                >
                  下载图片
                </a>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 我的画廊 */}
      {activeTab === "gallery" && (
        <div className="space-y-4">
          {gallery.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-zinc-500">
              <div className="text-5xl mb-4">🖼️</div>
              <p className="text-sm">还没有作品，去生成你的第一张图片吧</p>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm text-zinc-400">共 {gallery.length} 张作品</p>
                <button
                  onClick={() => { if (confirm("确定清空所有作品？")) saveGallery([]); }}
                  className="text-sm text-zinc-500 hover:text-red-400 transition-colors"
                >
                  清空画廊
                </button>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {gallery.map((item, i) => (
                  <div
                    key={i}
                    className="rounded-xl overflow-hidden bg-zinc-900 border border-zinc-800 cursor-pointer hover:border-zinc-600 transition-colors"
                    onClick={() => { setLatestImage(item.url); setActiveTab("generate"); }}
                  >
                    <img src={item.url} alt="" className="w-full aspect-square object-cover" />
                    <div className="px-3 py-2">
                      <p className="text-xs text-zinc-500 truncate">{item.prompt}</p>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* 使用提示 */}
      {activeTab === "generate" && (
        <div className="mt-12 p-6 rounded-lg bg-zinc-900/50 border border-zinc-800">
          <h3 className="text-sm font-semibold text-zinc-300 mb-3">使用提示</h3>
          <ul className="space-y-2 text-sm text-zinc-500">
            <li>• 提示词越详细，生成效果越好</li>
            <li>• 可以指定画面风格、色调、构图方式</li>
            <li>• 经济模式速度较慢但更省积分</li>
            <li>• 生成的图片将自动保存到「我的画廊」</li>
          </ul>
        </div>
      )}
    </div>
  );
}
