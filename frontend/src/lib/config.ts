// 前后端 API 统一配置

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
export const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
