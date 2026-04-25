"use client";

import { useState } from "react";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export default function ForgotPasswordPage() {
  const [step, setStep] = useState<"request" | "success">("request");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      // TODO: 实际部署时对接后端密码找回 API
      await new Promise((resolve) => setTimeout(resolve, 1000));
      setStep("success");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errors.networkError"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[80vh] flex items-center justify-center px-4">
      <div className="w-full max-w-md p-8 rounded-xl bg-zinc-900 border border-zinc-800">
        <h1 className="text-2xl font-bold text-center mb-2">密码找回</h1>
        <p className="text-zinc-400 text-center mb-8">
          {step === "request"
            ? "输入注册邮箱，我们将发送重置链接"
            : "重置链接已发送，请查收邮件"}
        </p>

        {step === "request" ? (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-zinc-400 mb-2">注册邮箱</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
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
              {loading ? "发送中..." : "发送重置链接"}
            </button>
          </form>
        ) : (
          <div className="text-center space-y-4">
            <div className="text-6xl">📧</div>
            <p className="text-zinc-400 text-sm">
              我们已向 <span className="text-amber-400">{email}</span> 发送了密码重置链接
            </p>
            <p className="text-zinc-500 text-xs">
              提示：如果 5 分钟内未收到邮件，请检查垃圾邮件箱
            </p>
          </div>
        )}

        <div className="mt-6 text-center">
          <Link
            href="/auth"
            className="text-sm text-zinc-400 hover:text-amber-400 transition-colors"
          >
            返回登录 →
          </Link>
        </div>
      </div>
    </div>
  );
}
