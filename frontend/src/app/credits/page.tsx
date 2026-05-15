"use client";
import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const PAGE_SIZE = 20;

interface LedgerRecord {
  id: string;
  delta: number;
  balance_after: number;
  reason: string;
  label: string;
  ref_id: string | null;
  module: string | null;
  created_at: number;
}

interface LedgerResponse {
  records: LedgerRecord[];
  total: number;
  page: number;
  pages: number;
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function deltaColor(delta: number): string {
  return delta > 0 ? "#07a352" : delta < 0 ? "#c00" : "#888";
}

function deltaStr(delta: number): string {
  return delta > 0 ? `+${delta}` : String(delta);
}

export default function CreditsPage() {
  const router = useRouter();
  const [data, setData] = useState<LedgerResponse | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchLedger = useCallback(async (p: number) => {
    const token = localStorage.getItem("token");
    if (!token) { router.push("/auth"); return; }
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/billing/ledger?page=${p}&limit=${PAGE_SIZE}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) { router.push("/auth?expired=1"); return; }
      if (!res.ok) throw new Error(await res.text());
      const json = await res.json() as LedgerResponse;
      setData(json);
    } catch (e) {
      setError((e as Error).message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { router.push("/auth"); return; }
    fetchLedger(page);
  }, [page, fetchLedger, router]);

  const goPage = (p: number) => { setPage(p); };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#fafaf8" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem", maxWidth: 760, margin: "0 auto", width: "100%" }}>
        {/* 标题 */}
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", gap: "1rem" }}>
          <Link href="/profile" style={{ color: "#888", textDecoration: "none", fontSize: "0.85rem" }}>
            ← 个人中心
          </Link>
          <h1 style={{ margin: 0, fontSize: "1.4rem", fontWeight: 500, color: "#0d0d0d" }}>
            积分明细
          </h1>
        </div>

        {error && (
          <div style={{ background: "#fff0f0", border: "1px solid #fcc", borderRadius: 10, padding: "0.75rem 1rem", marginBottom: "1rem", color: "#c00", fontSize: "0.85rem" }}>
            {error}
          </div>
        )}

        {/* 流水列表 */}
        <div style={{ background: "#fff", borderRadius: 20, border: "1px solid rgba(0,0,0,0.04)", overflow: "hidden" }}>
          {loading && (
            <div style={{ padding: "3rem", textAlign: "center", color: "#aaa", fontSize: "0.9rem" }}>
              加载中…
            </div>
          )}

          {!loading && data?.records.length === 0 && (
            <div style={{ padding: "3rem", textAlign: "center", color: "#aaa", fontSize: "0.9rem" }}>
              暂无积分记录
            </div>
          )}

          {!loading && data && data.records.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #f0ede8" }}>
                  {["时间", "类型", "积分变化", "变化后余额", "来源"].map(h => (
                    <th key={h} style={{ padding: "0.85rem 1rem", textAlign: "left", fontWeight: 500, color: "#888", background: "#fafaf8" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.records.map((r, idx) => (
                  <tr key={r.id} style={{ borderBottom: idx < data.records.length - 1 ? "1px solid #f5f3ef" : "none" }}>
                    <td style={{ padding: "0.8rem 1rem", color: "#888", whiteSpace: "nowrap" }}>
                      {formatTime(r.created_at)}
                    </td>
                    <td style={{ padding: "0.8rem 1rem" }}>
                      <span style={{
                        display: "inline-block",
                        padding: "0.2rem 0.6rem",
                        borderRadius: 999,
                        fontSize: "0.78rem",
                        fontWeight: 500,
                        background: r.delta > 0 ? "#edfbf3" : r.delta < 0 ? "#fff2f2" : "#f5f5f5",
                        color: deltaColor(r.delta),
                      }}>
                        {r.label || r.reason}
                      </span>
                    </td>
                    <td style={{ padding: "0.8rem 1rem", fontWeight: 600, color: deltaColor(r.delta), fontVariantNumeric: "tabular-nums" }}>
                      {deltaStr(r.delta)}
                    </td>
                    <td style={{ padding: "0.8rem 1rem", color: "#333", fontVariantNumeric: "tabular-nums" }}>
                      {r.balance_after}
                    </td>
                    <td style={{ padding: "0.8rem 1rem", color: "#aaa", fontSize: "0.78rem", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.module || r.ref_id || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* 分页 */}
        {data && data.pages > 1 && (
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: "0.5rem", marginTop: "1.25rem" }}>
            <button
              onClick={() => goPage(page - 1)}
              disabled={page <= 1}
              style={{ padding: "0.4rem 0.9rem", borderRadius: 8, border: "1px solid #e5e5e5", background: page <= 1 ? "#f5f5f5" : "#fff", color: page <= 1 ? "#ccc" : "#333", cursor: page <= 1 ? "default" : "pointer", fontSize: "0.85rem" }}
            >
              ←
            </button>
            <span style={{ fontSize: "0.85rem", color: "#666" }}>
              第 {page} / {data.pages} 页，共 {data.total} 条
            </span>
            <button
              onClick={() => goPage(page + 1)}
              disabled={page >= data.pages}
              style={{ padding: "0.4rem 0.9rem", borderRadius: 8, border: "1px solid #e5e5e5", background: page >= data.pages ? "#f5f5f5" : "#fff", color: page >= data.pages ? "#ccc" : "#333", cursor: page >= data.pages ? "default" : "pointer", fontSize: "0.85rem" }}
            >
              →
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
