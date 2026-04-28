"use client";

import { useState } from "react";
import Link from "next/link";

// 模拟产品数据
const productDetails = {
  id: "1",
  name: "经典白 T 恤",
  price: 99,
  description: "简约经典的白色 T 恤，采用 100% 纯棉材质，透气舒适，适合日常穿搭。版型修身，展现完美身材比例。",
  images: ["👕", "👕", "👕", "👕"],
  category: "上衣",
  gender: "女装",
  sizes: ["XS", "S", "M", "L", "XL"],
  colors: ["白色", "黑色", "灰色"],
  material: "100% 棉",
  care: "机洗，低温烘干",
  stock: 100,
  rating: 4.8,
  reviews: 156,
};

export default function ProductDetailPage() {
  const [selectedSize, setSelectedSize] = useState<string>("M");
  const [selectedColor, setSelectedColor] = useState<string>("白色");
  const [quantity, setQuantity] = useState<number>(1);

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* 面包屑导航 */}
      <div className="border-b border-zinc-800 px-6 py-3">
        <div className="flex items-center gap-2 text-sm text-zinc-400">
          <Link href="/" className="hover:text-white">首页</Link>
          <span>/</span>
          <Link href="/products" className="hover:text-white">服装商城</Link>
          <span>/</span>
          <Link href="/products" className="hover:text-white">{productDetails.category}</Link>
          <span>/</span>
          <span className="text-white">{productDetails.name}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 p-6">
        {/* 左侧：产品图片 */}
        <div className="space-y-4">
          {/* 主图 */}
          <div className="aspect-square rounded-xl border border-zinc-800 bg-zinc-900/50 flex items-center justify-center text-9xl">
            {productDetails.images[0]}
          </div>
          {/* 缩略图 */}
          <div className="flex gap-2">
            {productDetails.images.map((img, idx) => (
              <div
                key={idx}
                className="w-20 h-20 rounded-lg border border-zinc-800 bg-zinc-900/50 flex items-center justify-center text-3xl cursor-pointer hover:border-amber-500 transition-colors"
              >
                {img}
              </div>
            ))}
          </div>
        </div>

        {/* 右侧：产品信息 */}
        <div className="space-y-6">
          <div>
            <h1 className="text-2xl font-bold">{productDetails.name}</h1>
            <div className="flex items-center gap-4 mt-2">
              <span className="text-amber-400 font-semibold text-xl">¥{productDetails.price}</span>
              <span className="text-zinc-400 text-sm">
                ⭐ {productDetails.rating} ({productDetails.reviews} 条评价)
              </span>
            </div>
          </div>

          <p className="text-zinc-400 text-sm leading-relaxed">{productDetails.description}</p>

          {/* 颜色选择 */}
          <div>
            <h3 className="text-sm font-medium mb-2">颜色</h3>
            <div className="flex gap-2">
              {productDetails.colors.map(color => (
                <button
                  key={color}
                  onClick={() => setSelectedColor(color)}
                  className={`px-4 py-2 rounded-lg border transition-colors ${
                    selectedColor === color
                      ? "border-amber-500 bg-amber-500/10 text-amber-400"
                      : "border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600"
                  }`}
                >
                  {color}
                </button>
              ))}
            </div>
          </div>

          {/* 尺码选择 */}
          <div>
            <h3 className="text-sm font-medium mb-2">尺码</h3>
            <div className="flex gap-2">
              {productDetails.sizes.map(size => (
                <button
                  key={size}
                  onClick={() => setSelectedSize(size)}
                  className={`w-12 h-12 rounded-lg border transition-colors ${
                    selectedSize === size
                      ? "border-amber-500 bg-amber-500/10 text-amber-400"
                      : "border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600"
                  }`}
                >
                  {size}
                </button>
              ))}
            </div>
            <Link href="/canvas" className="text-amber-400 text-sm hover:underline mt-2 inline-block">
              不确定尺码？→ 使用 3D 量体
            </Link>
          </div>

          {/* 数量选择 */}
          <div>
            <h3 className="text-sm font-medium mb-2">数量</h3>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setQuantity(Math.max(1, quantity - 1))}
                className="w-10 h-10 rounded-lg border border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-amber-500 transition-colors"
              >
                -
              </button>
              <span className="w-12 text-center">{quantity}</span>
              <button
                onClick={() => setQuantity(Math.min(productDetails.stock, quantity + 1))}
                className="w-10 h-10 rounded-lg border border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-amber-500 transition-colors"
              >
                +
              </button>
            </div>
          </div>

          {/* 购买按钮 */}
          <div className="flex gap-4 pt-4">
            <button className="flex-1 py-4 rounded-lg bg-amber-500 text-black font-semibold hover:bg-amber-400 transition-colors">
              加入购物车
            </button>
            <button className="flex-1 py-4 rounded-lg bg-green-600 text-white font-semibold hover:bg-green-500 transition-colors">
              立即购买
            </button>
          </div>

          {/* 产品信息 */}
          <div className="pt-6 border-t border-zinc-800 space-y-2 text-sm">
            <div className="flex justify-between text-zinc-400">
              <span>类别</span>
              <span>{productDetails.category} / {productDetails.gender}</span>
            </div>
            <div className="flex justify-between text-zinc-400">
              <span>材质</span>
              <span>{productDetails.material}</span>
            </div>
            <div className="flex justify-between text-zinc-400">
              <span>护理</span>
              <span>{productDetails.care}</span>
            </div>
            <div className="flex justify-between text-zinc-400">
              <span>库存</span>
              <span className="text-green-400">有货 ({productDetails.stock} 件)</span>
            </div>
          </div>

          {/* 3D 试穿入口 */}
          <div className="p-4 rounded-xl border border-amber-800/50 bg-amber-500/10">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="font-medium text-amber-400">🎭 3D 试穿</h3>
                <p className="text-zinc-400 text-sm mt-1">
                  使用您的 3D 模型预览上身效果
                </p>
              </div>
              <Link
                href="/try-on"
                className="px-6 py-2 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 transition-colors"
              >
                去试穿 →
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
