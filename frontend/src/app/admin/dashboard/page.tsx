"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ModelStatus {
  model_name: string;
  state: string;
  failures: number;
  successes: number;
  last_failure: string | null;
}

interface QueueStatus {
  total_running: number;
  total_queued: number;
  user_stats: Record<string, { running: number; queued: number }>;
}

interface StatsOverview {
  total_users: number;
  total_tasks: number;
  today_tasks: number;
  today_revenue: number;
  model_usage: { model: string; count: number }[];
  task_status: { status: string; count: number }[];
}

export default function AdminDashboard() {
  const router = useRouter();
  const [models, setModels] = useState<ModelStatus[]>([]);
  const [queue, setQueue] = useState<QueueStatus | null>(null);
  const [stats, setStats] = useState<StatsOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000); // 每 5 秒刷新
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      const [modelsRes, queueRes, statsRes] = await Promise.all([
        fetch(`${API_BASE}/api/admin/models/status`),
        fetch(`${API_BASE}/api/admin/queue/status`),
        fetch(`${API_BASE}/api/admin/stats/overview`),
      ]);

      const modelsData = await modelsRes.json();
      const queueData = await queueRes.json();
      const statsData = await statsRes.json();

      setModels(modelsData.models || []);
      setQueue(queueData);
      setStats(statsData);
      setLoading(false);
    } catch (err) {
      console.error(err);
      setLoading(false);
    }
  };

  const handleResetModel = async (modelName: string) => {
    if (!confirm(`确定要重置模型 ${modelName} 的状态吗？`)) return;

    try {
      await fetch(`${API_BASE}/api/admin/models/${modelName}/reset`, {
        method: "POST",
      });
      alert("模型已重置");
      loadData();
    } catch (err) {
      alert("重置失败");
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-950 p-12">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-2xl font-bold text-zinc-400">加载中...</h1>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 p-12">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-white">开发者后台</h1>
            <p className="text-zinc-400 mt-1">AI 创意平台监控系统</p>
          </div>
          <button
            onClick={() => router.push("/")}
            className="px-4 py-2 text-zinc-400 hover:text-white transition-colors"
          >
            返回前台
          </button>
        </div>

        {/* 统计概览 */}
        {stats && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
            <div className="p-6 rounded-xl bg-zinc-900 border border-zinc-800">
              <p className="text-zinc-500 text-sm">总用户数</p>
              <p className="text-3xl font-bold text-white mt-2">{stats.total_users}</p>
            </div>
            <div className="p-6 rounded-xl bg-zinc-900 border border-zinc-800">
              <p className="text-zinc-500 text-sm">总任务数</p>
              <p className="text-3xl font-bold text-white mt-2">{stats.total_tasks}</p>
            </div>
            <div className="p-6 rounded-xl bg-zinc-900 border border-zinc-800">
              <p className="text-zinc-500 text-sm">今日任务</p>
              <p className="text-3xl font-bold text-amber-400 mt-2">{stats.today_tasks}</p>
            </div>
            <div className="p-6 rounded-xl bg-zinc-900 border border-zinc-800">
              <p className="text-zinc-500 text-sm">今日收入</p>
              <p className="text-3xl font-bold text-green-400 mt-2">¥{stats.today_revenue}</p>
            </div>
          </div>
        )}

        {/* 模型健康状态 */}
        <div className="mb-8">
          <h2 className="text-xl font-bold text-white mb-4">模型健康状态</h2>
          <div className="rounded-xl bg-zinc-900 border border-zinc-800 overflow-hidden">
            <table className="w-full">
              <thead className="bg-zinc-900/50 border-b border-zinc-800">
                <tr>
                  <th className="text-left p-4 text-zinc-400 font-medium">模型名称</th>
                  <th className="text-left p-4 text-zinc-400 font-medium">状态</th>
                  <th className="text-left p-4 text-zinc-400 font-medium">成功</th>
                  <th className="text-left p-4 text-zinc-400 font-medium">失败</th>
                  <th className="text-left p-4 text-zinc-400 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => (
                  <tr key={model.model_name} className="border-b border-zinc-800/50">
                    <td className="p-4 text-white font-mono">{model.model_name}</td>
                    <td className="p-4">
                      <span
                        className={`px-2 py-1 rounded text-xs font-medium ${
                          model.state === "closed"
                            ? "bg-green-900/30 text-green-400"
                            : model.state === "open"
                            ? "bg-red-900/30 text-red-400"
                            : "bg-yellow-900/30 text-yellow-400"
                        }`}
                      >
                        {model.state}
                      </span>
                    </td>
                    <td className="p-4 text-green-400">{model.successes}</td>
                    <td className="p-4 text-red-400">{model.failures}</td>
                    <td className="p-4">
                      {model.state === "open" && (
                        <button
                          onClick={() => handleResetModel(model.model_name)}
                          className="px-3 py-1 text-xs bg-amber-500 text-black rounded hover:bg-amber-400 transition-colors"
                        >
                          重置
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {models.length === 0 && (
                  <tr>
                    <td colSpan={5} className="p-8 text-center text-zinc-500">
                      暂无模型数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* 任务队列状态 */}
        {queue && (
          <div>
            <h2 className="text-xl font-bold text-white mb-4">任务队列状态</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-6 rounded-xl bg-zinc-900 border border-zinc-800">
                <p className="text-zinc-500 text-sm">运行中任务</p>
                <p className="text-3xl font-bold text-amber-400 mt-2">{queue.total_running}</p>
              </div>
              <div className="p-6 rounded-xl bg-zinc-900 border border-zinc-800">
                <p className="text-zinc-500 text-sm">排队中任务</p>
                <p className="text-3xl font-bold text-zinc-400 mt-2">{queue.total_queued}</p>
              </div>
            </div>

            {Object.keys(queue.user_stats || {}).length > 0 && (
              <div className="mt-4 rounded-xl bg-zinc-900 border border-zinc-800 overflow-hidden">
                <table className="w-full">
                  <thead className="bg-zinc-900/50 border-b border-zinc-800">
                    <tr>
                      <th className="text-left p-4 text-zinc-400 font-medium">用户 ID</th>
                      <th className="text-left p-4 text-zinc-400 font-medium">运行中</th>
                      <th className="text-left p-4 text-zinc-400 font-medium">排队中</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(queue.user_stats).map(([userId, data]) => (
                      <tr key={userId} className="border-b border-zinc-800/50">
                        <td className="p-4 text-white font-mono text-sm">
                          {userId.slice(0, 8)}...
                        </td>
                        <td className="p-4 text-amber-400">{data.running}</td>
                        <td className="p-4 text-zinc-400">{data.queued}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
