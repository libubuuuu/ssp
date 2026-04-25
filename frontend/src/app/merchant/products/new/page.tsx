"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function NewProductPage() {
  const router = useRouter();
  const [formData, setFormData] = useState({
    name: "",
    description: "",
    category: "上衣",
    gender: "女装",
    price: "",
    stock: "",
    sizes: [] as string[],
  });

  const [images, setImages] = useState<string[]>([]);
  const [model3D, setModel3D] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const categories = ["上衣", "裤子", "裙子", "外套", "连衣裙", "配饰"];
  const genders = ["女装", "男装", "中性"];
  const sizeOptions = ["XS", "S", "M", "L", "XL", "XXL"];

  const handleSizeToggle = (size: string) => {
    setFormData(prev => ({
      ...prev,
      sizes: prev.sizes.includes(size)
        ? prev.sizes.filter(s => s !== size)
        : [...prev.sizes, size],
    }));
  };

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      setImages(prev => [...prev, event.target?.result as string]);
    };
    reader.readAsDataURL(file);
  };

  const handleModelUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // 模拟上传
    setModel3D("https://example.com/model.glb");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    // TODO: 调用 API 提交产品
    console.log("提交产品:", formData);

    setTimeout(() => {
      alert(t("alerts.productSubmitted"));
      router.push("/merchant/products");
    }, 1000);
  };

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* 顶部标题栏 */}
      <div className="border-b border-zinc-800 px-6 py-4">
        <div className="flex items-center gap-4">
          <Link href="/merchant/products" className="text-zinc-400 hover:text-white transition-colors">
            ← 返回产品列表
          </Link>
          <h1 className="text-2xl font-bold">新增产品</h1>
        </div>
      </div>

      {/* 表单区域 */}
      <form onSubmit={handleSubmit} className="max-w-4xl mx-auto p-6 space-y-6">
        {/* 基本信息 */}
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <h2 className="text-lg font-semibold mb-4 text-amber-400">基本信息</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-zinc-400 mb-2">产品名称</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
                placeholder="例如：经典白 T 恤"
                className="w-full px-4 py-2 rounded-lg bg-zinc-950 border border-zinc-700 focus:border-amber-500 outline-none"
                required
              />
            </div>
            <div>
              <label className="block text-sm text-zinc-400 mb-2">价格 (¥)</label>
              <input
                type="number"
                value={formData.price}
                onChange={(e) => setFormData(prev => ({ ...prev, price: e.target.value }))}
                placeholder="99"
                className="w-full px-4 py-2 rounded-lg bg-zinc-950 border border-zinc-700 focus:border-amber-500 outline-none"
                required
              />
            </div>
            <div>
              <label className="block text-sm text-zinc-400 mb-2">分类</label>
              <select
                value={formData.category}
                onChange={(e) => setFormData(prev => ({ ...prev, category: e.target.value }))}
                className="w-full px-4 py-2 rounded-lg bg-zinc-950 border border-zinc-700 focus:border-amber-500 outline-none"
              >
                {categories.map(cat => (
                  <option key={cat} value={cat}>{cat}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm text-zinc-400 mb-2">性别</label>
              <select
                value={formData.gender}
                onChange={(e) => setFormData(prev => ({ ...prev, gender: e.target.value }))}
                className="w-full px-4 py-2 rounded-lg bg-zinc-950 border border-zinc-700 focus:border-amber-500 outline-none"
              >
                {genders.map(g => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-4">
            <label className="block text-sm text-zinc-400 mb-2">产品描述</label>
            <textarea
              value={formData.description}
              onChange={(e) => setFormData(prev => ({ ...prev, description: e.target.value }))}
              placeholder="描述产品的特点、材质、款式等..."
              className="w-full px-4 py-2 rounded-lg bg-zinc-950 border border-zinc-700 focus:border-amber-500 outline-none h-24 resize-none"
            />
          </div>
        </div>

        {/* 尺码选择 */}
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <h2 className="text-lg font-semibold mb-4 text-amber-400">可选尺码</h2>
          <div className="flex flex-wrap gap-2">
            {sizeOptions.map(size => (
              <button
                key={size}
                type="button"
                onClick={() => handleSizeToggle(size)}
                className={`px-4 py-2 rounded-lg transition-colors ${
                  formData.sizes.includes(size)
                    ? "bg-amber-500 text-black"
                    : "bg-zinc-800 text-zinc-400 hover:text-white"
                }`}
              >
                {size}
              </button>
            ))}
          </div>
        </div>

        {/* 图片上传 */}
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <h2 className="text-lg font-semibold mb-4 text-amber-400">产品图片</h2>
          <div className="flex gap-4 overflow-x-auto pb-4">
            <label className="flex-shrink-0 w-32 h-32 rounded-lg border-2 border-dashed border-zinc-700 hover:border-amber-500 cursor-pointer bg-zinc-950 flex items-center justify-center transition-colors">
              <input
                type="file"
                accept="image/*"
                onChange={handleImageUpload}
                className="hidden"
              />
              <div className="text-center text-zinc-500">
                <div className="text-2xl mb-1">📷</div>
                <div className="text-xs">上传图片</div>
              </div>
            </label>
            {images.map((img, idx) => (
              <div key={idx} className="flex-shrink-0 w-32 h-32 rounded-lg overflow-hidden border border-zinc-700">
                <img src={img} alt={`产品图 ${idx + 1}`} className="w-full h-full object-cover" />
              </div>
            ))}
          </div>
        </div>

        {/* 3D 模型上传 */}
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <h2 className="text-lg font-semibold mb-4 text-amber-400">3D 服装模型</h2>
          <p className="text-zinc-400 text-sm mb-4">上传 .glb 或 .gltf 格式的 3D 服装模型，用于用户试穿预览</p>
          <label className="block w-full max-w-md h-32 rounded-lg border-2 border-dashed border-zinc-700 hover:border-amber-500 cursor-pointer bg-zinc-950 flex items-center justify-center transition-colors">
            <input
              type="file"
              accept=".glb,.gltf"
              onChange={handleModelUpload}
              className="hidden"
            />
            {model3D ? (
              <div className="text-center text-green-400">
                <div className="text-2xl mb-1">✓</div>
                <div className="text-sm">模型已上传</div>
              </div>
            ) : (
              <div className="text-center text-zinc-500">
                <div className="text-2xl mb-1">🎭</div>
                <div className="text-xs">点击上传 3D 模型</div>
              </div>
            )}
          </label>
        </div>

        {/* 库存 */}
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <h2 className="text-lg font-semibold mb-4 text-amber-400">库存管理</h2>
          <div className="max-w-xs">
            <label className="block text-sm text-zinc-400 mb-2">库存数量</label>
            <input
              type="number"
              value={formData.stock}
              onChange={(e) => setFormData(prev => ({ ...prev, stock: e.target.value }))}
              placeholder="100"
              className="w-full px-4 py-2 rounded-lg bg-zinc-950 border border-zinc-700 focus:border-amber-500 outline-none"
              required
            />
          </div>
        </div>

        {/* 提交按钮 */}
        <div className="flex gap-4 pt-4">
          <button
            type="submit"
            disabled={loading}
            className="flex-1 py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
          >
            {loading ? "提交中..." : "提交产品"}
          </button>
          <Link
            href="/merchant/products"
            className="px-8 py-3 rounded-lg bg-zinc-800 text-white font-medium hover:bg-zinc-700 transition-colors"
          >
            取消
          </Link>
        </div>
      </form>
    </div>
  );
}
