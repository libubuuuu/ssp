"use client";

import { useState } from "react";
import Link from "next/link";

interface Product {
  id: string;
  name: string;
  category: string;
  gender: string;
  price: number;
  stock: number;
  isPublished: boolean;
  createdAt: string;
}

// 模拟产品数据
const sampleProducts: Product[] = [
  { id: "1", name: "经典白 T 恤", category: "上衣", gender: "女装", price: 99, stock: 100, isPublished: true, createdAt: "2026-04-01" },
  { id: "2", name: "修身牛仔裤", category: "裤子", gender: "女装", price: 299, stock: 50, isPublished: true, createdAt: "2026-04-02" },
  { id: "3", name: "连衣长裙", category: "裙子", gender: "女装", price: 399, stock: 30, isPublished: false, createdAt: "2026-04-03" },
  { id: "4", name: "休闲西装外套", category: "外套", gender: "男装", price: 599, stock: 20, isPublished: true, createdAt: "2026-04-04" },
  { id: "5", name: "商务衬衫", category: "上衣", gender: "男装", price: 199, stock: 80, isPublished: true, createdAt: "2026-04-05" },
];

export default function MerchantProductsPage() {
  const [products] = useState<Product[]>(sampleProducts);
  const [activeTab, setActiveTab] = useState<"all" | "published" | "draft">("all");
  const [searchQuery, setSearchQuery] = useState("");

  const filteredProducts = products.filter(product => {
    const matchesTab = activeTab === "all"
      ? true
      : activeTab === "published"
        ? product.isPublished
        : !product.isPublished;

    const matchesSearch = product.name.toLowerCase().includes(searchQuery.toLowerCase());

    return matchesTab && matchesSearch;
  });

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* 顶部标题栏 */}
      <div className="border-b border-zinc-800 px-6 py-4">
        <div className="flex items-center gap-4">
          <Link href="/merchant" className="text-zinc-400 hover:text-white transition-colors">
            ← 返回商家后台
          </Link>
          <h1 className="text-2xl font-bold">产品管理</h1>
        </div>
      </div>

      {/* 操作栏 */}
      <div className="p-6 border-b border-zinc-800">
        <div className="flex items-center justify-between gap-4">
          {/* 分类 Tab */}
          <div className="flex gap-2">
            <button
              onClick={() => setActiveTab("all")}
              className={`px-4 py-2 rounded-lg transition-colors ${
                activeTab === "all"
                  ? "bg-amber-500 text-black"
                  : "bg-zinc-800 text-zinc-400 hover:text-white"
              }`}
            >
              全部 ({products.length})
            </button>
            <button
              onClick={() => setActiveTab("published")}
              className={`px-4 py-2 rounded-lg transition-colors ${
                activeTab === "published"
                  ? "bg-amber-500 text-black"
                  : "bg-zinc-800 text-zinc-400 hover:text-white"
              }`}
            >
              已上架 ({products.filter(p => p.isPublished).length})
            </button>
            <button
              onClick={() => setActiveTab("draft")}
              className={`px-4 py-2 rounded-lg transition-colors ${
                activeTab === "draft"
                  ? "bg-amber-500 text-black"
                  : "bg-zinc-800 text-zinc-400 hover:text-white"
              }`}
            >
              草稿 ({products.filter(p => !p.isPublished).length})
            </button>
          </div>

          {/* 搜索框 */}
          <input
            type="text"
            placeholder="搜索产品..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="px-4 py-2 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none text-sm w-64"
          />

          {/* 新增按钮 */}
          <Link
            href="/merchant/products/new"
            className="px-6 py-2 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 transition-colors"
          >
            + 新增产品
          </Link>
        </div>
      </div>

      {/* 产品列表 */}
      <div className="p-6">
        <div className="rounded-xl border border-zinc-800 overflow-hidden">
          <table className="w-full">
            <thead className="bg-zinc-900 border-b border-zinc-800">
              <tr>
                <th className="text-left py-3 px-4 text-sm font-medium text-zinc-400">产品</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-zinc-400">分类</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-zinc-400">性别</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-zinc-400">价格</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-zinc-400">库存</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-zinc-400">状态</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-zinc-400">创建日期</th>
                <th className="text-right py-3 px-4 text-sm font-medium text-zinc-400">操作</th>
              </tr>
            </thead>
            <tbody>
              {filteredProducts.map((product) => (
                <tr key={product.id} className="border-b border-zinc-800 hover:bg-zinc-900/50 transition-colors">
                  <td className="py-3 px-4">
                    <span className="font-medium">{product.name}</span>
                  </td>
                  <td className="py-3 px-4 text-zinc-400">{product.category}</td>
                  <td className="py-3 px-4 text-zinc-400">{product.gender}</td>
                  <td className="py-3 px-4">¥{product.price}</td>
                  <td className="py-3 px-4 text-zinc-400">{product.stock}</td>
                  <td className="py-3 px-4">
                    <span className={`px-2 py-1 rounded text-xs ${
                      product.isPublished
                        ? "bg-green-900/30 text-green-400 border border-green-800"
                        : "bg-zinc-800 text-zinc-400 border border-zinc-700"
                    }`}>
                      {product.isPublished ? "已上架" : "草稿"}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-zinc-400 text-sm">{product.createdAt}</td>
                  <td className="py-3 px-4 text-right">
                    <button className="text-amber-400 hover:text-amber-300 text-sm mr-3">编辑</button>
                    <button className="text-zinc-400 hover:text-zinc-300 text-sm">删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {filteredProducts.length === 0 && (
          <div className="text-center py-12 text-zinc-500">
            <div className="text-4xl mb-2">📦</div>
            <p>暂无产品</p>
            <Link href="/merchant/products/new" className="text-amber-400 hover:underline mt-2 inline-block">
              添加第一个产品 →
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
