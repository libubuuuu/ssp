"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { errMsg } from "@/lib/utils/errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export default function TwoFAPage() {
  const { lang } = useLang();
  const router = useRouter();
  
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  
  // 启用流程
  const [setupQR, setSetupQR] = useState("");
  const [setupSecret, setSetupSecret] = useState("");
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [showSetup, setShowSetup] = useState(false);
  
  const isEn = lang === "en";

  useEffect(() => { loadStatus(); }, []);

  const loadStatus = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/auth/2fa/status`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      setEnabled(!!data.enabled);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  const startSetup = async () => {
    setErr(""); setMsg(""); setCode("");
    try {
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/auth/2fa/setup`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || (isEn ? "Setup failed" : "初始化失败"));
      setSetupQR(data.qr_code);
      setSetupSecret(data.secret);
      setShowSetup(true);
    } catch (e) {
      setErr(errMsg(e));
    }
  };

  const confirmEnable = async () => {
    setErr(""); setMsg("");
    if (!code || code.length !== 6) {
      setErr(isEn ? "Please enter 6-digit code" : "请输入 6 位验证码");
      return;
    }
    try {
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/auth/2fa/enable`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ secret: setupSecret, code }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || (isEn ? "Enable failed" : "启用失败"));
      setMsg(isEn ? "✓ 2FA enabled successfully" : "✓ 2FA 已启用");
      setShowSetup(false);
      setEnabled(true);
      setCode("");
    } catch (e) {
      setErr(errMsg(e));
    }
  };

  const disableTwoFA = async () => {
    setErr(""); setMsg("");
    if (!code || code.length !== 6) {
      setErr(isEn ? "Enter current 6-digit code to disable" : "输入当前 6 位码以禁用");
      return;
    }
    try {
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/auth/2fa/disable`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ code }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || (isEn ? "Disable failed" : "禁用失败"));
      setMsg(isEn ? "✓ 2FA disabled" : "✓ 2FA 已禁用");
      setEnabled(false);
      setCode("");
    } catch (e) {
      setErr(errMsg(e));
    }
  };

  if (loading) return <div style={{ padding: "2rem", textAlign: "center" }}>{isEn ? "Loading..." : "加载中..."}</div>;

  return (
    <div style={{ minHeight: "100vh", background: "#f5f3ed", padding: "2rem" }}>
      <div style={{ maxWidth: 600, margin: "0 auto", background: "#fff", borderRadius: 16, padding: "2rem", border: "1px solid rgba(0,0,0,0.06)" }}>
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h1 style={{ fontSize: "1.5rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>
            🔐 {isEn ? "Two-Factor Authentication" : "双因素认证 (2FA)"}
          </h1>
          <button onClick={() => router.push("/profile")} style={{ background: "none", border: "1px solid #ddd", padding: "0.4rem 0.9rem", borderRadius: 8, cursor: "pointer", fontSize: "0.85rem" }}>
            ← {isEn ? "Back" : "返回"}
          </button>
        </div>

        <div style={{ padding: "1rem", background: enabled ? "#eaf7ea" : "#fff4e0", borderRadius: 10, marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.95rem", fontWeight: 500 }}>
            {isEn ? "Status: " : "状态："}
            <span style={{ color: enabled ? "#0a7" : "#f80" }}>
              {enabled ? (isEn ? "✓ Enabled" : "✓ 已启用") : (isEn ? "✗ Disabled" : "✗ 未启用")}
            </span>
          </div>
          <div style={{ fontSize: "0.82rem", color: "#666", marginTop: "0.5rem" }}>
            {enabled
              ? (isEn ? "Login requires both password and 6-digit code from your Authenticator app" : "登录时需输入密码和 Authenticator App 的 6 位验证码")
              : (isEn ? "Enable 2FA to add an extra layer of security to your account" : "启用 2FA 给账号增加一层防护")}
          </div>
        </div>

        {!enabled && !showSetup && (
          <button onClick={startSetup} style={{ padding: "0.75rem 1.5rem", background: "#0d0d0d", color: "#fff", border: "none", borderRadius: 10, cursor: "pointer", fontSize: "0.9rem" }}>
            {isEn ? "Set Up 2FA" : "设置 2FA"}
          </button>
        )}

        {showSetup && (
          <div>
            <div style={{ fontSize: "0.95rem", marginBottom: "1rem", fontWeight: 500 }}>
              {isEn ? "Step 1: Scan QR Code" : "第 1 步：扫描二维码"}
            </div>
            <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "1rem" }}>
              {isEn ? "Open Google Authenticator / Microsoft Authenticator / Tencent Authenticator and scan:" : "打开 Google Authenticator / Microsoft Authenticator / 腾讯身份验证器，扫描：" }
            </div>
            {setupQR && <img src={setupQR} alt="QR" style={{ width: 200, height: 200, marginBottom: "1rem", border: "1px solid #eee" }} />}
            <div style={{ fontSize: "0.78rem", color: "#888", marginBottom: "1.5rem" }}>
              {isEn ? "Or enter manually: " : "或手动输入密钥："}<code style={{ background: "#f5f3ed", padding: "0.2rem 0.5rem", borderRadius: 4 }}>{setupSecret}</code>
            </div>

            <div style={{ fontSize: "0.95rem", marginBottom: "0.5rem", fontWeight: 500 }}>
              {isEn ? "Step 2: Enter the 6-digit code shown in the app" : "第 2 步：输入 App 显示的 6 位验证码"}
            </div>
            <input value={code} onChange={e => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="000000" style={{ width: "100%", padding: "0.75rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "1.2rem", fontFamily: "monospace", textAlign: "center", letterSpacing: "0.3rem", marginBottom: "1rem", boxSizing: "border-box" }} maxLength={6} />
            
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button onClick={confirmEnable} style={{ flex: 1, padding: "0.75rem", background: "#0d0d0d", color: "#fff", border: "none", borderRadius: 10, cursor: "pointer", fontSize: "0.9rem" }}>
                {isEn ? "Confirm & Enable" : "确认并启用"}
              </button>
              <button onClick={() => { setShowSetup(false); setCode(""); setErr(""); }} style={{ padding: "0.75rem 1.5rem", background: "none", color: "#666", border: "1px solid #ddd", borderRadius: 10, cursor: "pointer", fontSize: "0.9rem" }}>
                {isEn ? "Cancel" : "取消"}
              </button>
            </div>
          </div>
        )}

        {enabled && (
          <div>
            <div style={{ fontSize: "0.9rem", marginBottom: "0.5rem", fontWeight: 500 }}>
              {isEn ? "Disable 2FA (enter current code)" : "禁用 2FA（需输入当前验证码）"}
            </div>
            <input value={code} onChange={e => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="000000" style={{ width: "100%", padding: "0.75rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "1.2rem", fontFamily: "monospace", textAlign: "center", letterSpacing: "0.3rem", marginBottom: "1rem", boxSizing: "border-box" }} maxLength={6} />
            <button onClick={disableTwoFA} style={{ padding: "0.75rem 1.5rem", background: "none", color: "#c00", border: "1px solid #c00", borderRadius: 10, cursor: "pointer", fontSize: "0.9rem" }}>
              {isEn ? "Disable 2FA" : "禁用 2FA"}
            </button>
          </div>
        )}

        {err && <div style={{ marginTop: "1rem", padding: "0.75rem", background: "#fee", color: "#c00", borderRadius: 8, fontSize: "0.85rem" }}>{err}</div>}
        {msg && <div style={{ marginTop: "1rem", padding: "0.75rem", background: "#eaf7ea", color: "#0a7", borderRadius: 8, fontSize: "0.85rem" }}>{msg}</div>}
      </div>
    </div>
  );
}
