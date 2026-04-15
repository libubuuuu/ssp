"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Mode = "login" | "register";

export default function AuthPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const endpoint = mode === "login" ? "/api/auth/login" : "/api/auth/register";
      const body: Record<string, string> = { email, password };
      if (mode === "register") {
        body.name = name;
      }

      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      const data = await res.json();

      if (data.token) {
        // 保存 Token
        localStorage.setItem("token", data.token);
        localStorage.setItem("user", JSON.stringify(data.user));
        router.push("/");
      } else {
        setError(data.detail || "操作失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[80vh] flex items-center justify-center px-4">
      <div className="w-full max-w-md p-8 rounded-xl bg-zinc-900 border border-zinc-800">
        <h1 className="text-2xl font-bold text-center mb-2">
          {mode === "login" ? "登录" : "注册"}
        </h1>
        <p className="text-zinc-400 text-center mb-8">
          {mode === "login" ? "欢迎回来" : "创建新账号，赠送 100 积分"}
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === "register" && (
            <div>
              <label className="block text-sm text-zinc-400 mb-2">昵称</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="可选，默认为邮箱前缀"
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
              />
            </div>
          )}

          <div>
            <label className="block text-sm text-zinc-400 mb-2">邮箱</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="your@email.com"
              className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
              required
            />
          </div>

          <div>
            <label className="block text-sm text-zinc-400 mb-2">密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="至少 6 位"
              className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
              minLength={6}
              required
            />
          </div>

          {error && (
            <div className="p-3 rounded-lg bg-red-900/20 border border-red-700">
              <p className="text-red-400 text-sm">{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
          >
            {loading ? "处理中..." : mode === "login" ? "登录" : "注册"}
          </button>
        </form>

        <div className="mt-6 text-center">
          <button
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError(null);
            }}
            className="text-sm text-zinc-400 hover:text-amber-400 transition-colors"
          >
            {mode === "login" ? "没有账号？去注册" : "已有账号？去登录"}
          </button>
        </div>
      </div>
    </div>
  );
}
