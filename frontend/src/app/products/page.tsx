"use client";

import { useState } from "react";
import Link from "next/link";

interface Product {
  id: string;
  name: string;
  category: string;
  gender: string;
  price: number;
  image: string;
  isPublished: boolean;
}

// 模拟产品数据
const sampleProducts: Product[] = [
  { id: "1", name: "经典白 T 恤", category: "上衣", gender: "女装", price: 99, image: "👕", isPublished: true },
  { id: "2", name: "修身牛仔裤", category: "裤子", gender: "女装", price: 299, image: "👖", isPublished: true },
  { id: "3", name: "连衣长裙", category: "裙子", gender: "女装", price: 399, image: "👗", isPublished: true },
  { id: "4", name: "休闲西装外套", category: "外套", gender: "男装", price: 599, image: "🧥", isPublished: true },
  { id: "5", name: "商务衬衫", category: "上衣", gender: "男装", price: 199, image: "👔", isPublished: true },
  { id: "6", name: "运动卫衣", category: "上衣", gender: "中性", price: 249, image: "🧢", isPublished: true },
  { id: "7", name: "高腰半身裙", category: "裙子", gender: "女装", price: 179, image: "👘", isPublished: true },
  { id: "8", name: "羊毛大衣", category: "外套", gender: "女装", price: 899, image: "🧣", isPublished: true },
];

export default function ProductsPage() {
  const [activeCategory, setActiveCategory] = useState<string>("全部");
  const [genderFilter, setGenderFilter] = useState<string>("全部");
  const [priceRange, setPriceRange] = useState<[number, number]>([0, 1000]);

  const categories = ["全部", "上衣", "裤子", "裙子", "外套", "连衣裙", "配饰"];
  const genders = ["全部", "女装", "男装", "中性"];

  const filteredProducts = sampleProducts.filter(product => {
    const matchesCategory = activeCategory === "全部" || product.category === activeCategory;
    const matchesGender = genderFilter === "全部" || product.gender === genderFilter;
    const matchesPrice = product.price >= priceRange[0] && product.price <= priceRange[1];
    return matchesCategory && matchesGender && matchesPrice && product.isPublished;
  });

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* 顶部标题栏 */}
      <div className="border-b border-zinc-800 px-6 py-4">
        <h1 className="text-2xl font-bold">服装商城</h1>
        <p className="text-zinc-400 text-sm mt-1">精选服装，在线试穿</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 p-6">
        {/* 左侧筛选栏 */}
        <div className="space-y-6">
          {/* 分类筛选 */}
          <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50">
            <h2 className="text-sm font-semibold mb-3 text-amber-400">分类</h2>
            <div className="flex flex-wrap gap-2">
              {categories.map(cat => (
                <button
                  key={cat}
                  onClick={() => setActiveCategory(cat)}
                  className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                    activeCategory === cat
                      ? "bg-amber-500 text-black"
                      : "bg-zinc-800 text-zinc-400 hover:text-white"
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          {/* 性别筛选 */}
          <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50">
            <h2 className="text-sm font-semibold mb-3 text-amber-400">性别</h2>
            <div className="flex flex-col gap-2">
              {genders.map(gender => (
                <label key={gender} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="gender"
                    checked={genderFilter === gender}
                    onChange={() => setGenderFilter(gender)}
                    className="accent-amber-500"
                  />
                  <span className="text-zinc-400 text-sm">{gender}</span>
                </label>
              ))}
            </div>
          </div>

          {/* 价格区间 */}
          <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50">
            <h2 className="text-sm font-semibold mb-3 text-amber-400">价格区间</h2>
            <div className="flex items-center gap-2 text-zinc-400 text-sm">
              <input
                type="number"
                value={priceRange[0]}
                onChange={(e) => setPriceRange([Number(e.target.value), priceRange[1]])}
                className="w-full px-2 py-1 rounded bg-zinc-950 border border-zinc-700 text-white text-sm"
                placeholder="0"
              />
              <span>-</span>
              <input
                type="number"
                value={priceRange[1]}
                onChange={(e) => setPriceRange([priceRange[0], Number(e.target.value)])}
                className="w-full px-2 py-1 rounded bg-zinc-950 border border-zinc-700 text-white text-sm"
                placeholder="1000"
              />
            </div>
          </div>
        </div>

        {/* 右侧产品列表 */}
        <div className="lg:col-span-3">
          {/* 结果统计 */}
          <div className="flex items-center justify-between mb-4">
            <p className="text-zinc-400 text-sm">
              找到 <span className="text-white font-medium">{filteredProducts.length}</span> 件商品
            </p>
            <select className="px-3 py-2 rounded-lg bg-zinc-900 border border-zinc-700 text-sm text-zinc-400 focus:border-amber-500 outline-none">
              <option>默认排序</option>
              <option>价格从低到高</option>
              <option>价格从高到低</option>
              <option>最新上架</option>
            </select>
          </div>

          {/* 产品网格 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredProducts.map((product) => (
              <Link
                key={product.id}
                href={`/products/${product.id}`}
                className="group p-4 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500 transition-all"
              >
                <div className="aspect-square rounded-lg bg-zinc-950 flex items-center justify-center mb-4 text-6xl">
                  {product.image}
                </div>
                <h3 className="font-medium group-hover:text-amber-400 transition-colors">{product.name}</h3>
                <div className="flex items-center justify-between mt-2">
                  <span className="text-zinc-400 text-sm">{product.category} · {product.gender}</span>
                  <span className="text-amber-400 font-semibold">¥{product.price}</span>
                </div>
              </Link>
            ))}
          </div>

          {filteredProducts.length === 0 && (
            <div className="text-center py-12 text-zinc-500">
              <div className="text-4xl mb-2">🔍</div>
              <p>暂无符合条件的商品</p>
              <button
                onClick={() => {
                  setActiveCategory("全部");
                  setGenderFilter("全部");
                  setPriceRange([0, 1000]);
                }}
                className="text-amber-400 hover:underline mt-2"
              >
                清除筛选条件
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
