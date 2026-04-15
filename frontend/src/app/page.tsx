"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const [user, setUser] = useState<{ name: string; credits: number } | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem("user");
    if (stored) {
      setUser(JSON.parse(stored));
    }
  }, []);

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    setUser(null);
    window.location.reload();
  };

  return (
    <div className="min-h-screen px-6 py-12">
      {/* 顶部导航 */}
      <div className="max-w-7xl mx-auto mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-bold bg-gradient-to-r from-amber-400 to-orange-500 bg-clip-text text-transparent">
          AI 创意平台
        </h1>
        <div className="flex items-center gap-4">
          {user ? (
            <>
              <Link
                href="/pricing"
                className="text-sm text-zinc-400 hover:text-amber-400 transition-colors"
              >
                充值中心
              </Link>
              <span className="text-sm text-zinc-400">
                {user.name} · <span className="text-amber-400">{user.credits} 积分</span>
              </span>
              <button
                onClick={handleLogout}
                className="text-sm text-zinc-400 hover:text-white transition-colors"
              >
                退出
              </button>
            </>
          ) : (
            <Link
              href="/auth"
              className="px-4 py-2 rounded-lg bg-amber-500 text-black text-sm font-medium hover:bg-amber-400 transition-colors"
            >
              登录/注册
            </Link>
          )}
        </div>
      </div>

      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-16">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-amber-400 to-orange-500 bg-clip-text text-transparent">
            AI 创意平台
          </h1>
          <p className="text-zinc-400 max-w-2xl mx-auto">
            企业级 AI 商业内容创作平台 - 图片生成 · 视频生成 · 数字人 · 语音克隆
          </p>
        </div>

        {/* 核心功能 */}
        <h2 className="text-xl font-semibold mb-6">核心创作</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
          <Link
            href="/image"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">🖼️</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              图片生成
            </h3>
            <p className="text-zinc-500 text-sm">
              文生图、图生图、多参考图生图
            </p>
          </Link>

          <Link
            href="/video"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">🎬</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              视频生成
            </h3>
            <p className="text-zinc-500 text-sm">
              图生视频、元素替换、翻拍复刻
            </p>
          </Link>

          <Link
            href="/video/clone"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">🎥</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              视频翻拍
            </h3>
            <p className="text-zinc-500 text-sm">
              拿爆款视频换模特和产品
            </p>
          </Link>

          <Link
            href="/video/replace"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">🔄</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              元素替换
            </h3>
            <p className="text-zinc-500 text-sm">
              一键替换视频中的商品/人物
            </p>
          </Link>

          <Link
            href="/video/editor"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">✂️</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              视频剪辑台
            </h3>
            <p className="text-zinc-500 text-sm">
              分镜解析、多语言改写、时间轴重组
            </p>
          </Link>

          <Link
            href="/avatar"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">👤</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              数字人
            </h3>
            <p className="text-zinc-500 text-sm">
              只对口型，无多余动作
            </p>
          </Link>
        </div>

        {/* 语音和工具 */}
        <h2 className="text-xl font-semibold mb-6">语音与工具</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
          <Link
            href="/voice-clone"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">🎙️</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              语音克隆
            </h3>
            <p className="text-zinc-500 text-sm">
              5-10 秒提取音色，生成专属配音
            </p>
          </Link>

          <Link
            href="/image/multi-reference"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">🖼️</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              多参考图生图
            </h3>
            <p className="text-zinc-500 text-sm">
              拖拽排序决定权重，融合多图特征
            </p>
          </Link>

          <Link
            href="/admin/dashboard"
            className="block p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 hover:bg-zinc-900 transition-all group"
          >
            <span className="text-3xl mb-4 block">📊</span>
            <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-400 transition-colors">
              开发者后台
            </h3>
            <p className="text-zinc-500 text-sm">
              模型监控、熔断告警、任务队列
            </p>
          </Link>
        </div>

        {/* 底部信息 */}
        <div className="text-center text-zinc-600 text-sm">
          <p>模型熔断保护 · 并发任务控制 · 实时状态监控</p>
          <p className="mt-2">支持多窗口同步，高效稳定</p>
        </div>
      </div>
    </div>
  );
}
