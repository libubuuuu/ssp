"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
interface Package { id: string; name: string; credits: number; price: number; discount: string; description: string; }
interface CreditPack { id: string; credits: number; price: number; }

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

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (token) {
      fetch(`${API_BASE}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
        .then(r => r.json()).then(d => { if (d.credits !== undefined) setUserCredits(d.credits); }).catch(() => {});
    }
    fetch(`${API_BASE}/api/payment/packages`).then(r => r.json()).then(d => setPackages(d.packages || [])).catch(() => {});
    fetch(`${API_BASE}/api/payment/credit-packs`).then(r => r.json()).then(d => setCreditPacks(d.packs || [])).catch(() => {});
  }, []);

  const pollOrderStatus = async (orderId: string, token: string, expectedAmount: number) => {
    let attempts = 0;
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/payment/orders/${orderId}`, { headers: { Authorization: `Bearer ${token}` } });
        const data = await res.json();
        if (data.status === "paid") {
          setProcessingOrder(null);
          setSuccess(`✅ 已确认入账！获得 ${expectedAmount} 积分`);
          setUserCredits(prev => prev + expectedAmount);
          return;
        }
        attempts++;
        // 长轮询 10 分钟（等管理员确认）
        if (attempts < 300) setTimeout(poll, 2000);
      } catch { attempts++; if (attempts < 300) setTimeout(poll, 2000); }
    };
    poll();
  };

  const handlePurchase = async (type: string, packageId?: string, creditPackId?: string) => {
    const token = localStorage.getItem("token");
    if (!token) { router.push("/auth"); return; }
    setLoading(true); setError(null); setSuccess(null);
    try {
      const res = await fetch(`${API_BASE}/api/payment/orders/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ type, package_id: packageId, credit_pack_id: creditPackId }),
      });
      const data = await res.json();
      if (data.order_id) { setProcessingOrder(data.order_id); setTimeout(() => pollOrderStatus(data.order_id, token, data.amount), 1000); }
      else { setError(data.detail || "创建订单失败"); setLoading(false); }
    } catch (err) { setLoading(false); setError(err instanceof Error ? err.message : "网络错误"); }
  };

  const btn = (disabled: boolean) => ({ width: "100%", padding: "0.75rem", background: disabled ? "#ccc" : "#0d0d0d", color: "#fff", border: "none", borderRadius: "10px", cursor: disabled ? "not-allowed" as const : "pointer" as const, fontSize: "0.9rem", fontWeight: 500 });

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto" }}>
        <div style={{ marginBottom: "2rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>账户管理</div>
          <h1 style={{ fontSize: "1.6rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>充值<span style={{ fontStyle: "italic" }}> 中心</span></h1>
          <p style={{ fontSize: "0.85rem", color: "#999", marginTop: "0.4rem" }}>选择适合您的套餐，享受更优惠的价格</p>
        </div>

        <div style={{ marginBottom: "1.5rem", padding: "1rem 1.5rem", background: "#fff", borderRadius: "14px", border: "1px solid rgba(0,0,0,0.08)", display: "inline-flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{ fontSize: "0.85rem", color: "#999" }}>当前余额：</span>
          <span style={{ fontSize: "1.4rem", fontWeight: 700, color: "#0d0d0d" }}>{userCredits}</span>
          <span style={{ fontSize: "0.85rem", color: "#999" }}>积分</span>
        </div>

        <div style={{ display: "flex", gap: "0.4rem", marginBottom: "1.5rem" }}>
          {[{ key: "package", label: "订阅套餐" }, { key: "credit", label: "按次充值" }].map(t => (
            <button key={t.key} onClick={() => setTab(t.key as "package" | "credit")}
              style={{ padding: "0.5rem 1.25rem", border: tab === t.key ? "2px solid #0d0d0d" : "1px solid #ddd", background: tab === t.key ? "#0d0d0d" : "#fff", color: tab === t.key ? "#fff" : "#666", borderRadius: "999px", cursor: "pointer", fontSize: "0.85rem", fontWeight: tab === t.key ? 500 : 400 }}>
              {t.label}
            </button>
          ))}
        </div>

        {tab === "package" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))", gap: "1rem" }}>
            {packages.map(pkg => (
              <div key={pkg.id} style={{ background: "#fff", borderRadius: "16px", border: "1px solid rgba(0,0,0,0.08)", padding: "1.5rem", boxShadow: "0 4px 12px rgba(0,0,0,0.04)" }}>
                <h3 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#0d0d0d", margin: "0 0 0.25rem" }}>{pkg.name}</h3>
                <p style={{ fontSize: "0.78rem", color: "#999", margin: "0 0 1rem" }}>{pkg.description}</p>
                <div style={{ marginBottom: "1.25rem" }}>
                  <span style={{ fontSize: "2rem", fontWeight: 700, color: "#0d0d0d" }}>¥{pkg.price}</span>
                  <span style={{ fontSize: "0.8rem", color: "#999", marginLeft: "0.4rem" }}>/ {pkg.credits} 积分</span>
                  <span style={{ marginLeft: "0.4rem", padding: "0.15rem 0.5rem", background: "#f0ede6", color: "#666", borderRadius: "999px", fontSize: "0.72rem" }}>{pkg.discount}</span>
                </div>
                <button onClick={() => handlePurchase("package", pkg.id)} disabled={loading || !!processingOrder} style={btn(loading || !!processingOrder)}>
                  {processingOrder ? "订单处理中..." : loading ? "处理中..." : "立即购买"}
                </button>
              </div>
            ))}
          </div>
        )}

        {tab === "credit" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))", gap: "1rem" }}>
            {creditPacks.map(pack => (
              <div key={pack.id} style={{ background: "#fff", borderRadius: "16px", border: "1px solid rgba(0,0,0,0.08)", padding: "1.5rem", boxShadow: "0 4px 12px rgba(0,0,0,0.04)" }}>
                <h3 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#0d0d0d", margin: "0 0 0.25rem" }}>充值包</h3>
                <p style={{ fontSize: "0.78rem", color: "#999", margin: "0 0 1rem" }}>按需充值，永久有效</p>
                <div style={{ marginBottom: "1.25rem" }}>
                  <span style={{ fontSize: "2rem", fontWeight: 700, color: "#0d0d0d" }}>¥{pack.price}</span>
                  <span style={{ fontSize: "0.8rem", color: "#999", marginLeft: "0.4rem" }}>/ {pack.credits} 积分</span>
                </div>
                <button onClick={() => handlePurchase("credit", undefined, pack.id)} disabled={loading || !!processingOrder} style={btn(loading || !!processingOrder)}>
                  {processingOrder ? "订单处理中..." : loading ? "处理中..." : "立即充值"}
                </button>
              </div>
            ))}
          </div>
        )}

        {success && <div style={{ marginTop: "1.5rem", padding: "1rem", background: "#f0faf0", border: "1px solid #b7ddb7", borderRadius: "12px", color: "#2d7a2d", fontSize: "0.9rem" }}>{success}</div>}
        {error && <div style={{ marginTop: "1.5rem", padding: "1rem", background: "#fff0f0", border: "1px solid #ddb7b7", borderRadius: "12px", color: "#7a2d2d", fontSize: "0.9rem" }}>{error}</div>}

        <div style={{ marginTop: "2rem", padding: "1.25rem 1.5rem", background: "#fff", borderRadius: "14px", border: "1px solid rgba(0,0,0,0.08)" }}>
          <h3 style={{ fontSize: "0.85rem", fontWeight: 600, color: "#0d0d0d", margin: "0 0 0.75rem" }}>充值说明</h3>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "0.4rem" }}>
            {["积分永久有效，不会过期", "订阅套餐享受折扣价格", "充值后立即可用", "支持多种支付方式（实际部署时）"].map((item, i) => (
              <li key={i} style={{ fontSize: "0.82rem", color: "#888" }}>• {item}</li>
            ))}
          </ul>
        </div>
      </main>

      {processingOrder && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}>
          <div style={{ background: "#fff", padding: "2rem", borderRadius: "20px", maxWidth: "380px", width: "90%", textAlign: "center", boxShadow: "0 20px 60px rgba(0,0,0,0.15)" }}>
            <div style={{ width: "48px", height: "48px", border: "3px solid #eee", borderTopColor: "#0d0d0d", borderRadius: "50%", animation: "spin 1s linear infinite", margin: "0 auto 1rem" }}></div>
            <h3 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#0d0d0d", margin: "0 0 0.5rem" }}>等待支付</h3>
            <p style={{ fontSize: "0.8rem", color: "#999", margin: "0 0 1.5rem" }}>订单号：{processingOrder.slice(0, 8)}... · 支付完成后自动确认</p>
            <button onClick={() => { setProcessingOrder(null); setLoading(false); setError("已取消支付"); }}
              style={{ width: "100%", padding: "0.75rem", background: "#f5f5f5", color: "#666", border: "none", borderRadius: "10px", cursor: "pointer", fontSize: "0.9rem" }}>
              取消支付
            </button>
            <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
          </div>
        </div>
      )}

      {/* 收款码弹窗 */}
      {processingOrder && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem" }}>
          <div style={{ background: "#fff", borderRadius: 20, maxWidth: 440, width: "100%", padding: "2rem", textAlign: "center" }}>
            <h2 style={{ margin: "0 0 1rem", fontSize: "1.2rem", fontWeight: 500 }}>扫码支付</h2>
            <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "1.5rem" }}>请用微信/支付宝扫码付款</div>
            <div style={{ background: "#fafaf7", borderRadius: 12, padding: "2rem", marginBottom: "1rem" }}>
              <img src="/qr-payment.png" alt="收款码" onError={(e: any) => { e.target.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'%3E%3Crect width='200' height='200' fill='%23ddd'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' dy='.35em' fill='%23666'%3E收款码占位%3C/text%3E%3C/svg%3E"; }}
                style={{ width: 200, height: 200, display: "block", margin: "0 auto" }} />
            </div>
            <div style={{ background: "#fff8ea", border: "1px solid #f5d884", borderRadius: 10, padding: "0.8rem", marginBottom: "1rem", textAlign: "left", fontSize: "0.82rem", color: "#7a5400" }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>⚠ 支付流程</div>
              <div>1. 扫上方二维码付款</div>
              <div>2. 付款完成后把<b>订单号</b>发给客服微信</div>
              <div>3. 客服确认后积分自动到账</div>
            </div>
            <div style={{ background: "#f5f5f0", padding: "0.75rem", borderRadius: 8, marginBottom: "1rem", fontFamily: "monospace", fontSize: "0.82rem" }}>
              订单号: <span style={{ fontWeight: 600, userSelect: "all" }}>{processingOrder}</span>
              <button onClick={() => { navigator.clipboard.writeText(processingOrder); setSuccess("订单号已复制"); setTimeout(() => setSuccess(null), 2000); }}
                style={{ marginLeft: 8, background: "#0d0d0d", color: "#fff", border: "none", borderRadius: 6, padding: "0.2rem 0.6rem", fontSize: "0.75rem", cursor: "pointer" }}>复制</button>
            </div>
            <div style={{ fontSize: "0.8rem", color: "#999", marginBottom: "1rem" }}>⏳ 等待确认中... (最长 10 分钟)</div>
            <button onClick={() => { setProcessingOrder(null); setLoading(false); }}
              style={{ width: "100%", padding: "0.7rem", background: "#f5f5f0", border: "none", borderRadius: 10, cursor: "pointer", fontSize: "0.88rem", color: "#333" }}>
              关闭（订单保留，可稍后确认）
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
