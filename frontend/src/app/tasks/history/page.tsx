"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Task {
  id: string;
  module: string;
  status: string;
  cost_credits: number;
  created_at: string;
}

export default function TaskHistoryPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    const storedToken = localStorage.getItem("token");
    if (!storedToken) {
      setError("请先登录");
      setLoading(false);
      return;
    }
    setToken(storedToken);
  }, []);

  useEffect(() => {
    if (!token) return;

    const fetchTasks = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/admin/tasks/recent`, {
          headers: {
            "Authorization": `Bearer ${token}`,
          },
        });

        if (res.status === 403) {
          setError("需要管理员权限");
          return;
        }

        const data = await res.json();
        if (data.tasks) {
          setTasks(data.tasks);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载失败");
      } finally {
        setLoading(false);
      }
    };

    fetchTasks();
  }, [token]);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "completed":
        return "bg-green-900/30 text-green-400 border border-green-800";
      case "failed":
        return "bg-red-900/30 text-red-400 border border-red-800";
      case "processing":
        return "bg-yellow-900/30 text-yellow-400 border border-yellow-800";
      default:
        return "bg-zinc-800 text-zinc-400 border border-zinc-700";
    }
  };

  const getModuleLabel = (module: string) => {
    const labels: Record<string, string> = {
      "image/style": "风格化图片",
      "image/realistic": "写实图片",
      "image/multi-reference": "多参考图",
      "video/image-to-video": "图生视频",
      "video/replace/element": "元素替换",
      "video/clone": "翻拍复刻",
      "avatar/generate": "数字人",
      "voice/clone": "语音克隆",
      "voice/tts": "TTS",
    };
    return labels[module] || module;
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-zinc-400">加载中...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen px-6 py-12">
      <div className="max-w-5xl mx-auto">
        <div className="mb-8">
          <h1 className="text-2xl font-bold mb-2">任务历史</h1>
          <p className="text-zinc-400">查看您最近的 AI 生成任务记录</p>
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-lg bg-red-900/20 border border-red-700">
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        )}

        {!error && tasks.length === 0 && (
          <div className="p-8 rounded-xl border border-zinc-800 bg-zinc-900/50 text-center">
            <div className="text-6xl mb-4">📋</div>
            <p className="text-zinc-400">暂无任务记录</p>
            <Link
              href="/"
              className="inline-block mt-4 px-6 py-2 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 transition-colors"
            >
              开始创作
            </Link>
          </div>
        )}

        {tasks.length > 0 && (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="text-left text-sm text-zinc-400 border-b border-zinc-800">
                  <th className="px-6 py-4">任务 ID</th>
                  <th className="px-6 py-4">模块</th>
                  <th className="px-6 py-4">状态</th>
                  <th className="px-6 py-4">消耗积分</th>
                  <th className="px-6 py-4">创建时间</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id} className="border-b border-zinc-800 last:border-0">
                    <td className="px-6 py-4 font-mono text-xs text-zinc-500">
                      {task.id.slice(0, 8)}...
                    </td>
                    <td className="px-6 py-4">{getModuleLabel(task.module)}</td>
                    <td className="px-6 py-4">
                      <span className={`px-2 py-1 rounded text-xs ${getStatusBadge(task.status)}`}>
                        {task.status === "completed" ? "已完成" :
                         task.status === "failed" ? "失败" :
                         task.status === "processing" ? "处理中" : "等待中"}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-amber-400">{task.cost_credits}</td>
                    <td className="px-6 py-4 text-zinc-500 text-sm">
                      {new Date(task.created_at).toLocaleString("zh-CN")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <Link
          href="/"
          className="inline-block mt-6 px-6 py-2 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors"
        >
          ← 返回主页
        </Link>
      </div>
    </div>
  );
}
