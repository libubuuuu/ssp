"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function ProfilePage() {
  const router = useRouter();
  const [user, setUser] = useState<{ id: string; name: string; email: string; credits: number } | null>(null);
  const [token, setToken] = useState<string | null>(null);

  // 表单状态
  const [name, setName] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    // 获取存储的用户信息
    const storedUser = localStorage.getItem("user");
    const storedToken = localStorage.getItem("token");

    if (!storedUser || !storedToken) {
      router.push("/auth");
      return;
    }

    setUser(JSON.parse(storedUser));
    setToken(storedToken);
    setName(JSON.parse(storedUser).name || "");
  }, [router]);

  const handleUpdateName = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const res = await fetch(`${API_BASE}/api/auth/me`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify({ name }),
      });

      if (res.ok) {
        const updatedUser = { ...user, name };
        localStorage.setItem("user", JSON.stringify(updatedUser));
        setUser(updatedUser);
        setSuccess("昵称已更新");
      } else {
        const data = await res.json();
        setError(data.detail || "更新失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();

    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致");
      return;
    }

    if (newPassword.length < 6) {
      setError("新密码至少需要 6 位");
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const res = await fetch(`${API_BASE}/api/auth/change-password`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      if (res.ok) {
        setSuccess("密码已修改，请重新登录");
        setCurrentPassword("");
        setNewPassword("");
        setConfirmPassword("");
        // 3 秒后跳转到登录页
        setTimeout(() => {
          localStorage.removeItem("token");
          localStorage.removeItem("user");
          router.push("/auth");
        }, 3000);
      } else {
        const data = await res.json();
        setError(data.detail || "修改失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    router.push("/");
  };

  if (!user) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-zinc-400">加载中...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen px-6 py-12">
      <div className="max-w-2xl mx-auto">
        {/* 头部 */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold mb-2">个人中心</h1>
          <p className="text-zinc-400">管理您的个人信息和账户设置</p>
        </div>

        {/* 用户信息卡片 */}
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 mb-6">
          <div className="flex items-center gap-4 mb-4">
            <div className="w-16 h-16 rounded-full bg-amber-500/20 flex items-center justify-center text-2xl font-bold text-amber-400">
              {user.name?.[0]?.toUpperCase() || "U"}
            </div>
            <div>
              <h2 className="text-lg font-semibold">{user.name || user.email}</h2>
              <p className="text-zinc-400 text-sm">{user.email}</p>
            </div>
          </div>
          <div className="flex items-center justify-between py-3 border-t border-zinc-800">
            <span className="text-zinc-400 text-sm">账户余额</span>
            <span className="text-amber-400 font-semibold">{user.credits} 积分</span>
          </div>
          <Link
            href="/pricing"
            className="block w-full py-2 mt-3 text-center rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 transition-colors text-sm"
          >
            充值中心
          </Link>
        </div>

        {/* 修改昵称 */}
        <form onSubmit={handleUpdateName} className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 mb-6">
          <h3 className="text-lg font-semibold mb-4">修改昵称</h3>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-zinc-400 mb-2">昵称</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
                placeholder="输入新昵称"
              />
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-900/20 border border-red-700">
                <p className="text-red-400 text-sm">{error}</p>
              </div>
            )}
            {success && (
              <div className="p-3 rounded-lg bg-green-900/20 border border-green-700">
                <p className="text-green-400 text-sm">{success}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
            >
              {loading ? "保存中..." : "保存"}
            </button>
          </div>
        </form>

        {/* 修改密码 */}
        <form onSubmit={handleChangePassword} className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 mb-6">
          <h3 className="text-lg font-semibold mb-4">修改密码</h3>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-zinc-400 mb-2">当前密码</label>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
                placeholder="输入当前密码"
              />
            </div>
            <div>
              <label className="block text-sm text-zinc-400 mb-2">新密码</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
                placeholder="至少 6 位"
                minLength={6}
              />
            </div>
            <div>
              <label className="block text-sm text-zinc-400 mb-2">确认新密码</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
                placeholder="再次输入新密码"
                minLength={6}
              />
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-900/20 border border-red-700">
                <p className="text-red-400 text-sm">{error}</p>
              </div>
            )}
            {success && (
              <div className="p-3 rounded-lg bg-green-900/20 border border-green-700">
                <p className="text-green-400 text-sm">{success}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !currentPassword || !newPassword || !confirmPassword}
              className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
            >
              {loading ? "修改中..." : "修改密码"}
            </button>
          </div>
        </form>

        {/* 退出登录 */}
        <button
          onClick={handleLogout}
          className="w-full py-3 rounded-lg border border-zinc-700 text-zinc-400 hover:bg-zinc-800 transition-colors"
        >
          退出登录
        </button>

        {/* 返回主页 */}
        <Link
          href="/"
          className="block w-full py-3 mt-3 text-center rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors"
        >
          返回主页
        </Link>
      </div>
    </div>
  );
}
