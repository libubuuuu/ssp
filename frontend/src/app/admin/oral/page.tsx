"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface OralItem {
  id: string;
  user_id: string;
  user_email: string | null;
  tier: string;
  status: string;
  duration_seconds: number;
  credits_charged: number;
  credits_refunded: number;
  credits_net: number;
  error_step: string | null;
  error_message: string | null;
  final_video_url: string | null;
  created_at: string;
  completed_at: string | null;
  step_progress: {
    step1_asr: boolean;
    step2_edit: boolean;
    step3_audio: boolean;
    step4_swap: boolean;
    step5_final: boolean;
  };
}

interface OralResponse {
  summary: {
    total: number;
    avg_duration_seconds: number;
    avg_net_credits: number;
    total_net_credits: number;
    status_counts: Record<string, number>;
  };
  failure_top: Array<{ step: string | null; message: string; count: number }>;
  items: OralItem[];
}

const STATUS_LABEL_ZH: Record<string, string> = {
  uploaded: "已上传",
  asr_running: "①识别中",
  asr_done: "①识别完",
  edit_submitted: "②编辑已提交",
  tts_running: "③音频中",
  tts_done: "③音频完",
  swap_running: "④换装中",
  lipsync_running: "⑤合成中",
  completed: "✅ 完成",
  cancelled: "已取消",
  failed_step1: "❌ ①ASR 失败",
  failed_step3: "❌ ③音频失败",
  failed_step4: "❌ ④换装失败",
  failed_step5: "❌ ⑤合成失败",
};

function statusLabel(s: string, isEn: boolean): string {
  if (isEn) return s;
  return STATUS_LABEL_ZH[s] || s;
}

const STATUS_COLOR: Record<string, string> = {
  completed: "#0a7d35",
  cancelled: "#888",
};
function statusColor(s: string): string {
  if (STATUS_COLOR[s]) return STATUS_COLOR[s];
  if (s.startsWith("failed_")) return "#c00";
  return "#0d6efd";  // running 状态
}

export default function AdminOralPage() {
  const { lang } = useLang();
  const router = useRouter();
  const isEn = lang === "en";
  const [data, setData] = useState<OralResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [tierFilter, setTierFilter] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token") ?? "";
      const params = new URLSearchParams();
      if (statusFilter) params.set("status", statusFilter);
      if (tierFilter) params.set("tier", tierFilter);
      params.set("limit", "100");
      const res = await fetch(`${API_BASE}/api/admin/oral-tasks?${params.toString()}`, {
        headers: { Authorization: `Bearer ${token}` },
        credentials: "include",
      });
      if (res.status === 403) {
        alert(isEn ? "Admin only" : "仅管理员可访问");
        router.push("/dashboard");
        return;
      }
      const body: OralResponse = await res.json();
      setData(body);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, tierFilter, isEn, router]);

  useEffect(() => { load(); }, [load]);

  const fmtTime = (s: string | null) => {
    if (!s) return "-";
    return s.replace("T", " ").replace(/\.\d+$/, "").slice(0, 19);
  };

  return (
    <main style={{ padding: "1.5rem", maxWidth: 1400, margin: "0 auto" }}>
      <h1 style={{ fontSize: "1.4rem", fontWeight: 600, marginTop: 0 }}>
        🎤 {isEn ? "Oral Broadcast Tasks" : "口播带货任务"}
      </h1>
      <p style={{ color: "#888", fontSize: "0.85rem", marginBottom: "1.5rem" }}>
        {isEn
          ? "Operations view of all oral_sessions: status distribution, failure top, per-task drill-down."
          : "运营视图:总览所有 oral_sessions 任务,看状态分布、失败 top、单任务卡哪一步。"}
      </p>

      {/* 汇总 */}
      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "1rem", marginBottom: "1.5rem" }}>
          <Card label={isEn ? "Total" : "总数"} value={data.summary.total} />
          <Card label={isEn ? "Completed" : "已完成"} value={data.summary.status_counts.completed || 0} accent="#0a7d35" />
          <Card label={isEn ? "Failed" : "失败"} value={
            Object.entries(data.summary.status_counts)
              .filter(([k]) => k.startsWith("failed_"))
              .reduce((sum, [, v]) => sum + v, 0)
          } accent="#c00" />
          <Card label={isEn ? "Running" : "进行中"} value={
            Object.entries(data.summary.status_counts)
              .filter(([k]) => k.endsWith("_running"))
              .reduce((sum, [, v]) => sum + v, 0)
          } accent="#0d6efd" />
          <Card label={isEn ? "Avg duration" : "平均时长"} value={`${data.summary.avg_duration_seconds}s`} />
          <Card label={isEn ? "Avg net credits" : "平均净扣"} value={data.summary.avg_net_credits} />
          <Card label={isEn ? "Total net credits" : "总净扣"} value={data.summary.total_net_credits} />
        </div>
      )}

      {/* 失败 top */}
      {data && data.failure_top.length > 0 && (
        <section style={{ background: "#fff5f5", border: "1px solid #ffd6d6", padding: "1rem 1.2rem", borderRadius: 10, marginBottom: "1.5rem" }}>
          <strong style={{ color: "#c00" }}>{isEn ? "Top failures" : "失败 top 5"}</strong>
          <ul style={{ marginTop: "0.5rem", marginBottom: 0, paddingLeft: "1.2rem" }}>
            {data.failure_top.map((f, i) => (
              <li key={i} style={{ fontSize: "0.85rem", marginBottom: "0.25rem" }}>
                <code style={{ background: "#fff", padding: "1px 6px", borderRadius: 3 }}>×{f.count}</code>{" "}
                <strong>{f.step || "?"}</strong> — {f.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 过滤 */}
      <div style={{ marginBottom: "1rem", display: "flex", gap: "0.8rem", alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ fontSize: "0.85rem", color: "#666" }}>
          {isEn ? "Status:" : "状态:"}{" "}
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
            style={{ padding: "0.3rem 0.5rem", borderRadius: 6, border: "1px solid #ddd" }}>
            <option value="">{isEn ? "(all)" : "(全部)"}</option>
            <option value="completed">completed</option>
            <option value="failed_step1">failed_step1</option>
            <option value="failed_step3">failed_step3</option>
            <option value="failed_step4">failed_step4</option>
            <option value="failed_step5">failed_step5</option>
            <option value="cancelled">cancelled</option>
          </select>
        </label>
        <label style={{ fontSize: "0.85rem", color: "#666" }}>
          {isEn ? "Tier:" : "档位:"}{" "}
          <select value={tierFilter} onChange={e => setTierFilter(e.target.value)}
            style={{ padding: "0.3rem 0.5rem", borderRadius: 6, border: "1px solid #ddd" }}>
            <option value="">{isEn ? "(all)" : "(全部)"}</option>
            <option value="economy">economy</option>
            <option value="standard">standard</option>
            <option value="premium">premium</option>
          </select>
        </label>
        <button onClick={load} style={{ padding: "0.3rem 0.8rem", border: "1px solid #ddd", background: "#fff", borderRadius: 6, cursor: "pointer", fontSize: "0.85rem" }}>
          {isEn ? "Refresh" : "刷新"}
        </button>
      </div>

      {/* 列表 */}
      {loading && <p style={{ color: "#888" }}>{isEn ? "Loading..." : "加载中..."}</p>}
      {!loading && data && data.items.length === 0 && (
        <p style={{ color: "#888" }}>{isEn ? "No tasks." : "暂无任务"}</p>
      )}
      {!loading && data && data.items.length > 0 && (
        <div style={{ background: "#fff", borderRadius: 10, overflow: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem" }}>
            <thead>
              <tr style={{ background: "#f5f3ed", textAlign: "left" }}>
                <th style={th}>{isEn ? "Created" : "创建时间"}</th>
                <th style={th}>{isEn ? "User" : "用户"}</th>
                <th style={th}>Tier</th>
                <th style={th}>{isEn ? "Status" : "状态"}</th>
                <th style={th}>{isEn ? "Steps" : "步骤"}</th>
                <th style={th}>{isEn ? "Dur (s)" : "时长 s"}</th>
                <th style={th}>{isEn ? "Net credits" : "净扣"}</th>
                <th style={th}>{isEn ? "Error" : "错误"}</th>
                <th style={th}>{isEn ? "Output" : "产物"}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map(it => {
                const stepBits = [
                  it.step_progress.step1_asr,
                  it.step_progress.step2_edit,
                  it.step_progress.step3_audio,
                  it.step_progress.step4_swap,
                  it.step_progress.step5_final,
                ];
                return (
                  <tr key={it.id} style={{ borderTop: "1px solid #eee" }}>
                    <td style={td}>{fmtTime(it.created_at)}</td>
                    <td style={{ ...td, fontFamily: "monospace", fontSize: "0.75rem" }}>{it.user_email || it.user_id.slice(0, 8)}</td>
                    <td style={td}>{it.tier}</td>
                    <td style={{ ...td, color: statusColor(it.status), fontWeight: 500 }}>{statusLabel(it.status, isEn)}</td>
                    <td style={{ ...td, fontFamily: "monospace" }}>
                      {stepBits.map((b, i) => (
                        <span key={i} style={{ color: b ? "#0a7d35" : "#ccc" }}>{b ? "●" : "○"}</span>
                      ))}
                    </td>
                    <td style={td}>{it.duration_seconds?.toFixed(1)}</td>
                    <td style={td}>{it.credits_net}{it.credits_refunded > 0 ? <span style={{ color: "#888" }}> (退{it.credits_refunded})</span> : null}</td>
                    <td style={{ ...td, color: "#c00", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={it.error_message ?? ""}>
                      {it.error_message || "-"}
                    </td>
                    <td style={td}>
                      {it.final_video_url ? <a href={it.final_video_url} target="_blank" rel="noreferrer">▶</a> : "-"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

const th: React.CSSProperties = { padding: "0.6rem 0.8rem", fontWeight: 500, fontSize: "0.8rem", color: "#666" };
const td: React.CSSProperties = { padding: "0.6rem 0.8rem", verticalAlign: "top" };

function Card({ label, value, accent }: { label: string; value: number | string; accent?: string }) {
  return (
    <div style={{ background: "#fff", padding: "1rem 1.2rem", borderRadius: 10, border: "1px solid #eee" }}>
      <div style={{ fontSize: "0.75rem", color: "#888", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: "1.4rem", fontWeight: 600, color: accent ?? "#0d0d0d" }}>{value}</div>
    </div>
  );
}
