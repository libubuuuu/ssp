"use client";
/**
 * Cookie 同意横幅(六十六续)
 *
 * - localStorage `cookie_consent` 三态:'all' / 'necessary' / null(未选)
 * - 未选时底部显示横幅,提供"接受全部 / 仅必要 / 政策详情"
 * - 严格必要 Cookie(token / refresh)无论选择都开,这是 PIPL §13 第 2 项
 *   "为订立、履行个人作为一方当事人的合同所必需"法定要件
 */
import { useState, useEffect } from "react";

const STORAGE_KEY = "cookie_consent";

export default function CookieConsent() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const v = localStorage.getItem(STORAGE_KEY);
    // 派生 state 场景 — localStorage 读取后初始化是合理用法,
    // React 19.2 set-state-in-effect 在此过严
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!v) setShow(true);
  }, []);

  const accept = (level: "all" | "necessary") => {
    localStorage.setItem(STORAGE_KEY, level);
    setShow(false);
  };

  if (!show) return null;

  return (
    <div style={{
      position: "fixed", bottom: 0, left: 0, right: 0,
      background: "#1a1a1a", color: "#eee",
      padding: "1rem 1.5rem",
      boxShadow: "0 -4px 16px rgba(0,0,0,0.2)",
      zIndex: 9999,
      fontSize: "0.88rem", lineHeight: 1.6,
    }}>
      <div style={{ maxWidth: "1200px", margin: "0 auto", display: "flex", flexWrap: "wrap", gap: "1rem", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ flex: "1 1 400px", minWidth: 0 }}>
          <strong style={{ color: "#f59e0b" }}>🍪 Cookie 偏好</strong>
          <span style={{ marginLeft: "0.5rem", color: "#bbb" }}>
            我们使用 Cookie 维持您的登录态和偏好。详见
            <a href="/cookie" target="_blank" rel="noopener noreferrer" style={{ color: "#f59e0b", marginLeft: "0.3rem", textDecoration: "underline" }}>
              《Cookie 政策》
            </a>。
          </span>
        </div>
        <div style={{ display: "flex", gap: "0.6rem", flexShrink: 0 }}>
          <button
            onClick={() => accept("necessary")}
            style={{
              padding: "0.5rem 1rem",
              background: "transparent", color: "#aaa",
              border: "1px solid #555", borderRadius: "6px",
              cursor: "pointer", fontSize: "0.85rem",
            }}>
            仅必要
          </button>
          <button
            onClick={() => accept("all")}
            style={{
              padding: "0.5rem 1.2rem",
              background: "#f59e0b", color: "#000",
              border: "none", borderRadius: "6px",
              cursor: "pointer", fontSize: "0.85rem",
              fontWeight: 600,
            }}>
            接受全部
          </button>
        </div>
      </div>
    </div>
  );
}
