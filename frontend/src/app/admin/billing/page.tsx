"use client";
import { useCallback, useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
type Tab = "consumption" | "users" | "recharge" | "gift";

interface Row { [key: string]: string | number | null | undefined; }

interface UserStat {
  user_id: string; email: string; name: string;
  current_balance: number; gift_credits: number;
  total_charges: number; success_count: number; failed_count: number;
  gross_credits: number; refunded_credits: number; net_credits: number;
}

interface UserDetailModal {
  user: UserStat;
  rows: Row[];
  total: number;
  page: number;
  loading: boolean;
}

function downloadCSV(rows: Row[], filename: string) {
  if (!rows.length) return;
  const keys = Object.keys(rows[0]);
  const csv = [keys.join(","), ...rows.map(r => keys.map(k => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(","))].join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
}

const TH = ({ children, right }: { children: React.ReactNode; right?: boolean }) => (
  <th style={{ textAlign: right ? "right" : "left", padding: "0.85rem 1rem", color: "#666", fontWeight: 500, whiteSpace: "nowrap", borderBottom: "1px solid #eee", background: "#fafaf7" }}>{children}</th>
);
const TD = ({ children, right, mono }: { children: React.ReactNode; right?: boolean; mono?: boolean }) => (
  <td style={{ textAlign: right ? "right" : "left", padding: "0.75rem 1rem", fontFamily: mono ? "monospace" : undefined, fontSize: mono ? "0.82rem" : undefined, whiteSpace: "nowrap", borderTop: "1px solid #f0f0f0" }}>{children}</td>
);

const PROVIDER_STYLE: Record<string, { bg: string; color: string }> = {
  aiview: { bg: "#e6f4ea", color: "#137333" },
  fal:    { bg: "#e8f0fe", color: "#1a56db" },
  system: { bg: "#f1f1f1", color: "#888" },
};

function ProviderBadge({ provider }: { provider: string }) {
  const s = PROVIDER_STYLE[provider] ?? { bg: "#f5f5f5", color: "#999" };
  return provider && provider !== "—"
    ? <span style={{ padding: "0.15rem 0.5rem", borderRadius: "999px", fontSize: "0.75rem", fontWeight: 500, background: s.bg, color: s.color }}>{provider}</span>
    : <span style={{ color: "#ccc" }}>—</span>;
}

export default function AdminBillingPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("consumption");

  // 消耗明细（成功）
  const [cRows, setCRows] = useState<Row[]>([]);
  const [cTotal, setCTotal] = useState(0);
  const [cPage, setCPage] = useState(0);
  const [cLoading, setCLoading] = useState(false);

  // 用户列表
  const [users, setUsers] = useState<UserStat[]>([]);
  const [uSearch, setUSearch] = useState("");
  const [uLoading, setULoading] = useState(false);
  const [userModal, setUserModal] = useState<UserDetailModal | null>(null);

  // 充值 / 赠送
  const [rRows, setRRows] = useState<Row[]>([]);
  const [rSummary, setRSummary] = useState({ total_amount: 0, total_credits: 0 });
  const [gRows, setGRows] = useState<Row[]>([]);
  const [gTotal, setGTotal] = useState(0);

  const tk = () => typeof window !== "undefined" ? localStorage.getItem("token") || "" : "";
  const headers = () => ({ Authorization: `Bearer ${tk()}` });

  const fetchConsumption = useCallback(async (p = 0) => {
    setCLoading(true); setCPage(p);
    try {
      const r = await fetch(`${API_BASE}/api/admin/billing-consumption?page=${p}&limit=100`, { headers: headers() });
      if (r.status === 403) { router.push("/dashboard"); return; }
      const d = await r.json();
      setCRows(d.rows ?? []); setCTotal(d.total ?? 0);
    } finally { setCLoading(false); }
  }, [router]);

  const fetchUsers = useCallback(async () => {
    setULoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/admin/billing-users`, { headers: headers() });
      const d = await r.json();
      setUsers(d.users ?? []);
    } finally { setULoading(false); }
  }, []);

  const fetchRecharge = useCallback(async () => {
    const r = await fetch(`${API_BASE}/api/admin/billing-recharges`, { headers: headers() });
    const d = await r.json();
    setRRows(d.rows ?? []); setRSummary({ total_amount: d.total_amount ?? 0, total_credits: d.total_credits ?? 0 });
  }, []);

  const fetchGift = useCallback(async () => {
    const r = await fetch(`${API_BASE}/api/admin/billing-gifts`, { headers: headers() });
    const d = await r.json();
    setGRows(d.rows ?? []); setGTotal(d.total ?? 0);
  }, []);

  const openUserDetail = useCallback(async (user: UserStat, page = 0) => {
    setUserModal({ user, rows: [], total: 0, page, loading: true });
    const r = await fetch(`${API_BASE}/api/admin/billing-user-detail?user_id=${user.user_id}&page=${page}&limit=100`, { headers: headers() });
    const d = await r.json();
    setUserModal(prev => prev ? { ...prev, rows: d.rows ?? [], total: d.total ?? 0, loading: false } : null);
  }, []);

  const exportConsumption = async () => {
    const r = await fetch(`${API_BASE}/api/admin/billing-consumption?export=true`, { headers: headers() });
    const d = await r.json(); downloadCSV(d.rows ?? [], "消耗明细.csv");
  };

  const exportUserDetail = async () => {
    if (!userModal) return;
    const r = await fetch(`${API_BASE}/api/admin/billing-user-detail?user_id=${userModal.user.user_id}&export=true`, { headers: headers() });
    const d = await r.json(); downloadCSV(d.rows ?? [], `${userModal.user.email}_明细.csv`);
  };

  useEffect(() => { fetchConsumption(); fetchUsers(); fetchRecharge(); fetchGift(); }, [fetchConsumption, fetchUsers, fetchRecharge, fetchGift]);

  const filteredUsers = users.filter(u => !uSearch || u.email.includes(uSearch) || (u.name || "").includes(uSearch));

  const tabs: { key: Tab; label: string }[] = [
    { key: "consumption", label: "消耗明细" },
    { key: "users",       label: "用户明细" },
    { key: "recharge",    label: "充值入账" },
    { key: "gift",        label: "赠送积分" },
  ];

  const card = (label: string, value: string | number, sub = "") => (
    <div style={{ background: "#fff", borderRadius: "10px", border: "1px solid rgba(0,0,0,0.06)", padding: "1.2rem 1.5rem", flex: "1 1 150px", minWidth: 150 }}>
      <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.35rem" }}>{label}</div>
      <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "#0d0d0d" }}>{typeof value === "number" ? value.toLocaleString() : value}</div>
      {sub && <div style={{ fontSize: "0.72rem", color: "#bbb", marginTop: "0.2rem" }}>{sub}</div>}
    </div>
  );

  return (
    <div style={{ minHeight: "100vh", background: "#f5f3ed", padding: "2rem" }}>
      <div style={{ maxWidth: 1300, margin: "0 auto" }}>

        {/* Header */}
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>账单明细</h1>
          <button onClick={() => { fetchConsumption(0); fetchUsers(); fetchRecharge(); fetchGift(); }}
            style={{ padding: "0.5rem 1rem", border: "1px solid #ddd", background: "#fff", borderRadius: "8px", cursor: "pointer", fontSize: "0.85rem" }}>刷新</button>
        </div>

        {/* Summary cards */}
        <div style={{ display: "flex", gap: "1rem", marginBottom: "1.5rem", flexWrap: "wrap" }}>
          {card("成功消耗", cRows.reduce((s, r) => s + (Number(r["净消耗"] ?? r["消耗积分"]) || 0), 0), "积分（当页）")}
          {card("充值入账", rSummary.total_credits, `积分 · ¥${rSummary.total_amount}`)}
          {card("赠送积分", gTotal > 0 ? gRows.reduce((s, r) => s + (Number(r["赠送积分"]) || 0), 0) : 0, "积分")}
          {card("活跃用户", users.filter(u => u.net_credits > 0).length, "有净消耗")}
        </div>

        {/* Tabs + Export */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
          {tabs.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)} style={{
              padding: "0.5rem 1.2rem", borderRadius: "999px", cursor: "pointer", fontSize: "0.85rem",
              border: tab === t.key ? "2px solid #0d0d0d" : "1px solid #ddd",
              background: tab === t.key ? "#0d0d0d" : "#fff",
              color: tab === t.key ? "#fff" : "#333",
            }}>{t.label}</button>
          ))}
          <div style={{ marginLeft: "auto" }}>
            {tab === "consumption" && <button onClick={exportConsumption} style={{ padding: "0.5rem 1.2rem", border: "1px solid #0a7", borderRadius: "8px", background: "#eaf7ea", color: "#0a7", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500 }}>导出 CSV</button>}
            {tab === "recharge" && <button onClick={() => downloadCSV(rRows, "充值入账.csv")} style={{ padding: "0.5rem 1.2rem", border: "1px solid #1a56db", borderRadius: "8px", background: "#e8f0fe", color: "#1a56db", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500 }}>导出 CSV</button>}
            {tab === "gift" && <button onClick={() => downloadCSV(gRows, "赠送积分.csv")} style={{ padding: "0.5rem 1.2rem", border: "1px solid #0a7", borderRadius: "8px", background: "#eaf7ea", color: "#0a7", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500 }}>导出 CSV</button>}
          </div>
        </div>

        {/* Table card */}
        <div style={{ background: "#fff", borderRadius: "12px", overflow: "auto", border: "1px solid rgba(0,0,0,0.06)" }}>

          {/* ── 消耗明细（成功） ── */}
          {tab === "consumption" && (cLoading
            ? <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>加载中...</div>
            : cRows.length === 0
              ? <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>暂无消耗记录</div>
              : <>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                    <thead><tr>
                      <TH>时间</TH><TH>用户</TH><TH>供应商</TH><TH>模型 / 接口</TH><TH right>消耗积分</TH>
                    </tr></thead>
                    <tbody>
                      {cRows.map((r, i) => (
                        <tr key={i}>
                          <TD><span style={{ color: "#888" }}>{r["时间"]}</span></TD>
                          <TD>{r["用户"]}</TD>
                          <TD><ProviderBadge provider={String(r["供应商"] ?? "")} /></TD>
                          <TD mono><span style={{ fontSize: "0.8rem", color: "#444" }}>{r["模型/接口"] || "—"}</span></TD>
                          <TD right><strong style={{ color: "#c33" }}>{Number(r["消耗积分"] ?? r["净消耗"]).toLocaleString()}</strong></TD>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {cTotal > 100 && (
                    <div style={{ padding: "0.8rem 1rem", borderTop: "1px solid #eee", display: "flex", gap: "0.5rem", alignItems: "center", justifyContent: "flex-end" }}>
                      <span style={{ fontSize: "0.8rem", color: "#888", marginRight: "auto" }}>第 {cPage*100+1}–{Math.min((cPage+1)*100, cTotal)} 条 / 共 {cTotal} 条</span>
                      <button disabled={cPage===0} onClick={() => fetchConsumption(cPage-1)} style={{ padding: "0.3rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px", cursor: cPage===0?"not-allowed":"pointer", opacity: cPage===0?0.4:1, background: "#fff" }}>← 上一页</button>
                      <button disabled={(cPage+1)*100>=cTotal} onClick={() => fetchConsumption(cPage+1)} style={{ padding: "0.3rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px", cursor: (cPage+1)*100>=cTotal?"not-allowed":"pointer", opacity: (cPage+1)*100>=cTotal?0.4:1, background: "#fff" }}>下一页 →</button>
                    </div>
                  )}
                </>
          )}

          {/* ── 用户明细 ── */}
          {tab === "users" && (
            <>
              <div style={{ padding: "0.8rem 1rem", borderBottom: "1px solid #eee" }}>
                <input
                  placeholder="搜索用户邮箱..."
                  value={uSearch}
                  onChange={e => setUSearch(e.target.value)}
                  style={{ padding: "0.4rem 0.8rem", border: "1px solid #ddd", borderRadius: "6px", fontSize: "0.85rem", width: 260 }}
                />
              </div>
              {uLoading
                ? <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>加载中...</div>
                : <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                    <thead><tr>
                      <TH>用户</TH>
                      <TH right>当前余额</TH>
                      <TH right>赠送积分</TH>
                      <TH right>成功次数</TH>
                      <TH right>失败次数</TH>
                      <TH right>净消耗积分</TH>
                      <TH right>退款积分</TH>
                      <TH></TH>
                    </tr></thead>
                    <tbody>
                      {filteredUsers.map(u => (
                        <tr key={u.user_id}>
                          <TD>
                            <div style={{ fontWeight: 500 }}>{u.email}</div>
                            {u.name && u.name !== "—" && <div style={{ fontSize: "0.75rem", color: "#999" }}>{u.name}</div>}
                          </TD>
                          <TD right><strong style={{ color: u.current_balance < 50 ? "#c33" : "#0d0d0d" }}>{u.current_balance.toLocaleString()}</strong></TD>
                          <TD right><span style={{ color: "#0a7" }}>{u.gift_credits > 0 ? `+${u.gift_credits.toLocaleString()}` : "—"}</span></TD>
                          <TD right><span style={{ color: "#0a7" }}>{u.success_count}</span></TD>
                          <TD right><span style={{ color: u.failed_count > 0 ? "#c33" : "#bbb" }}>{u.failed_count}</span></TD>
                          <TD right><strong>{u.net_credits.toLocaleString()}</strong></TD>
                          <TD right><span style={{ color: "#888" }}>{u.refunded_credits.toLocaleString()}</span></TD>
                          <TD>
                            <button onClick={() => openUserDetail(u)}
                              style={{ padding: "0.25rem 0.8rem", border: "1px solid #ddd", borderRadius: "6px", cursor: "pointer", fontSize: "0.8rem", background: "#fafaf7" }}>
                              明细
                            </button>
                          </TD>
                        </tr>
                      ))}
                    </tbody>
                  </table>
              }
            </>
          )}

          {/* ── 充值入账 ── */}
          {tab === "recharge" && (rRows.length === 0
            ? <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>暂无充值记录</div>
            : <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                <thead><tr><TH>时间</TH><TH>用户</TH><TH right>充值积分</TH><TH right>金额（元）</TH><TH>订单号</TH></tr></thead>
                <tbody>
                  {rRows.map((r, i) => (
                    <tr key={i}>
                      <TD><span style={{ color: "#888" }}>{r["时间"]}</span></TD>
                      <TD>{r["用户"]}</TD>
                      <TD right><strong style={{ color: "#1a56db" }}>+{Number(r["充值积分"]).toLocaleString()}</strong></TD>
                      <TD right>¥{r["金额(元)"]}</TD>
                      <TD mono><span style={{ color: "#aaa", fontSize: "0.75rem" }}>{r["订单号"]}</span></TD>
                    </tr>
                  ))}
                </tbody>
              </table>
          )}

          {/* ── 赠送积分 ── */}
          {tab === "gift" && (gRows.length === 0
            ? <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>暂无赠送记录</div>
            : <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                <thead><tr><TH>时间</TH><TH>用户</TH><TH right>赠送积分</TH><TH>类型</TH></tr></thead>
                <tbody>
                  {gRows.map((r, i) => (
                    <tr key={i}>
                      <TD><span style={{ color: "#888" }}>{r["时间"]}</span></TD>
                      <TD>{r["用户"]}</TD>
                      <TD right><strong style={{ color: "#0a7" }}>+{Number(r["赠送积分"]).toLocaleString()}</strong></TD>
                      <TD>
                        <span style={{ padding: "0.15rem 0.5rem", borderRadius: "999px", fontSize: "0.72rem",
                          background: r["类型"]==="注册赠送"?"#eaf7ea":"#fff4e0",
                          color: r["类型"]==="注册赠送"?"#0a7":"#f80" }}>
                          {r["类型"]}
                        </span>
                      </TD>
                    </tr>
                  ))}
                </tbody>
              </table>
          )}
        </div>
      </div>

      {/* ── 用户明细弹窗 ── */}
      {userModal && (
        <div onClick={e => { if (e.target === e.currentTarget) setUserModal(null); }}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem" }}>
          <div style={{ background: "#fff", borderRadius: "12px", width: "100%", maxWidth: 900, maxHeight: "85vh", display: "flex", flexDirection: "column", boxShadow: "0 8px 40px rgba(0,0,0,0.2)" }}>
            {/* header */}
            <div style={{ padding: "1rem 1.5rem", borderBottom: "1px solid #eee", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
              <div>
                <div style={{ fontWeight: 600 }}>{userModal.user.email} — 消耗明细</div>
                <div style={{ fontSize: "0.78rem", color: "#999", marginTop: "0.2rem" }}>
                  成功 {userModal.user.success_count} 次 · 失败 {userModal.user.failed_count} 次 · 净消耗 {userModal.user.net_credits.toLocaleString()} 积分
                </div>
              </div>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <button onClick={exportUserDetail}
                  style={{ padding: "0.3rem 0.8rem", border: "1px solid #0a7", borderRadius: "6px", background: "#eaf7ea", color: "#0a7", cursor: "pointer", fontSize: "0.8rem" }}>
                  导出 CSV
                </button>
                <button onClick={() => setUserModal(null)}
                  style={{ background: "none", border: "none", cursor: "pointer", fontSize: "1.4rem", color: "#888", lineHeight: 1 }}>✕</button>
              </div>
            </div>
            {/* body */}
            <div style={{ overflow: "auto", flex: 1, fontSize: "0.84rem" }}>
              {userModal.loading
                ? <div style={{ padding: "2.5rem", textAlign: "center", color: "#999" }}>加载中…</div>
                : <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead style={{ position: "sticky", top: 0 }}>
                      <tr>
                        <TH>时间</TH>
                        <TH>状态</TH>
                        <TH>供应商</TH>
                        <TH>模型 / 接口</TH>
                        <TH right>充值积分</TH>
                        <TH right>赠送积分</TH>
                        <TH right>其他入账</TH>
                        <TH right>扣积分</TH>
                        <TH right>退积分</TH>
                        <TH right>净消耗</TH>
                      </tr>
                    </thead>
                    <tbody>
                      {userModal.rows.map((r, i) => {
                        const isGift = r["状态"] === "赠送";
                        const isRecharge = r["状态"] === "充值";
                        const isAdmin = ["管理员补偿","账本修正","管理员调整"].includes(String(r["状态"]));
                        const statusStyle = isGift
                          ? { background: "#eaf7ea", color: "#0a7" }
                          : isRecharge
                            ? { background: "#e8f0fe", color: "#1a56db" }
                            : isAdmin
                              ? { background: "#fff4e0", color: "#c45f00" }
                              : r["状态"] === "成功"
                                ? { background: "#f0f0f0", color: "#555" }
                                : { background: "#fde8e8", color: "#c33" };
                        return (
                        <tr key={i} style={{ background: r["状态"]==="失败" ? "#fff8f8" : isRecharge ? "#f8fbff" : isGift ? "#f6fff6" : isAdmin ? "#fffbf0" : undefined }}>
                          <TD><span style={{ color: "#888" }}>{r["时间"]}</span></TD>
                          <TD>
                            <span style={{ padding: "0.15rem 0.5rem", borderRadius: "999px", fontSize: "0.72rem", ...statusStyle }}>
                              {String(r["状态"])}
                            </span>
                          </TD>
                          <TD><ProviderBadge provider={String(r["供应商"] ?? "")} /></TD>
                          <TD mono><span style={{ fontSize: "0.8rem", color: "#444" }}>{r["模型/接口"] || "—"}</span></TD>
                          <TD right><span style={{ color: "#1a56db", fontWeight: Number(r["充值积分"])>0?600:400 }}>{Number(r["充值积分"])>0 ? `+${Number(r["充值积分"]).toLocaleString()}` : "—"}</span></TD>
                          <TD right><span style={{ color: "#0a7", fontWeight: Number(r["赠送积分"])>0?600:400 }}>{Number(r["赠送积分"])>0 ? `+${Number(r["赠送积分"]).toLocaleString()}` : "—"}</span></TD>
                          <TD right><span style={{ color: "#f80", fontWeight: Number(r["其他入账"])>0?600:400 }}>{Number(r["其他入账"])>0 ? `+${Number(r["其他入账"]).toLocaleString()}` : "—"}</span></TD>
                          <TD right><span style={{ color: "#bbb" }}>{r["状态"]==="赠送"||r["状态"]==="充值"||r["状态"]==="管理员补偿"||r["状态"]==="账本修正"||r["状态"]==="管理员调整" ? "—" : Number(r["扣积分"]).toLocaleString()}</span></TD>
                          <TD right><span style={{ color: Number(r["退积分"]) > 0 ? "#0a7" : "#bbb" }}>{Number(r["退积分"]) > 0 ? `+${Number(r["退积分"]).toLocaleString()}` : "—"}</span></TD>
                          <TD right><strong style={{ color: Number(r["净消耗"])>0?"#c33":"#bbb" }}>{isGift||isRecharge||isAdmin ? "—" : Number(r["净消耗"]).toLocaleString()}</strong></TD>
                        </tr>
                        );
                      })}
                    </tbody>
                  </table>
              }
            </div>
            {/* pagination */}
            {!userModal.loading && userModal.total > 100 && (
              <div style={{ padding: "0.8rem 1.5rem", borderTop: "1px solid #eee", display: "flex", gap: "0.5rem", alignItems: "center", justifyContent: "flex-end", flexShrink: 0 }}>
                <span style={{ fontSize: "0.8rem", color: "#888", marginRight: "auto" }}>
                  第 {userModal.page*100+1}–{Math.min((userModal.page+1)*100, userModal.total)} 条 / 共 {userModal.total} 条
                </span>
                <button disabled={userModal.page===0} onClick={() => openUserDetail(userModal.user, userModal.page-1)}
                  style={{ padding: "0.3rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px", cursor: userModal.page===0?"not-allowed":"pointer", opacity: userModal.page===0?0.4:1, background: "#fff" }}>← 上一页</button>
                <button disabled={(userModal.page+1)*100>=userModal.total} onClick={() => openUserDetail(userModal.user, userModal.page+1)}
                  style={{ padding: "0.3rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px", cursor: (userModal.page+1)*100>=userModal.total?"not-allowed":"pointer", opacity: (userModal.page+1)*100>=userModal.total?0.4:1, background: "#fff" }}>下一页 →</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
