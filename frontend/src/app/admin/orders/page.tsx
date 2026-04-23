"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface Order {
  id: string;
  user_id: string;
  amount: number;
  price: number;
  status: string;
  created_at: string;
  paid_at: string | null;
  user_email: string;
  user_name: string;
}

export default function AdminOrdersPage() {
  const { t, lang } = useLang();
  const router = useRouter();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"pending" | "paid" | "all">("pending");
  const [confirming, setConfirming] = useState<string | null>(null);

  const loadOrders = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/payment/admin/orders?status=${filter}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 403) {
        alert(lang === "en" ? "Admin only" : "仅管理员可访问");
        router.push("/dashboard");
        return;
      }
      const data = await res.json();
      setOrders(data.orders || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadOrders(); }, [filter]);

  const confirmOrder = async (orderId: string) => {
    if (!confirm(lang === "en" ? `Confirm order ${orderId.slice(0, 8)}...?` : `确认订单 ${orderId.slice(0, 8)}... 入账？`)) return;
    setConfirming(orderId);
    try {
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/payment/orders/${orderId}/confirm`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (res.ok) {
        alert(lang === "en" ? `✅ Confirmed! Added ${data.credits_added} credits` : `✅ 已确认！用户获得 ${data.credits_added} 积分`);
        loadOrders();
      } else {
        alert(lang === "en" ? `Failed: ${data.detail}` : `失败：${data.detail}`);
      }
    } finally {
      setConfirming(null);
    }
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f3ed", padding: "2rem" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>
            {lang === "en" ? "Order Management" : "订单管理"}
          </h1>
          <button onClick={() => router.push("/admin/settings")} style={{ background: "none", border: "1px solid #ddd", padding: "0.5rem 1rem", borderRadius: "8px", cursor: "pointer", fontSize: "0.85rem" }}>
            ← {lang === "en" ? "Back" : "返回"}
          </button>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1.5rem" }}>
          {[
            { v: "pending", zh: "待确认", en: "Pending" },
            { v: "paid", zh: "已入账", en: "Paid" },
            { v: "all", zh: "全部", en: "All" },
          ].map(f => (
            <button key={f.v} onClick={() => setFilter(f.v as any)} style={{
              padding: "0.5rem 1.2rem",
              border: filter === f.v ? "2px solid #0d0d0d" : "1px solid #ddd",
              background: filter === f.v ? "#0d0d0d" : "#fff",
              color: filter === f.v ? "#fff" : "#333",
              borderRadius: "999px", cursor: "pointer", fontSize: "0.85rem",
            }}>
              {lang === "en" ? f.en : f.zh}
            </button>
          ))}
          <button onClick={loadOrders} style={{ marginLeft: "auto", padding: "0.5rem 1rem", border: "1px solid #ddd", background: "#fff", borderRadius: "8px", cursor: "pointer", fontSize: "0.85rem" }}>
            🔄 {lang === "en" ? "Refresh" : "刷新"}
          </button>
        </div>

        <div style={{ background: "#fff", borderRadius: "12px", overflow: "hidden", border: "1px solid rgba(0,0,0,0.06)" }}>
          {loading ? (
            <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>
              {lang === "en" ? "Loading..." : "加载中..."}
            </div>
          ) : orders.length === 0 ? (
            <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>
              {lang === "en" ? "No orders" : "暂无订单"}
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
              <thead style={{ background: "#fafaf7" }}>
                <tr>
                  <th style={{ textAlign: "left", padding: "0.9rem 1rem", color: "#666", fontWeight: 500 }}>
                    {lang === "en" ? "Order ID" : "订单号"}
                  </th>
                  <th style={{ textAlign: "left", padding: "0.9rem 1rem", color: "#666", fontWeight: 500 }}>
                    {lang === "en" ? "User" : "用户"}
                  </th>
                  <th style={{ textAlign: "right", padding: "0.9rem 1rem", color: "#666", fontWeight: 500 }}>
                    {lang === "en" ? "Credits" : "积分"}
                  </th>
                  <th style={{ textAlign: "right", padding: "0.9rem 1rem", color: "#666", fontWeight: 500 }}>
                    {lang === "en" ? "Price" : "金额"}
                  </th>
                  <th style={{ textAlign: "left", padding: "0.9rem 1rem", color: "#666", fontWeight: 500 }}>
                    {lang === "en" ? "Created" : "创建时间"}
                  </th>
                  <th style={{ textAlign: "center", padding: "0.9rem 1rem", color: "#666", fontWeight: 500 }}>
                    {lang === "en" ? "Status" : "状态"}
                  </th>
                  <th style={{ textAlign: "center", padding: "0.9rem 1rem", color: "#666", fontWeight: 500 }}>
                    {lang === "en" ? "Action" : "操作"}
                  </th>
                </tr>
              </thead>
              <tbody>
                {orders.map(o => (
                  <tr key={o.id} style={{ borderTop: "1px solid #eee" }}>
                    <td style={{ padding: "0.9rem 1rem", fontFamily: "monospace", fontSize: "0.8rem", color: "#555" }}>
                      {o.id.slice(0, 12)}...
                    </td>
                    <td style={{ padding: "0.9rem 1rem" }}>
                      <div style={{ fontWeight: 500 }}>{o.user_name || "—"}</div>
                      <div style={{ fontSize: "0.75rem", color: "#999" }}>{o.user_email}</div>
                    </td>
                    <td style={{ textAlign: "right", padding: "0.9rem 1rem", fontWeight: 600 }}>
                      {o.amount}
                    </td>
                    <td style={{ textAlign: "right", padding: "0.9rem 1rem" }}>
                      ¥{o.price}
                    </td>
                    <td style={{ padding: "0.9rem 1rem", color: "#666", fontSize: "0.82rem" }}>
                      {o.created_at}
                    </td>
                    <td style={{ textAlign: "center", padding: "0.9rem 1rem" }}>
                      <span style={{
                        padding: "0.2rem 0.6rem",
                        borderRadius: "999px",
                        fontSize: "0.75rem",
                        background: o.status === "paid" ? "#eaf7ea" : o.status === "pending" ? "#fff4e0" : "#eee",
                        color: o.status === "paid" ? "#0a7" : o.status === "pending" ? "#f80" : "#999",
                      }}>
                        {o.status === "paid" ? (lang === "en" ? "Paid" : "已入账") : o.status === "pending" ? (lang === "en" ? "Pending" : "待确认") : o.status}
                      </span>
                    </td>
                    <td style={{ textAlign: "center", padding: "0.9rem 1rem" }}>
                      {o.status === "pending" ? (
                        <button onClick={() => confirmOrder(o.id)} disabled={confirming === o.id} style={{
                          padding: "0.4rem 1rem", background: "#0d0d0d", color: "#fff", border: "none",
                          borderRadius: "8px", cursor: "pointer", fontSize: "0.8rem",
                          opacity: confirming === o.id ? 0.5 : 1,
                        }}>
                          {confirming === o.id ? "..." : (lang === "en" ? "Confirm" : "确认入账")}
                        </button>
                      ) : (
                        <span style={{ color: "#999", fontSize: "0.8rem" }}>—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
