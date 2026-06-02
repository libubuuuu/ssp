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

type Provider = "fal" | "aliyun" | "aiview" | "未知";

const PROVIDER_STYLE: Record<Provider, { bg: string; color: string }> = {
  fal:    { bg: "#e8f0fe", color: "#1a56db" },
  aliyun: { bg: "#fff3e0", color: "#c45f00" },
  aiview: { bg: "#e6f4ea", color: "#137333" },
  未知:   { bg: "#f1f1f1", color: "#888"    },
};

function getProvider(label: string): Provider {
  if (label === "gpt-image-2")          return "fal";
  if (label === "AI爆款视频")            return "fal";
  if (label === "分镜复刻/替换")         return "fal";
  if (label === "分镜复刻/生成")         return "fal";
  if (label === "广告视频")              return "fal";
  if (label === "图生视频/未标注")       return "fal";
  if (label === "AI爆款/分析")           return "aliyun";
  if (label === "分镜复刻/分析")         return "aliyun";
  return "未知";
}

function ProviderBadge({ label }: { label: string }) {
  const p = getProvider(label);
  const s = PROVIDER_STYLE[p];
  return (
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
      {p}
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
  const isEn = lang === "en";

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
                    <TH>{isEn ? "Provider" : "供应商"}</TH>
                    <TH>{isEn ? "Model / Type" : "模型 / 类型"}</TH>
                    <TH right>{isEn ? "Total" : "总次数"}</TH>
                    <TH right>{isEn ? "Success" : "成功"}</TH>
                    <TH right>{isEn ? "Failed" : "失败"}</TH>
                    <TH right>{isEn ? "Success %" : "成功率"}</TH>
                    <TH right>{isEn ? "Cost (credits)" : "消耗（积分）"}</TH>
                    <TH right>{isEn ? "Avg/job" : "均次积分"}</TH>
                    <TH right>{isEn ? "Users" : "用户数"}</TH>
                  </tr>
                </thead>
                <tbody>
                  {data.jobs_usage.map(r => {
                    const rate = r.count > 0 ? ((r.success / r.count) * 100).toFixed(1) : "—";
                    const avg = r.count > 0 ? Math.round(r.cost_credits / r.count) : 0;
                    return (
                      <tr key={r.model_label} style={{ borderTop: "1px solid #eee" }}>
                        <TD><ProviderBadge label={r.model_label} /></TD>
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
                    <TH>{isEn ? "Model" : "模型"}</TH>
                    <TH right>{isEn ? "Total" : "总数"}</TH>
                    <TH right>{isEn ? "Completed" : "成功"}</TH>
                    <TH right>{isEn ? "Failed" : "失败"}</TH>
                    <TH right>{isEn ? "Charged" : "已扣积分"}</TH>
                    <TH right>{isEn ? "Refunded" : "已退积分"}</TH>
                    <TH right>{isEn ? "Net Cost" : "净消耗"}</TH>
                    <TH right>{isEn ? "fal ~USD" : "fal 估算 USD"}</TH>
                    <TH right>{isEn ? "Users" : "用户"}</TH>
                  </tr>
                </thead>
                <tbody>
                  {data.vc2_usage.map(r => (
                    <tr key={r.video_model} style={{ borderTop: "1px solid #eee" }}>
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
