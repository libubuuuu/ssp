"use client";
import type { ReactNode } from "react";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface JobsUsageRow {
  model_label: string;
  count: number;
  success: number;
  failed: number;
  cost_credits: number;
  unique_users: number;
}

interface Vc2UsageRow {
  video_model: string;
  display_name: string;
  count: number;
  completed: number;
  failed: number;
  credits_charged: number;
  credits_refunded: number;
  fal_estimated_usd: number;
  unique_users: number;
}

interface LedgerRow {
  user_id: string;
  user_email: string;
  user_name: string;
  current_balance: number;
  consumed: number;
  refunded: number;
  recharged: number;
  admin_adjusted: number;
  net: number;
  tx_count: number;
}

interface UsageData {
  jobs_usage: JobsUsageRow[];
  vc2_usage: Vc2UsageRow[];
  ledger_summary: LedgerRow[];
  totals: {
    total_jobs: number;
    jobs_cost_credits: number;
    vc2_net_cost_credits: number;
    total_recharged_credits: number;
    total_consumed_credits: number;
    total_refunded_credits: number;
  };
}

type Tab = "jobs" | "vc2" | "ledger";

interface DetailRow { [key: string]: string | number | null | undefined; }
interface RechargeRow {
  id: string;
  delta: number;
  balance_after: number;
  reason: string;
  ref_id: string;
  created_at: string;
}
interface DetailModal {
  title: string;
  type: "jobs" | "ledger" | "vc2";
  params: Record<string, string>;
  rows: DetailRow[];
  recharges: RechargeRow[];
  total: number;
  page: number;
  offset: number;
  loading: boolean;
}

type Provider = "fal" | "aliyun" | "aiview" | "未知";

const PROVIDER_STYLE: Record<Provider, { bg: string; color: string }> = {
  fal:    { bg: "#e8f0fe", color: "#1a56db" },
  aliyun: { bg: "#fff3e0", color: "#c45f00" },
  aiview: { bg: "#e6f4ea", color: "#137333" },
  未知:   { bg: "#f1f1f1", color: "#888"    },
};

interface ProviderInfo { provider: Provider; endpoint: string; }

function getProviderInfo(label: string): ProviderInfo {
  const fal = (ep: string): ProviderInfo => ({ provider: "fal",    endpoint: ep });
  const ali = (ep: string): ProviderInfo => ({ provider: "aliyun", endpoint: ep });
  if (label === "gpt-image-2")      return fal("openai/gpt-image-2");
  if (label === "AI爆款视频")        return fal("seedance/v1/pro/i2v");
  if (label === "分镜复刻/替换")     return fal("kling-video/o3/edit");
  if (label === "分镜复刻/生成")     return fal("gpt-image-2");
  if (label === "广告视频")          return fal("seedance+omnihuman");
  if (label === "图生视频/未标注")   return fal("kling-video/o3/i2v");
  if (label === "AI爆款/分析")       return ali("qwen-vl-max");
  if (label === "分镜复刻/分析")     return ali("qwen-vl-max");
  return { provider: "未知", endpoint: "—" };
}

function ProviderCell({ label }: { label: string }) {
  const { provider, endpoint } = getProviderInfo(label);
  const s = PROVIDER_STYLE[provider];
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", gap: "0.2rem" }}>
      <span style={{
        display: "inline-block",
        padding: "0.15rem 0.5rem",
        borderRadius: "999px",
        fontSize: "0.75rem",
        fontWeight: 500,
        background: s.bg,
        color: s.color,
        whiteSpace: "nowrap",
      }}>
        {provider}
      </span>
      {endpoint !== "—" && (
        <span style={{ fontSize: "0.72rem", color: "#999", fontFamily: "monospace", whiteSpace: "nowrap" }}>
          {endpoint}
        </span>
      )}
    </span>
  );
}

const TH = ({ children, right }: { children: ReactNode; right?: boolean }) => (
  <th style={{
    textAlign: right ? "right" : "left",
    padding: "0.9rem 1rem",
    color: "#666",
    fontWeight: 500,
    whiteSpace: "nowrap",
  }}>
    {children}
  </th>
);

const TD = ({ children, right, mono }: { children: ReactNode; right?: boolean; mono?: boolean }) => (
  <td style={{
    textAlign: right ? "right" : "left",
    padding: "0.85rem 1rem",
    fontFamily: mono ? "monospace" : undefined,
    fontSize: mono ? "0.82rem" : undefined,
    whiteSpace: "nowrap",
  }}>
    {children}
  </td>
);

export default function AdminBillingPage() {
  const { lang } = useLang();
  const router = useRouter();
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("jobs");
  const [modal, setModal] = useState<DetailModal | null>(null);
  const isEn = lang === "en";

  const openDetail = useCallback(async (
    type: "jobs" | "ledger" | "vc2",
    title: string,
    params: Record<string, string>,
    page = 0,
  ) => {
    setModal({ title, type, params, rows: [], recharges: [], total: 0, page, offset: page * 50, loading: true });
    try {
      const token = localStorage.getItem("token") || "";
      const qs = new URLSearchParams({ type, ...params, limit: "50", offset: String(page * 50) });
      const res = await fetch(`${API_BASE}/api/admin/billing-detail?${qs}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const d = await res.json();
      setModal(prev => prev ? { ...prev, rows: d.rows ?? [], recharges: d.recharges ?? [], total: d.total ?? 0, offset: d.offset ?? 0, loading: false } : null);
    } catch {
      setModal(prev => prev ? { ...prev, loading: false } : null);
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/admin/model-usage`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 403) {
        alert(isEn ? "Admin only" : "仅管理员可访问");
        router.push("/dashboard");
        return;
      }
      setData(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [isEn, router]);

  useEffect(() => { load(); }, [load]);

  const tabs: { key: Tab; zh: string; en: string }[] = [
    { key: "jobs",   zh: "模型用量", en: "Model Usage" },
    { key: "vc2",    zh: "视频复刻", en: "Clone V2"    },
    { key: "ledger", zh: "积分对账", en: "Credits"     },
  ];

  return (
    <div style={{ minHeight: "100vh", background: "#f5f3ed", padding: "2rem" }}>
      <div style={{ maxWidth: 1280, margin: "0 auto" }}>

        {/* Header */}
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>
            {isEn ? "Billing Report" : "对账单"}
          </h1>
          <button
            onClick={load}
            disabled={loading}
            style={{
              padding: "0.5rem 1rem",
              border: "1px solid #ddd",
              background: "#fff",
              borderRadius: "8px",
              cursor: "pointer",
              fontSize: "0.85rem",
              opacity: loading ? 0.5 : 1,
            }}
          >
            🔄 {isEn ? "Refresh" : "刷新"}
          </button>
        </div>

        {/* Summary cards */}
        {data && (
          <div style={{ display: "flex", gap: "1rem", marginBottom: "1.5rem", flexWrap: "wrap" }}>
            {[
              { label: isEn ? "Total Jobs" : "任务总数",     value: data.totals.total_jobs.toLocaleString(),              sub: "" },
              { label: isEn ? "Jobs Cost" : "任务消耗",      value: data.totals.jobs_cost_credits.toLocaleString(),       sub: isEn ? "credits" : "积分" },
              { label: isEn ? "Clone V2 Net" : "复刻净消耗", value: data.totals.vc2_net_cost_credits.toLocaleString(),    sub: isEn ? "credits" : "积分" },
              { label: isEn ? "Total Recharged" : "总充值",  value: data.totals.total_recharged_credits.toLocaleString(), sub: isEn ? "credits" : "积分" },
              { label: isEn ? "Total Consumed" : "总消耗",   value: data.totals.total_consumed_credits.toLocaleString(),  sub: isEn ? "credits" : "积分" },
              { label: isEn ? "Total Refunded" : "总退款",   value: data.totals.total_refunded_credits.toLocaleString(),  sub: isEn ? "credits" : "积分" },
            ].map(c => (
              <div key={c.label} style={{
                background: "#fff",
                borderRadius: "10px",
                border: "1px solid rgba(0,0,0,0.06)",
                padding: "1.2rem 1.5rem",
                flex: "1 1 140px",
                minWidth: 140,
              }}>
                <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.35rem" }}>{c.label}</div>
                <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "#0d0d0d" }}>{c.value}</div>
                {c.sub && <div style={{ fontSize: "0.72rem", color: "#bbb", marginTop: "0.2rem" }}>{c.sub}</div>}
              </div>
            ))}
          </div>
        )}

        {/* Tabs */}
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1.5rem" }}>
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                padding: "0.5rem 1.2rem",
                border: tab === t.key ? "2px solid #0d0d0d" : "1px solid #ddd",
                background: tab === t.key ? "#0d0d0d" : "#fff",
                color: tab === t.key ? "#fff" : "#333",
                borderRadius: "999px",
                cursor: "pointer",
                fontSize: "0.85rem",
              }}
            >
              {isEn ? t.en : t.zh}
            </button>
          ))}
        </div>

        {/* Table card */}
        <div style={{
          background: "#fff",
          borderRadius: "12px",
          overflow: "auto",
          border: "1px solid rgba(0,0,0,0.06)",
        }}>
          {loading ? (
            <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>
              {isEn ? "Loading..." : "加载中..."}
            </div>
          ) : !data ? (
            <div style={{ padding: "3rem", textAlign: "center", color: "#c33" }}>
              {isEn ? "Failed to load data" : "加载失败"}
            </div>

          ) : tab === "jobs" ? (
            /* ── 模型用量 ── */
            data.jobs_usage.length === 0 ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>
                {isEn ? "No jobs data" : "暂无任务数据"}
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                <thead style={{ background: "#fafaf7" }}>
                  <tr>
                    <TH>{isEn ? "Provider / API" : "供应商 / 接口"}</TH>
                    <TH>{isEn ? "Model / Type" : "模型 / 类型"}</TH>
                    <TH right>{isEn ? "Total" : "总次数"}</TH>
                    <TH right>{isEn ? "Success" : "成功"}</TH>
                    <TH right>{isEn ? "Failed" : "失败"}</TH>
                    <TH right>{isEn ? "Success %" : "成功率"}</TH>
                    <TH right>{isEn ? "Cost (credits)" : "消耗（积分）"}</TH>
                    <TH right>{isEn ? "Avg/job" : "均次积分"}</TH>
                    <TH right>{isEn ? "Users" : "用户数"}</TH>
                    <TH>{/* 明细 */}</TH>
                  </tr>
                </thead>
                <tbody>
                  {data.jobs_usage.map(r => {
                    const rate = r.count > 0 ? ((r.success / r.count) * 100).toFixed(1) : "—";
                    const avg = r.count > 0 ? Math.round(r.cost_credits / r.count) : 0;
                    return (
                      <tr key={r.model_label} style={{ borderTop: "1px solid #eee" }}>
                        <TD><ProviderCell label={r.model_label} /></TD>
                        <TD><span style={{ fontWeight: 500 }}>{r.model_label}</span></TD>
                        <TD right>{r.count.toLocaleString()}</TD>
                        <TD right><span style={{ color: "#0a7" }}>{r.success.toLocaleString()}</span></TD>
                        <TD right><span style={{ color: r.failed > 0 ? "#c33" : "#999" }}>{r.failed.toLocaleString()}</span></TD>
                        <TD right>
                          <span style={{ color: parseFloat(rate) >= 90 ? "#0a7" : parseFloat(rate) < 70 ? "#c33" : "#f80" }}>
                            {rate}{rate !== "—" ? "%" : ""}
                          </span>
                        </TD>
                        <TD right><strong>{r.cost_credits.toLocaleString()}</strong></TD>
                        <TD right><span style={{ color: "#666" }}>{avg}</span></TD>
                        <TD right>{r.unique_users}</TD>
                        <TD>
                          <button
                            onClick={() => openDetail("jobs", `${r.model_label} — 任务明细`, { model_label: r.model_label })}
                            style={{ padding: "0.2rem 0.7rem", border: "1px solid #ddd", borderRadius: "6px", cursor: "pointer", fontSize: "0.78rem", background: "#fafaf7", color: "#555" }}
                          >
                            {isEn ? "Detail" : "明细"}
                          </button>
                        </TD>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )

          ) : tab === "vc2" ? (
            /* ── 视频复刻 V2 ── */
            data.vc2_usage.length === 0 ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>
                {isEn ? "No video clone V2 jobs" : "暂无视频复刻 V2 记录"}
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                <thead style={{ background: "#fafaf7" }}>
                  <tr>
                    <TH>{isEn ? "Provider / API" : "供应商 / 接口"}</TH>
                    <TH>{isEn ? "Model" : "模型"}</TH>
                    <TH right>{isEn ? "Total" : "总数"}</TH>
                    <TH right>{isEn ? "Completed" : "成功"}</TH>
                    <TH right>{isEn ? "Failed" : "失败"}</TH>
                    <TH right>{isEn ? "Charged" : "已扣积分"}</TH>
                    <TH right>{isEn ? "Refunded" : "已退积分"}</TH>
                    <TH right>{isEn ? "Net Cost" : "净消耗"}</TH>
                    <TH right>{isEn ? "fal ~USD" : "fal 估算 USD"}</TH>
                    <TH right>{isEn ? "Users" : "用户"}</TH>
                    <TH>{/* 明细 */}</TH>
                  </tr>
                </thead>
                <tbody>
                  {data.vc2_usage.map(r => (
                    <tr key={r.video_model} style={{ borderTop: "1px solid #eee" }}>
                      <TD>
                        <span style={{ display: "inline-flex", flexDirection: "column", gap: "0.2rem" }}>
                          <span style={{
                            display: "inline-block", padding: "0.15rem 0.5rem",
                            borderRadius: "999px", fontSize: "0.75rem", fontWeight: 500,
                            background: PROVIDER_STYLE.aiview.bg, color: PROVIDER_STYLE.aiview.color,
                            whiteSpace: "nowrap",
                          }}>aiview</span>
                          <span style={{ fontSize: "0.72rem", color: "#999", fontFamily: "monospace", whiteSpace: "nowrap" }}>
                            aiview.club/{r.video_model}
                          </span>
                        </span>
                      </TD>
                      <TD>
                        <strong>{r.display_name}</strong>
                        <span style={{ color: "#aaa", fontSize: "0.75rem", marginLeft: "0.5rem" }}>
                          ({r.video_model})
                        </span>
                      </TD>
                      <TD right>{r.count}</TD>
                      <TD right><span style={{ color: "#0a7" }}>{r.completed}</span></TD>
                      <TD right><span style={{ color: r.failed > 0 ? "#c33" : "#999" }}>{r.failed}</span></TD>
                      <TD right>{r.credits_charged.toLocaleString()}</TD>
                      <TD right><span style={{ color: "#0a7" }}>{r.credits_refunded.toLocaleString()}</span></TD>
                      <TD right><strong>{(r.credits_charged - r.credits_refunded).toLocaleString()}</strong></TD>
                      <TD right>${r.fal_estimated_usd.toFixed(2)}</TD>
                      <TD right>{r.unique_users}</TD>
                      <TD>
                        <button
                          onClick={() => openDetail("vc2", `${r.display_name}(${r.video_model}) — 任务明细`, { video_model: r.video_model })}
                          style={{ padding: "0.2rem 0.7rem", border: "1px solid #ddd", borderRadius: "6px", cursor: "pointer", fontSize: "0.78rem", background: "#fafaf7", color: "#555" }}
                        >
                          {isEn ? "Detail" : "明细"}
                        </button>
                      </TD>
                    </tr>
                  ))}
                </tbody>
              </table>
            )

          ) : (
            /* ── 积分对账 ── */
            data.ledger_summary.length === 0 ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>
                {isEn ? "No ledger data" : "暂无流水记录"}
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                <thead style={{ background: "#fafaf7" }}>
                  <tr>
                    <TH>{isEn ? "User" : "用户"}</TH>
                    <TH right>{isEn ? "Balance" : "当前余额"}</TH>
                    <TH right>{isEn ? "Consumed" : "已消耗"}</TH>
                    <TH right>{isEn ? "Refunded" : "已退款"}</TH>
                    <TH right>{isEn ? "Recharged" : "已充值"}</TH>
                    <TH right>{isEn ? "Admin Adj." : "管理员调整"}</TH>
                    <TH right>{isEn ? "Net Δ" : "净变动"}</TH>
                    <TH right>{isEn ? "Txns" : "笔数"}</TH>
                    <TH>{/* 明细 */}</TH>
                  </tr>
                </thead>
                <tbody>
                  {data.ledger_summary.map(r => (
                    <tr key={r.user_id} style={{ borderTop: "1px solid #eee" }}>
                      <TD>
                        <div style={{ fontWeight: 500 }}>
                          {r.user_name !== "—" ? r.user_name : r.user_email}
                        </div>
                        <div style={{ fontSize: "0.75rem", color: "#999" }}>{r.user_email}</div>
                      </TD>
                      <TD right>
                        <strong style={{ color: r.current_balance < 0 ? "#c33" : undefined }}>
                          {r.current_balance.toLocaleString()}
                        </strong>
                      </TD>
                      <TD right><span style={{ color: "#c33" }}>{r.consumed.toLocaleString()}</span></TD>
                      <TD right><span style={{ color: "#0a7" }}>{r.refunded.toLocaleString()}</span></TD>
                      <TD right><span style={{ color: "#36a" }}>{r.recharged.toLocaleString()}</span></TD>
                      <TD right>
                        <span style={{ color: r.admin_adjusted !== 0 ? "#f80" : "#bbb" }}>
                          {r.admin_adjusted !== 0 ? `+${r.admin_adjusted.toLocaleString()}` : "0"}
                        </span>
                      </TD>
                      <TD right>
                        <strong style={{ color: r.net >= 0 ? "#0a7" : "#c33" }}>
                          {r.net >= 0 ? "+" : ""}{r.net.toLocaleString()}
                        </strong>
                      </TD>
                      <TD right>{r.tx_count}</TD>
                      <TD>
                        <button
                          onClick={() => openDetail("ledger", `${r.user_name !== "—" ? r.user_name : r.user_email} — 积分流水`, { user_id: r.user_id })}
                          style={{ padding: "0.2rem 0.7rem", border: "1px solid #ddd", borderRadius: "6px", cursor: "pointer", fontSize: "0.78rem", background: "#fafaf7", color: "#555" }}
                        >
                          {isEn ? "Detail" : "明细"}
                        </button>
                      </TD>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}
        </div>

      </div>

      {/* ── 明细弹窗 ── */}
      {modal && (
        <div
          onClick={e => { if (e.target === e.currentTarget) setModal(null); }}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem" }}
        >
          <div style={{ background: "#fff", borderRadius: "12px", width: "100%", maxWidth: "960px", maxHeight: "82vh", display: "flex", flexDirection: "column", boxShadow: "0 8px 40px rgba(0,0,0,0.2)" }}>
            {/* header */}
            <div style={{ padding: "1rem 1.5rem", borderBottom: "1px solid #eee", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: "1rem" }}>{modal.title}</div>
                {!modal.loading && (
                  <div style={{ fontSize: "0.75rem", color: "#999", marginTop: "0.2rem" }}>
                    共 {modal.total} 条 · 第 {modal.page + 1} 页（每页 50）
                  </div>
                )}
              </div>
              <button onClick={() => setModal(null)} style={{ background: "none", border: "none", cursor: "pointer", fontSize: "1.4rem", color: "#888", lineHeight: 1 }}>✕</button>
            </div>

            {/* body */}
            <div style={{ overflow: "auto", flex: 1, fontSize: "0.83rem" }}>
              {modal.loading ? (
                <div style={{ padding: "2.5rem", textAlign: "center", color: "#999" }}>加载中…</div>
              ) : modal.rows.length === 0 ? (
                <div style={{ padding: "2.5rem", textAlign: "center", color: "#999" }}>暂无记录</div>
              ) : modal.type === "jobs" ? (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead style={{ background: "#fafaf7", position: "sticky", top: 0 }}>
                    <tr>
                      {["时间","用户","标题","类型","积分","状态","耗时(s)"].map(h => (
                        <th key={h} style={{ textAlign: "left", padding: "0.7rem 0.9rem", color: "#666", fontWeight: 500, whiteSpace: "nowrap", borderBottom: "1px solid #eee" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {modal.rows.map((r, i) => (
                      <tr key={i} style={{ borderTop: "1px solid #f0f0f0" }}>
                        <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap", color: "#666" }}>{r.created_at}</td>
                        <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap" }}>
                          <div style={{ fontWeight: 500 }}>{String(r.user_name) !== "—" ? r.user_name : r.user_email}</div>
                          <div style={{ fontSize: "0.72rem", color: "#aaa" }}>{r.user_email}</div>
                        </td>
                        <td style={{ padding: "0.65rem 0.9rem", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.title || "—"}</td>
                        <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap", color: "#777", fontFamily: "monospace", fontSize: "0.78rem" }}>{r.module || r.type}</td>
                        <td style={{ padding: "0.65rem 0.9rem", textAlign: "right", fontWeight: 600 }}>{r.cost}</td>
                        <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap" }}>
                          <span style={{ padding: "0.15rem 0.5rem", borderRadius: "999px", fontSize: "0.72rem", background: r.status === "completed" ? "#eaf7ea" : r.status === "failed" ? "#fde8e8" : "#fff4e0", color: r.status === "completed" ? "#0a7" : r.status === "failed" ? "#c33" : "#f80" }}>
                            {r.status}
                          </span>
                        </td>
                        <td style={{ padding: "0.65rem 0.9rem", textAlign: "right", color: "#888" }}>{r.duration_sec ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : modal.type === "ledger" ? (
                <div>
                  {/* 充值记录区块 */}
                  {modal.recharges.length > 0 && (
                    <div style={{ margin: "1rem 1rem 0", padding: "0.9rem 1rem", background: "#f0f7ff", borderRadius: "8px", border: "1px solid #c5ddf7" }}>
                      <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "#1a56db", marginBottom: "0.6rem" }}>
                        充值记录（共 {modal.recharges.length} 次）
                      </div>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
                        <thead>
                          <tr>
                            {["时间","充值积分","充值后余额","订单号"].map(h => (
                              <th key={h} style={{ textAlign: "left", padding: "0.3rem 0.6rem", color: "#555", fontWeight: 500 }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {modal.recharges.map((r, i) => (
                            <tr key={i} style={{ borderTop: "1px solid #d4e8fc" }}>
                              <td style={{ padding: "0.4rem 0.6rem", color: "#444" }}>{r.created_at}</td>
                              <td style={{ padding: "0.4rem 0.6rem", fontWeight: 700, color: "#1a56db" }}>+{r.delta.toLocaleString()}</td>
                              <td style={{ padding: "0.4rem 0.6rem" }}>{r.balance_after.toLocaleString()}</td>
                              <td style={{ padding: "0.4rem 0.6rem", fontFamily: "monospace", fontSize: "0.75rem", color: "#888" }}>{r.ref_id || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {/* 全量流水 */}
                  <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead style={{ background: "#fafaf7", position: "sticky", top: 0 }}>
                      <tr>
                        {["时间","类型","接口/模型","变动","余额","任务ID"].map(h => (
                          <th key={h} style={{ textAlign: "left", padding: "0.7rem 0.9rem", color: "#666", fontWeight: 500, whiteSpace: "nowrap", borderBottom: "1px solid #eee" }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {modal.rows.map((r, i) => {
                        const delta = Number(r.delta);
                        return (
                          <tr key={i} style={{ borderTop: "1px solid #f0f0f0" }}>
                            <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap", color: "#666" }}>{r.created_at}</td>
                            <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap" }}>
                              <span style={{
                                padding: "0.15rem 0.5rem", borderRadius: "999px", fontSize: "0.72rem",
                                background: String(r.reason).startsWith("recharge") ? "#e8f0fe" : delta < 0 ? "#fde8e8" : "#eaf7ea",
                                color: String(r.reason).startsWith("recharge") ? "#1a56db" : delta < 0 ? "#c33" : "#0a7",
                              }}>
                                {String(r.reason_label || r.reason)}
                              </span>
                            </td>
                            <td style={{ padding: "0.65rem 0.9rem", color: "#555", fontSize: "0.78rem" }}>
                              {String(r.module) !== "—" ? r.module : <span style={{ color: "#ccc" }}>—</span>}
                            </td>
                            <td style={{ padding: "0.65rem 0.9rem", textAlign: "right", fontWeight: 700, color: delta > 0 ? "#0a7" : "#c33" }}>
                              {delta > 0 ? "+" : ""}{delta}
                            </td>
                            <td style={{ padding: "0.65rem 0.9rem", textAlign: "right" }}>{r.balance_after}</td>
                            <td style={{ padding: "0.65rem 0.9rem", fontFamily: "monospace", fontSize: "0.75rem", color: "#aaa" }}>{r.ref_id || "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead style={{ background: "#fafaf7", position: "sticky", top: 0 }}>
                    <tr>
                      {["时间","用户","类型","状态","扣积分","退积分","fal USD","耗时(s)","错误"].map(h => (
                        <th key={h} style={{ textAlign: "left", padding: "0.7rem 0.9rem", color: "#666", fontWeight: 500, whiteSpace: "nowrap", borderBottom: "1px solid #eee" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {modal.rows.map((r, i) => (
                      <tr key={i} style={{ borderTop: "1px solid #f0f0f0" }}>
                        <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap", color: "#666" }}>{r.created_at}</td>
                        <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap" }}>{r.user_email}</td>
                        <td style={{ padding: "0.65rem 0.9rem", fontFamily: "monospace", fontSize: "0.78rem" }}>{r.type}/{r.replacement_mode}</td>
                        <td style={{ padding: "0.65rem 0.9rem", whiteSpace: "nowrap" }}>
                          <span style={{ padding: "0.15rem 0.5rem", borderRadius: "999px", fontSize: "0.72rem", background: r.status === "completed" ? "#eaf7ea" : r.status === "failed" ? "#fde8e8" : r.status === "partial_completed" ? "#fff4e0" : "#f5f5f5", color: r.status === "completed" ? "#0a7" : r.status === "failed" ? "#c33" : r.status === "partial_completed" ? "#f80" : "#888" }}>
                            {r.status}
                          </span>
                        </td>
                        <td style={{ padding: "0.65rem 0.9rem", textAlign: "right", fontWeight: 600 }}>{r.credits_charged}</td>
                        <td style={{ padding: "0.65rem 0.9rem", textAlign: "right", color: "#0a7" }}>{r.credits_refunded}</td>
                        <td style={{ padding: "0.65rem 0.9rem", textAlign: "right", color: "#666" }}>${r.fal_usd}</td>
                        <td style={{ padding: "0.65rem 0.9rem", textAlign: "right", color: "#888" }}>{r.duration_sec ?? "—"}</td>
                        <td style={{ padding: "0.65rem 0.9rem", color: "#c33", fontSize: "0.75rem", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.error || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* footer pagination */}
            {!modal.loading && modal.total > 50 && (
              <div style={{ padding: "0.8rem 1.5rem", borderTop: "1px solid #eee", display: "flex", gap: "0.5rem", alignItems: "center", justifyContent: "flex-end", flexShrink: 0 }}>
                <span style={{ fontSize: "0.8rem", color: "#888", marginRight: "auto" }}>
                  {modal.offset + 1}–{Math.min(modal.page * 50 + 50, modal.total)} / {modal.total}
                </span>
                <button
                  disabled={modal.page === 0}
                  onClick={() => openDetail(modal.type, modal.title, modal.params, modal.page - 1)}
                  style={{ padding: "0.3rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px", cursor: modal.page === 0 ? "not-allowed" : "pointer", opacity: modal.page === 0 ? 0.4 : 1, background: "#fff" }}
                >
                  ← {isEn ? "Prev" : "上一页"}
                </button>
                <button
                  disabled={(modal.page + 1) * 50 >= modal.total}
                  onClick={() => openDetail(modal.type, modal.title, modal.params, modal.page + 1)}
                  style={{ padding: "0.3rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px", cursor: (modal.page + 1) * 50 >= modal.total ? "not-allowed" : "pointer", opacity: (modal.page + 1) * 50 >= modal.total ? 0.4 : 1, background: "#fff" }}
                >
                  {isEn ? "Next" : "下一页"} →
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
