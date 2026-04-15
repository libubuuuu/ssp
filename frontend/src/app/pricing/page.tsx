"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Package {
  id: string;
  name: string;
  credits: number;
  price: number;
  discount: string;
  description: string;
}

interface CreditPack {
  id: string;
  credits: number;
  price: number;
}

interface Order {
  id: string;
  amount: number;
  price: number;
  status: string;
  created_at: string;
  paid_at?: string;
}

export default function PricingPage() {
  const router = useRouter();
  const [tab, setTab] = useState<"package" | "credit">("package");
  const [packages, setPackages] = useState<Package[]>([]);
  const [creditPacks, setCreditPacks] = useState<CreditPack[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [processingOrder, setProcessingOrder] = useState<string | null>(null);
  const [userCredits, setUserCredits] = useState<number>(0);

  // 获取用户当前额度
  useEffect(() => {
    const token = localStorage.getItem("token");
    if (token) {
      fetch(`${API_BASE}/api/auth/me`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      })
        .then((res) => res.json())
        .then((data) => {
          if (data.credits !== undefined) {
            setUserCredits(data.credits);
          }
        })
        .catch(() => {});
    }
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/api/payment/packages`)
      .then((res) => res.json())
      .then((data) => setPackages(data.packages || []))
      .catch(() => {});

    fetch(`${API_BASE}/api/payment/credit-packs`)
      .then((res) => res.json())
      .then((data) => setCreditPacks(data.packs || []))
      .catch(() => {});
  }, []);

  // 轮询订单状态
  const pollOrderStatus = async (orderId: string, token: string, expectedAmount: number) => {
    const maxAttempts = 30; // 最多轮询 30 次（约 1 分钟）
    let attempts = 0;

    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/payment/orders/${orderId}`, {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });
        const data = await res.json();

        if (data.status === "paid") {
          setProcessingOrder(null);
          setSuccess(`支付成功！获得 ${expectedAmount} 积分`);
          // 更新用户额度
          setUserCredits((prev) => prev + expectedAmount);
          return true;
        }

        attempts++;
        if (attempts < maxAttempts) {
          setTimeout(poll, 2000); // 每 2 秒轮询一次
        } else {
          setProcessingOrder(null);
          setError("支付超时，请刷新页面重试");
        }
      } catch (err) {
        attempts++;
        if (attempts < maxAttempts) {
          setTimeout(poll, 2000);
        } else {
          setProcessingOrder(null);
          setError("网络错误，请重试");
        }
      }
    };

    poll();
  };

  const handlePurchase = async (type: string, packageId?: string, creditPackId?: string) => {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/auth");
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const res = await fetch(`${API_BASE}/api/payment/orders/create`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          type,
          package_id: packageId,
          credit_pack_id: creditPackId,
        }),
      });

      const data = await res.json();

      if (data.order_id) {
        setProcessingOrder(data.order_id);
        const expectedAmount = data.amount;

        // 开始轮询订单状态
        setTimeout(() => {
          pollOrderStatus(data.order_id, token, expectedAmount);
        }, 1000);

        // 在实际部署中，这里会打开支付二维码或跳转支付页面
        // 当前为模拟支付，2 秒后自动完成
      } else {
        setError(data.detail || "创建订单失败");
        setLoading(false);
      }
    } catch (err) {
      setLoading(false);
      setError(err instanceof Error ? err.message : "网络错误");
    }
  };

  const handleCancelOrder = () => {
    setProcessingOrder(null);
    setLoading(false);
    setError("已取消支付");
  };

  return (
    <div className="min-h-screen py-12 px-6">
      <div className="max-w-5xl mx-auto">
        <h1 className="text-3xl font-bold text-center mb-4">充值中心</h1>
        <p className="text-zinc-400 text-center mb-12">
          选择适合您的套餐，享受更优惠的价格
        </p>

        {/* 用户当前额度 */}
        {userCredits > 0 && (
          <div className="mb-8 p-4 rounded-lg bg-zinc-900/50 border border-zinc-800 text-center">
            <span className="text-zinc-400">当前余额：</span>
            <span className="text-2xl font-bold text-amber-400">{userCredits} 积分</span>
          </div>
        )}

        {/* Tab 切换 */}
        <div className="flex justify-center mb-8">
          <div className="flex gap-2 p-1 bg-zinc-900 rounded-lg">
            <button
              onClick={() => setTab("package")}
              className={`px-6 py-2 rounded-md transition-colors ${
                tab === "package" ? "bg-amber-500 text-black" : "text-zinc-400 hover:text-white"
              }`}
            >
              订阅套餐
            </button>
            <button
              onClick={() => setTab("credit")}
              className={`px-6 py-2 rounded-md transition-colors ${
                tab === "credit" ? "bg-amber-500 text-black" : "text-zinc-400 hover:text-white"
              }`}
            >
              按次充值
            </button>
          </div>
        </div>

        {/* 套餐列表 */}
        {tab === "package" && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {packages.map((pkg) => (
              <div
                key={pkg.id}
                className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 transition-all"
              >
                <div className="text-center mb-4">
                  <h3 className="text-xl font-bold">{pkg.name}</h3>
                  <p className="text-zinc-500 text-sm mt-1">{pkg.description}</p>
                </div>

                <div className="text-center mb-6">
                  <span className="text-4xl font-bold text-amber-400">¥{pkg.price}</span>
                  <span className="text-zinc-500 text-sm ml-2">/ {pkg.credits} 积分</span>
                  <span className="ml-2 px-2 py-1 text-xs bg-amber-500/20 text-amber-400 rounded">
                    {pkg.discount}
                  </span>
                </div>

                <button
                  onClick={() => handlePurchase("package", pkg.id)}
                  disabled={loading || !!processingOrder}
                  className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
                >
                  {processingOrder ? "订单处理中..." : loading ? "处理中..." : "立即购买"}
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 充值包列表 */}
        {tab === "credit" && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {creditPacks.map((pack) => (
              <div
                key={pack.id}
                className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 hover:border-amber-500/50 transition-all"
              >
                <div className="text-center mb-4">
                  <h3 className="text-xl font-bold">充值包</h3>
                  <p className="text-zinc-500 text-sm mt-1">按需充值，永久有效</p>
                </div>

                <div className="text-center mb-6">
                  <span className="text-4xl font-bold text-amber-400">¥{pack.price}</span>
                  <span className="text-zinc-500 text-sm ml-2">/ {pack.credits} 积分</span>
                </div>

                <button
                  onClick={() => handlePurchase("credit", undefined, pack.id)}
                  disabled={loading || !!processingOrder}
                  className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
                >
                  {processingOrder ? "订单处理中..." : loading ? "处理中..." : "立即充值"}
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 支付中弹窗 */}
        {processingOrder && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
            <div className="bg-zinc-900 p-8 rounded-xl border border-zinc-800 max-w-md w-full mx-4">
              <div className="text-center mb-6">
                <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-amber-400 mb-4"></div>
                <h3 className="text-xl font-bold mb-2">等待支付</h3>
                <p className="text-zinc-400 text-sm">
                  订单号：{processingOrder.slice(0, 8)}...
                </p>
                <p className="text-zinc-500 text-xs mt-2">
                  支付完成后自动确认
                </p>
              </div>
              <button
                onClick={handleCancelOrder}
                disabled={loading}
                className="w-full py-3 rounded-lg bg-zinc-800 text-zinc-300 font-medium hover:bg-zinc-700 disabled:opacity-50 transition-colors"
              >
                取消支付
              </button>
            </div>
          </div>
        )}

        {/* 成功/错误提示 */}
        {success && (
          <div className="mt-8 p-4 rounded-lg bg-green-900/20 border border-green-700">
            <p className="text-green-400 text-center">{success}</p>
          </div>
        )}

        {error && (
          <div className="mt-8 p-4 rounded-lg bg-red-900/20 border border-red-700">
            <p className="text-red-400 text-center">{error}</p>
          </div>
        )}

        {/* 说明 */}
        <div className="mt-12 p-6 rounded-lg bg-zinc-900/50 border border-zinc-800">
          <h3 className="text-sm font-semibold text-zinc-300 mb-3">充值说明</h3>
          <ul className="space-y-2 text-sm text-zinc-500">
            <li>• 积分永久有效，不会过期</li>
            <li>• 订阅套餐享受折扣价格</li>
            <li>• 充值后立即可用</li>
            <li>• 支持多种支付方式（实际部署时）</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
