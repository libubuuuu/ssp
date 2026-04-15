"use client";

import Link from "next/link";

// 模拟统计数据
const stats = {
  totalProducts: 28,
  publishedProducts: 22,
  totalOrders: 156,
  pendingOrders: 8,
  totalRevenue: 45680,
  monthRevenue: 12890,
};

// 最近订单
const recentOrders = [
  { id: "ORD-001", product: "经典白 T 恤", quantity: 2, amount: 198, status: "pending", date: "2026-04-09" },
  { id: "ORD-002", product: "修身牛仔裤", quantity: 1, amount: 299, status: "paid", date: "2026-04-09" },
  { id: "ORD-003", product: "连衣长裙", quantity: 1, amount: 399, status: "shipping", date: "2026-04-08" },
  { id: "ORD-004", product: "休闲西装外套", quantity: 1, amount: 599, status: "delivered", date: "2026-04-08" },
  { id: "ORD-005", product: "商务衬衫", quantity: 3, amount: 597, status: "delivered", date: "2026-04-07" },
];

export default function MerchantDashboardPage() {
  return (
    <div className="min-h-screen bg-zinc-950">
      {/* 顶部标题栏 */}
      <div className="border-b border-zinc-800 px-6 py-4">
        <h1 className="text-2xl font-bold">商家后台</h1>
        <p className="text-zinc-400 text-sm mt-1">管理您的产品和订单</p>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 p-6">
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <div className="text-zinc-400 text-sm mb-2">总产品数</div>
          <div className="text-3xl font-bold text-amber-400">{stats.totalProducts}</div>
          <div className="text-zinc-500 text-xs mt-2">已上架 {stats.publishedProducts} 个</div>
        </div>
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <div className="text-zinc-400 text-sm mb-2">总订单数</div>
          <div className="text-3xl font-bold text-blue-400">{stats.totalOrders}</div>
          <div className="text-zinc-500 text-xs mt-2">待处理 {stats.pendingOrders} 个</div>
        </div>
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <div className="text-zinc-400 text-sm mb-2">本月收入</div>
          <div className="text-3xl font-bold text-green-400">¥{stats.monthRevenue.toLocaleString()}</div>
          <div className="text-zinc-500 text-xs mt-2">总收入 ¥{stats.totalRevenue.toLocaleString()}</div>
        </div>
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <div className="text-zinc-400 text-sm mb-2">店铺评分</div>
          <div className="text-3xl font-bold text-purple-400">4.8</div>
          <div className="text-zinc-500 text-xs mt-2">⭐⭐⭐⭐⭐</div>
        </div>
      </div>

      {/* 快捷入口 */}
      <div className="px-6 pb-6">
        <h2 className="text-lg font-semibold mb-4">快捷操作</h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Link
            href="/merchant/products/new"
            className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500 transition-colors group"
          >
            <div className="text-2xl mb-2">📦</div>
            <div className="font-medium group-hover:text-amber-400 transition-colors">新增产品</div>
            <div className="text-zinc-500 text-sm mt-1">上传新产品到店铺</div>
          </Link>
          <Link
            href="/merchant/products"
            className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500 transition-colors group"
          >
            <div className="text-2xl mb-2">📋</div>
            <div className="font-medium group-hover:text-amber-400 transition-colors">产品管理</div>
            <div className="text-zinc-500 text-sm mt-1">编辑和管理产品</div>
          </Link>
          <Link
            href="/merchant/orders"
            className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500 transition-colors group"
          >
            <div className="text-2xl mb-2">📝</div>
            <div className="font-medium group-hover:text-amber-400 transition-colors">订单管理</div>
            <div className="text-zinc-500 text-sm mt-1">处理客户订单</div>
          </Link>
          <Link
            href="/merchant/analytics"
            className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500 transition-colors group"
          >
            <div className="text-2xl mb-2">📊</div>
            <div className="font-medium group-hover:text-amber-400 transition-colors">数据分析</div>
            <div className="text-zinc-500 text-sm mt-1">查看销售数据</div>
          </Link>
        </div>
      </div>

      {/* 最近订单 */}
      <div className="px-6 pb-6">
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">最近订单</h2>
            <Link href="/merchant/orders" className="text-amber-400 text-sm hover:underline">
              查看全部 →
            </Link>
          </div>
          <table className="w-full">
            <thead>
              <tr className="text-left text-sm text-zinc-400 border-b border-zinc-800">
                <th className="pb-3">订单号</th>
                <th className="pb-3">商品</th>
                <th className="pb-3">数量</th>
                <th className="pb-3">金额</th>
                <th className="pb-3">状态</th>
                <th className="pb-3">日期</th>
              </tr>
            </thead>
            <tbody>
              {recentOrders.map((order) => (
                <tr key={order.id} className="border-b border-zinc-800 last:border-0">
                  <td className="py-3 font-medium">{order.id}</td>
                  <td className="py-3 text-zinc-400">{order.product}</td>
                  <td className="py-3 text-zinc-400">{order.quantity}</td>
                  <td className="py-3">¥{order.amount}</td>
                  <td className="py-3">
                    <span className={`px-2 py-1 rounded text-xs ${
                      order.status === "pending" ? "bg-yellow-900/30 text-yellow-400 border border-yellow-800" :
                      order.status === "paid" ? "bg-blue-900/30 text-blue-400 border border-blue-800" :
                      order.status === "shipping" ? "bg-purple-900/30 text-purple-400 border border-purple-800" :
                      "bg-green-900/30 text-green-400 border border-green-800"
                    }`}>
                      {order.status === "pending" ? "待处理" :
                       order.status === "paid" ? "已付款" :
                       order.status === "shipping" ? "配送中" : "已完成"}
                    </span>
                  </td>
                  <td className="py-3 text-zinc-400 text-sm">{order.date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
