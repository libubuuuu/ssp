"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

// 历史时间格式化:DB 的 created_at 是 UTC 无时区串(如 "2026-06-15 17:49:21"),
// 补成 ISO UTC("...T...Z")再按用户本地时区显示;解析失败回退原串前 16 位。
function fmtLocal(s?: string, withSec = false): string {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) return s.slice(0, withSec ? 19 : 16);
  return d.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", ...(withSec ? { second: "2-digit" } : {}),
    hour12: false,
  });
}

export default function HistoryPage() {
  const { t } = useLang();
  interface HistoryItem {
    id: string;
    module: string;
    prompt?: string;
    images?: string[];
    videos?: string[];
    cost?: number;
    created_at?: string;
    status?: string;
    credits_refunded?: number;
  }
  interface LedgerRecord {
    id: string;
    delta: number;
    balance_after: number;
    reason: string;
    label: string;
    ref_id?: string;
    module?: string;
    created_at: number;
  }
  const [activeTab, setActiveTab] = useState<"works" | "credits">("works");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<HistoryItem | null>(null);
  const [ledger, setLedger] = useState<LedgerRecord[]>([]);
  const [ledgerLoading, setLedgerLoading] = useState(false);
  const [ledgerFetched, setLedgerFetched] = useState(false);
  const [ledgerError, setLedgerError] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("token") || "";
    const headers = { "Authorization": `Bearer ${token}` };

    Promise.all([
      fetch(`${API_BASE}/api/tasks/history`, { headers })
        .then(r => r.ok ? r.json() : { history: [] })
        .then(data => (data.history || []) as HistoryItem[])
        .catch(() => [] as HistoryItem[]),

      fetch(`${API_BASE}/api/video/clone-v2/jobs`, { headers })
        .then(r => r.ok ? r.json() : { items: [] })
        .then(data => {
          const typeLabel: Record<string, string> = {
            single: "单段复刻", ultimate: "多段复刻",
          };
          const modeLabel: Record<string, string> = {
            full: "全替换", partial: "局部替换",
          };
          return ((data.items || []) as Record<string, unknown>[]).map(job => ({
            id: job.id as string,
            module: "video/clone-v2",
            prompt: [
              typeLabel[job.type as string] || (job.type as string),
              modeLabel[job.replacement_mode as string] || (job.replacement_mode as string),
            ].filter(Boolean).join(" · "),
            videos: job.final_video_url_watermarked
              ? [job.final_video_url_watermarked as string]
              : job.final_video_url_raw
              ? [job.final_video_url_raw as string]
              : [],
            cost: (job.total_credits_charged as number) ?? 0,
            created_at: job.created_at as string,
            status: job.status as string,
            credits_refunded: (job.total_credits_refunded as number) ?? 0,
          } as HistoryItem));
        })
        .catch(() => [] as HistoryItem[]),
    ]).then(([general, v2]) => {
      const merged = [...general, ...v2].sort((a, b) => {
        const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
        const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
        return tb - ta;
      });
      setHistory(merged);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    if (activeTab !== "credits" || ledgerFetched) return;
    setLedgerLoading(true);
    setLedgerError(false);
    const token = localStorage.getItem("token") || "";
    fetch(`${API_BASE}/api/billing/ledger?page=1&limit=50`, {
      headers: { "Authorization": `Bearer ${token}` },
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => {
        setLedger((data.records || []) as LedgerRecord[]);
        setLedgerFetched(true);
      })
      .catch(() => {
        setLedgerError(true);
      })
      .finally(() => setLedgerLoading(false));
  }, [activeTab, ledgerFetched]);

  const moduleLabel: Record<string, string> = {
    "video/image-to-video": t("tasks.mod_i2v"),
    "video/replace/element": t("tasks.mod_replace"),
    "video/clone": t("tasks.mod_clone"),
    "image/style": t("tasks.mod_style"),
    "image/realistic": t("tasks.mod_realistic"),
    "image/multi-reference": t("tasks.mod_multi_ref"),
    "avatar/generate": t("tasks.mod_avatar"),
    "voice/clone": t("tasks.mod_voice_clone"),
    "voice/tts": t("tasks.mod_tts"),
    "video/editor/compose": t("tasks.mod_editor_compose"),
    "video/replicate": t("tasks.mod_replicate"),
    "video/clone-v2": "视频复刻",
  };

  function StatusBadge({ item }: { item: HistoryItem }) {
    if (!item.status) return null;
    if (item.status === "completed") {
      return (
        <span style={{ fontSize: "0.7rem", padding: "2px 7px", borderRadius: "20px", background: "#e6f9ee", color: "#1a7a40", fontWeight: 500 }}>
          ✅ 成功
        </span>
      );
    }
    if (item.status === "failed") {
      return (
        <span style={{ fontSize: "0.7rem", padding: "2px 7px", borderRadius: "20px", background: "#fdecea", color: "#b71c1c", fontWeight: 500 }}>
          ❌ 失败{item.credits_refunded ? `（已退 ${item.credits_refunded} 积分）` : ""}
        </span>
      );
    }
    if (item.status === "processing" || item.status === "pending") {
      return (
        <span style={{ fontSize: "0.7rem", padding: "2px 7px", borderRadius: "20px", background: "#f0f0f0", color: "#666", fontWeight: 500 }}>
          ⏳ 处理中
        </span>
      );
    }
    return null;
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1280, width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>{t("tasks.genRecord")}</div>
          <h1 style={{ fontSize: "1.6rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>{t("tasks.titleMain")}<span style={{ fontStyle: "italic" }}> {t("tasks.titleAccent")}</span></h1>
        </div>

        {/* 保存期限提示 */}
        <div style={{ marginBottom: "1.2rem", padding: "0.6rem 1rem", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: "8px", fontSize: "0.82rem", color: "#92400e" }}>
          生成的图片和视频保存 <b>7 天</b>，到期后自动清除（积分记录和账单不受影响，永久保留）。请及时下载保存。
        </div>

        {/* Tab switcher */}
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1.5rem" }}>
          {(["works", "credits"] as const).map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)}
              style={{
                padding: "0.45rem 1.1rem",
                borderRadius: "20px",
                border: "none",
                cursor: "pointer",
                fontWeight: activeTab === tab ? 600 : 400,
                fontSize: "0.88rem",
                background: activeTab === tab ? "#0d0d0d" : "#e8e5df",
                color: activeTab === tab ? "#fff" : "#555",
                transition: "background 0.15s",
              }}>
              {tab === "works" ? "作品历史" : "积分明细"}
            </button>
          ))}
        </div>

        {/* Credits tab */}
        {activeTab === "credits" && (
          <div>
            {ledger.length > 0 && (
              <div style={{ marginBottom: "1rem", padding: "0.75rem 1.1rem", background: "#fff", borderRadius: "12px", boxShadow: "0 2px 8px rgba(0,0,0,0.05)", display: "inline-block", fontSize: "0.9rem", color: "#333" }}>
                当前余额 <strong style={{ fontSize: "1.1rem", color: "#0d0d0d" }}>{ledger[0].balance_after}</strong> 积分
              </div>
            )}
            {ledgerLoading && <div style={{ color: "#999", textAlign: "center", marginTop: "4rem" }}>加载中…</div>}
            {ledgerError && <div style={{ color: "#b71c1c", textAlign: "center", marginTop: "4rem" }}>加载失败，请刷新重试</div>}
            {!ledgerLoading && !ledgerError && ledger.length === 0 && (
              <div style={{ color: "#999", textAlign: "center", marginTop: "4rem" }}>暂无积分记录</div>
            )}
            {!ledgerLoading && ledger.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {ledger.map(rec => {
                  const balanceBefore = rec.balance_after - rec.delta;
                  const isNeg = rec.delta < 0;
                  const isPos = rec.delta > 0;
                  return (
                    <div key={rec.id} style={{ background: "#fff", borderRadius: "12px", padding: "0.75rem 1.1rem", display: "flex", alignItems: "center", justifyContent: "space-between", boxShadow: "0 2px 6px rgba(0,0,0,0.04)", flexWrap: "wrap", gap: "0.5rem" }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: "0.88rem", fontWeight: 500, color: "#222" }}>{rec.label || rec.reason}</div>
                        <div style={{ fontSize: "0.75rem", color: "#999", marginTop: "0.15rem" }}>
                          {new Date(rec.created_at * 1000).toLocaleString()}
                          {rec.module && <span style={{ marginLeft: "0.5rem", color: "#bbb" }}>{rec.module}</span>}
                        </div>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: "1rem", flexShrink: 0 }}>
                        <span style={{ fontSize: "1rem", fontWeight: 700, color: isNeg ? "#c0392b" : isPos ? "#1a7a40" : "#555" }}>
                          {isPos ? "+" : ""}{rec.delta}
                        </span>
                        <span style={{ fontSize: "0.78rem", color: "#999", whiteSpace: "nowrap" }}>
                          {balanceBefore} → {rec.balance_after}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Works tab */}
        {activeTab === "works" && loading && <div style={{ color: "#999", textAlign: "center", marginTop: "4rem" }}>{t("tasks.loading")}</div>}
        {activeTab === "works" && !loading && history.length === 0 && (
          <div style={{ color: "#999", textAlign: "center", marginTop: "4rem" }}>{t("tasks.empty")}</div>
        )}

        <div style={{ display: activeTab === "works" ? "grid" : "none", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "1rem" }}>
          {history.map((item) => (
            <div key={item.id} onClick={() => setSelected(item)}
              style={{ background: "#fff", borderRadius: "14px", overflow: "hidden", boxShadow: "0 4px 12px rgba(0,0,0,0.06)", cursor: "pointer", transition: "transform 0.15s" }}
              onMouseEnter={e => (e.currentTarget.style.transform = "translateY(-2px)")}
              onMouseLeave={e => (e.currentTarget.style.transform = "translateY(0)")}>
              {item.videos?.[0] && (
                <video src={item.videos[0]} style={{ width: "100%", display: "block", maxHeight: "180px", objectFit: "cover" }} />
              )}
              {item.images?.[0] && !item.videos?.[0] && (
                <img src={item.images[0]} alt="" style={{ width: "100%", display: "block", maxHeight: "180px", objectFit: "cover" }} />
              )}
              {!item.videos?.[0] && !item.images?.[0] && (
                <div style={{ height: "120px", background: "#f5f5f0", display: "flex", alignItems: "center", justifyContent: "center", color: "#ccc", fontSize: "2rem" }}>
                  {item.module?.includes("video") ? "▶" : "🖼"}
                </div>
              )}
              <div style={{ padding: "0.75rem 1rem" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.3rem", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.85rem", fontWeight: 500, color: "#333" }}>{moduleLabel[item.module] || item.module}</span>
                  <StatusBadge item={item} />
                </div>
                <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: "0.4rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{item.prompt}</div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", color: "#999" }}>
                  <span>{fmtLocal(item.created_at)}</span>
                  <span>{item.cost} {t("tasks.cost")}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </main>

      {/* 详情弹窗 */}
      {selected && (
        <div onClick={() => setSelected(null)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: "2rem" }}>
          <div onClick={e => e.stopPropagation()}
            style={{ background: "#fff", borderRadius: "20px", maxWidth: "700px", width: "100%", maxHeight: "90vh", overflow: "auto", padding: "2rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                <h2 style={{ margin: 0, fontSize: "1.2rem", fontWeight: 500 }}>{moduleLabel[selected.module] || selected.module}</h2>
                <StatusBadge item={selected} />
              </div>
              <button onClick={() => setSelected(null)} style={{ background: "none", border: "none", fontSize: "1.5rem", cursor: "pointer", color: "#999" }}>×</button>
            </div>

            {selected.videos?.[0] && (
              <video src={selected.videos[0]} controls style={{ width: "100%", borderRadius: "12px", marginBottom: "1rem" }} />
            )}
            {selected.images?.[0] && !selected.videos?.[0] && (
              <img src={selected.images[0]} alt="" style={{ width: "100%", borderRadius: "12px", marginBottom: "1rem" }} />
            )}

            <div style={{ fontSize: "0.88rem", color: "#555", marginBottom: "1rem", lineHeight: 1.6 }}>
              <strong>{t("tasks.promptLabel")}</strong>{selected.prompt}
            </div>
            <div style={{ fontSize: "0.82rem", color: "#999", marginBottom: "1.5rem" }}>
              {fmtLocal(selected.created_at, true)} · {t("tasks.consume")} {selected.cost} {t("tasks.cost")}
            </div>

            <div style={{ display: "flex", gap: "0.75rem" }}>
              {selected.videos?.[0] && (
                <a href={selected.videos[0]} download target="_blank" rel="noreferrer"
                  style={{ flex: 1, padding: "0.75rem", background: "#0d0d0d", color: "#fff", textAlign: "center", borderRadius: "10px", textDecoration: "none", fontSize: "0.88rem" }}>{t("tasks.downloadVideo")}</a>
              )}
              {selected.images?.[0] && (
                <a href={selected.images[0]} download target="_blank" rel="noreferrer"
                  style={{ flex: 1, padding: "0.75rem", background: "#0d0d0d", color: "#fff", textAlign: "center", borderRadius: "10px", textDecoration: "none", fontSize: "0.88rem" }}>{t("tasks.downloadImage")}</a>
              )}
              <button onClick={() => setSelected(null)}
                style={{ flex: 1, padding: "0.75rem", background: "#f5f5f0", border: "none", borderRadius: "10px", cursor: "pointer", fontSize: "0.88rem", color: "#333" }}>{t("tasks.close")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
