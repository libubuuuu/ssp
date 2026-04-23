"use client";

import { useEffect, useRef, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

type TaskStatus = "pending" | "processing" | "completed" | "failed";

interface UseTaskPollingOptions {
  /** Polling interval in ms (default: 5000) */
  interval?: number;
  /** Timeout in ms (default: 180000 = 3 min) */
  timeout?: number;
  /** Task status endpoint suffix (default: "/api/video/status/") */
  statusEndpoint?: string;
}

interface UseTaskPollingReturn {
  status: TaskStatus | null;
  isPolling: boolean;
  startPolling: (taskId: string) => void;
  stopPolling: () => void;
}

/**
 * 通用任务状态轮询 Hook
 * 自动清理 interval，防止内存泄漏
 */
export function useTaskPolling(
  onComplete: (data: any) => void,
  onError: (error: string) => void,
  options: UseTaskPollingOptions = {}
): UseTaskPollingReturn {
  const {
    interval = 5000,
    timeout = 180000,
    statusEndpoint = "/api/video/status/",
  } = options;

  const intervalRef = useRef<number | null>(null);
  const timeoutRef = useRef<number | null>(null);
  const statusRef = useRef<TaskStatus | null>(null);

  const clearAll = useCallback(() => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  // 组件卸载时清理
  useEffect(() => {
    return clearAll;
  }, [clearAll]);

  const stopPolling = useCallback(() => {
    clearAll();
    statusRef.current = null;
  }, [clearAll]);

  const startPolling = useCallback(
    (taskId: string) => {
      statusRef.current = "pending";
      clearAll();

      const poll = async () => {
        try {
          const res = await fetch(`${API_BASE}${statusEndpoint}${taskId}`);
          const data = await res.json();
          statusRef.current = data.status;

          if (data.status === "completed") {
            clearAll();
            onComplete(data);
          } else if (data.status === "failed") {
            clearAll();
            onError(data.error || "任务失败");
          }
        } catch {
          clearAll();
          onError("网络错误，请重试");
        }
      };

      intervalRef.current = setInterval(poll, interval) as unknown as number;
      timeoutRef.current = setTimeout(() => {
        clearAll();
        onError("任务超时，请重试");
      }, timeout) as unknown as number;

      // 立即执行一次
      poll();
    },
    [interval, timeout, statusEndpoint, clearAll, onComplete, onError]
  );

  return {
    status: statusRef.current,
    isPolling: intervalRef.current !== null,
    startPolling,
    stopPolling,
  };
}
