"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { useEffect, useState, Suspense } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

function TaskStatusInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const taskId = searchParams.get("id");
  const [status, setStatus] = useState<{
    status: string;
    progress: number;
    result_url: string | null;
    error: string | null;
  } | null>(null);

  useEffect(() => {
    if (!taskId) return;

    const fetchStatus = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/tasks/status/${taskId}`);
        const data = await res.json();
        setStatus(data);
      } catch (err) {
        console.error(err);
      }
    };

    fetchStatus();

    // WebSocket 多窗口同步
    const ws = new WebSocket(`${WS_BASE}/api/tasks/ws/${taskId}`);
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setStatus(data);
      } catch {}
    };
    return () => ws.close();
  }, [taskId]);

  if (!taskId) {
    return (
      <div className="max-w-2xl mx-auto py-12 px-6">
        <p className="text-zinc-500">请提供任务 ID</p>
        <button
          onClick={() => router.push("/")}
          className="mt-4 text-amber-400 hover:underline"
        >
          返回首页
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-2">任务状态</h1>
      <p className="font-mono text-amber-400 mb-6">{taskId}</p>

      {status ? (
        <div className="space-y-4 p-6 rounded-lg bg-zinc-900 border border-zinc-700">
          <p>
            <span className="text-zinc-500">状态：</span>
            <span
              className={
                status.status === "completed"
                  ? "text-green-400"
                  : status.status === "failed"
                    ? "text-red-400"
                    : "text-amber-400"
              }
            >
              {status.status}
            </span>
          </p>
          {status.progress > 0 && (
            <div>
              <p className="text-zinc-500 text-sm mb-1">进度</p>
              <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-amber-500 transition-all"
                  style={{ width: `${status.progress}%` }}
                />
              </div>
            </div>
          )}
          {status.result_url && (
            <a
              href={status.result_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-amber-400 hover:underline"
            >
              查看结果 →
            </a>
          )}
          {status.error && <p className="text-red-400">{status.error}</p>}
        </div>
      ) : (
        <p className="text-zinc-500">加载中...</p>
      )}

      <button
        onClick={() => router.push("/")}
        className="mt-6 text-amber-400 hover:underline"
      >
        返回首页
      </button>
    </div>
  );
}

export default function TasksPage() {
  return (
    <Suspense fallback={<div className="p-12">加载中...</div>}>
      <TaskStatusInner />
    </Suspense>
  );
}
