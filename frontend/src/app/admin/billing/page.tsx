"use client";
import { useCallback, useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

type Tab = "consumption" | "recharge" | "gift";

interface Row { [key: string]: string | number | null | undefined; }

interface SectionState {
  rows: Row[];
  total: number;
  page: number;
  loading: boolean;
  summary?: Record<string, number>;
}

function downloadCSV(rows: Row[], filename: string) {
  if (!rows.length) return;
  const keys = Object.keys(rows[0]);
  const csv = [
    keys.join(","),
    ...rows.map(r => keys.map(k => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(",")),
  ].join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
}

const TH = ({ children, right }: { children: React.ReactNode; right?: boolean }) => (
  <th style={{ textAlign: right ? "right" : "left", padding: "0.85rem 1rem", color: "#666", fontWeight: 500, whiteSpace: "nowrap", borderBottom: "1px solid #eee", background: "#fafaf7" }}>
    {children}
  </th>
);
const TD = ({ children, right, mono }: { children: React.ReactNode; right?: boolean; mono?: boolean }) => (
  <td style={{ textAlign: right ? "right" : "left", padding: "0.75rem 1rem", fontFamily: mono ? "monospace" : undefined, fontSize: mono ? "0.82rem" : undefined, whiteSpace: "nowrap", borderTop: "1px solid #f0f0f0" }}>
    {children}
  </td>
);

export default function AdminBillingPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("consumption");

  const empty = (): SectionState => ({ rows: [], total: 0, page: 0, loading: false });
  const [consumption, setConsumption] = useState<SectionState>(empty());
  const [recharge, setRecharge]       = useState<SectionState>(empty());
  const [gift, setGift]               = useState<SectionState>(empty());

  const token = () => (typeof window !== "undefined" ? localStorage.getItem("token") || "" : "");

  const fetchConsumption = useCallback(async (page = 0) => {
    setConsumption(p => ({ ...p, loading: true, page }));
    try {
      const r = await fetch(`${API_BASE}/api/admin/billing-consumption?page=${page}&limit=100`, {
        headers: { Authorization: `Bearer ${token()}` },
      });
      if (r.status === 403) { router.push("/dashboard"); return; }
      const d = await r.json();
      setConsumption({ rows: d.rows ?? [], total: d.total ?? 0, page, loading: false });
    } catch { setConsumption(p => ({ ...p, loading: false })); }
  }, [router]);

  const fetchRecharge = useCallback(async () => {
    setRecharge(p => ({ ...p, loading: true }));
    try {
      const r = await fetch(`${API_BASE}/api/admin/billing-recharges`, {
        headers: { Authorization: `Bearer ${token()}` },
      });
      const d = await r.json();
      setRecharge({ rows: d.rows ?? [], total: d.total ?? 0, page: 0, loading: false,
        summary: { total_amount: d.total_amount, total_credits: d.total_credits } });
    } catch { setRecharge(p => ({ ...p, loading: false })); }
  }, []);

  const fetchGift = useCallback(async () => {
    setGift(p => ({ ...p, loading: true }));
    try {
      const r = await fetch(`${API_BASE}/api/admin/billing-gifts`, {
        headers: { Authorization: `Bearer ${token()}` },
      });
      const d = await r.json();
      setGift({ rows: d.rows ?? [], total: d.total ?? 0, page: 0, loading: false,
        summary: { total_credits: d.total_credits } });
    } catch { setGift(p => ({ ...p, loading: false })); }
  }, []);

  useEffect(() => { fetchConsumption(); fetchRecharge(); fetchGift(); }, [fetchConsumption, fetchRecharge, fetchGift]);

  const exportConsumption = async () => {
    const r = await fetch(`${API_BASE}/api/admin/billing-consumption?export=true`, {
      headers: { Authorization: `Bearer ${token()}` },
    });
    const d = await r.json();
    downloadCSV(d.rows ?? [], "消耗明细.csv");
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: "consumption", label: "消耗明细" },
    { key: "recharge",    label: "充值入账" },
    { key: "gift",        label: "赠送积分" },
  ];

  const cardStyle: React.CSSProperties = {
    background: "#fff", borderRadius: "10px", border: "1px solid rgba(0,0,0,0.06)",
    padding: "1.2rem 1.5rem", flex: "1 1 160px", minWidth: 160,
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f3ed", padding: "2rem" }}>
      <div style={{ maxWidth: 1300, margin: "0 auto" }}>

        {/* Header */}
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>
            账单明细
          </h1>
          <button onClick={() => { fetchConsumption(0); fetchRecharge(); fetchGift(); }}
            style={{ padding: "0.5rem 1rem", border: "1px solid #ddd", background: "#fff", borderRadius: "8px", cursor: "pointer", fontSize: "0.85rem" }}>
            刷新
          </button>
        </div>

        {/* Summary cards */}
        <div style={{ display: "flex", gap: "1rem", marginBottom: "1.5rem", flexWrap: "wrap" }}>
          <div style={cardStyle}>
            <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.35rem" }}>总消耗</div>
            <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "#c33" }}>
              {consumption.rows.reduce((s, r) => s + (Number(r["消耗积分"]) || 0), 0).toLocaleString()}
            </div>
            <div style={{ fontSize: "0.72rem", color: "#bbb", marginTop: "0.2rem" }}>积分（当页）</div>
          </div>
          <div style={cardStyle}>
            <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.35rem" }}>充值入账</div>
            <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "#1a56db" }}>
              {(recharge.summary?.total_credits ?? 0).toLocaleString()}
            </div>
            <div style={{ fontSize: "0.72rem", color: "#bbb", marginTop: "0.2rem" }}>
              积分 · ¥{(recharge.summary?.total_amount ?? 0).toLocaleString()} 元
            </div>
          </div>
          <div style={cardStyle}>
            <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.35rem" }}>赠送积分</div>
            <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "#0a7" }}>
              {(gift.summary?.total_credits ?? 0).toLocaleString()}
            </div>
            <div style={{ fontSize: "0.72rem", color: "#bbb", marginTop: "0.2rem" }}>积分</div>
          </div>
          <div style={cardStyle}>
            <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.35rem" }}>消耗笔数</div>
            <div style={{ fontSize: "1.4rem", fontWeight: 700 }}>{consumption.total.toLocaleString()}</div>
            <div style={{ fontSize: "0.72rem", color: "#bbb", marginTop: "0.2rem" }}>笔</div>
          </div>
        </div>

        {/* Tabs + Export */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
          {tabs.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)} style={{
              padding: "0.5rem 1.2rem",
              border: tab === t.key ? "2px solid #0d0d0d" : "1px solid #ddd",
              background: tab === t.key ? "#0d0d0d" : "#fff",
              color: tab === t.key ? "#fff" : "#333",
              borderRadius: "999px", cursor: "pointer", fontSize: "0.85rem",
            }}>{t.label}</button>
          ))}
          <div style={{ marginLeft: "auto" }}>
            {tab === "consumption" && (
              <button onClick={exportConsumption} style={{ padding: "0.5rem 1.2rem", border: "1px solid #0a7", borderRadius: "8px", background: "#eaf7ea", color: "#0a7", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500 }}>
                导出 CSV
              </button>
            )}
            {tab === "recharge" && (
              <button onClick={() => downloadCSV(recharge.rows, "充值入账.csv")} style={{ padding: "0.5rem 1.2rem", border: "1px solid #1a56db", borderRadius: "8px", background: "#e8f0fe", color: "#1a56db", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500 }}>
                导出 CSV
              </button>
            )}
            {tab === "gift" && (
              <button onClick={() => downloadCSV(gift.rows, "赠送积分.csv")} style={{ padding: "0.5rem 1.2rem", border: "1px solid #0a7", borderRadius: "8px", background: "#eaf7ea", color: "#0a7", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500 }}>
                导出 CSV
              </button>
            )}
          </div>
        </div>

        {/* Table */}
        <div style={{ background: "#fff", borderRadius: "12px", overflow: "auto", border: "1px solid rgba(0,0,0,0.06)" }}>

          {/* ── 消耗明细 ── */}
          {tab === "consumption" && (
            consumption.loading ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>加载中...</div>
            ) : consumption.rows.length === 0 ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>暂无消耗记录</div>
            ) : (
              <>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                  <thead><tr>
                    <TH>时间</TH>
                    <TH>用户</TH>
                    <TH>接口 / 模型</TH>
                    <TH right>消耗积分</TH>
                    <TH>任务ID</TH>
                  </tr></thead>
                  <tbody>
                    {consumption.rows.map((r, i) => (
                      <tr key={i}>
                        <TD><span style={{ color: "#888" }}>{r["时间"]}</span></TD>
                        <TD>{r["用户"]}</TD>
                        <TD>
                          {String(r["接口/模型"]) !== "—"
                            ? <span style={{ padding: "0.15rem 0.5rem", borderRadius: "6px", background: "#f0f7ff", color: "#1a56db", fontSize: "0.78rem", fontFamily: "monospace" }}>{r["接口/模型"]}</span>
                            : <span style={{ color: "#ccc" }}>—</span>}
                        </TD>
                        <TD right><strong style={{ color: "#c33" }}>{Number(r["消耗积分"]).toLocaleString()}</strong></TD>
                        <TD mono>{r["任务ID"] || "—"}</TD>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {/* Pagination */}
                {consumption.total > 100 && (
                  <div style={{ padding: "0.8rem 1rem", borderTop: "1px solid #eee", display: "flex", gap: "0.5rem", alignItems: "center", justifyContent: "flex-end" }}>
                    <span style={{ fontSize: "0.8rem", color: "#888", marginRight: "auto" }}>
                      第 {consumption.page * 100 + 1}–{Math.min((consumption.page + 1) * 100, consumption.total)} 条 / 共 {consumption.total} 条
                    </span>
                    <button disabled={consumption.page === 0} onClick={() => fetchConsumption(consumption.page - 1)}
                      style={{ padding: "0.3rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px", cursor: consumption.page === 0 ? "not-allowed" : "pointer", opacity: consumption.page === 0 ? 0.4 : 1, background: "#fff" }}>
                      ← 上一页
                    </button>
                    <button disabled={(consumption.page + 1) * 100 >= consumption.total} onClick={() => fetchConsumption(consumption.page + 1)}
                      style={{ padding: "0.3rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px", cursor: (consumption.page + 1) * 100 >= consumption.total ? "not-allowed" : "pointer", opacity: (consumption.page + 1) * 100 >= consumption.total ? 0.4 : 1, background: "#fff" }}>
                      下一页 →
                    </button>
                  </div>
                )}
              </>
            )
          )}

          {/* ── 充值入账 ── */}
          {tab === "recharge" && (
            recharge.loading ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>加载中...</div>
            ) : recharge.rows.length === 0 ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>暂无充值记录</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                <thead><tr>
                  <TH>时间</TH>
                  <TH>用户</TH>
                  <TH right>充值积分</TH>
                  <TH right>金额（元）</TH>
                  <TH>订单号</TH>
                </tr></thead>
                <tbody>
                  {recharge.rows.map((r, i) => (
                    <tr key={i}>
                      <TD><span style={{ color: "#888" }}>{r["时间"]}</span></TD>
                      <TD>{r["用户"]}</TD>
                      <TD right><strong style={{ color: "#1a56db" }}>+{Number(r["充值积分"]).toLocaleString()}</strong></TD>
                      <TD right><span style={{ color: "#333" }}>¥{r["金额(元)"]}</span></TD>
                      <TD mono><span style={{ color: "#aaa", fontSize: "0.75rem" }}>{r["订单号"]}</span></TD>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}

          {/* ── 赠送积分 ── */}
          {tab === "gift" && (
            gift.loading ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>加载中...</div>
            ) : gift.rows.length === 0 ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>暂无赠送记录</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                <thead><tr>
                  <TH>时间</TH>
                  <TH>用户</TH>
                  <TH right>赠送积分</TH>
                  <TH>类型</TH>
                </tr></thead>
                <tbody>
                  {gift.rows.map((r, i) => (
                    <tr key={i}>
                      <TD><span style={{ color: "#888" }}>{r["时间"]}</span></TD>
                      <TD>{r["用户"]}</TD>
                      <TD right><strong style={{ color: "#0a7" }}>+{Number(r["赠送积分"]).toLocaleString()}</strong></TD>
                      <TD>
                        <span style={{ padding: "0.15rem 0.5rem", borderRadius: "999px", fontSize: "0.72rem",
                          background: r["类型"] === "注册赠送" ? "#eaf7ea" : "#fff4e0",
                          color: r["类型"] === "注册赠送" ? "#0a7" : "#f80" }}>
                          {r["类型"]}
                        </span>
                      </TD>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}

        </div>
      </div>
    </div>
  );
}
