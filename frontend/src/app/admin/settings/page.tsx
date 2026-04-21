"use client";
import { useState, useRef } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://43.134.71.189:8000";

export default function AdminSettingsPage() {
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [qrUrl, setQrUrl] = useState(`/qr-payment.png?v=${Date.now()}`);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleUpload = async (file: File) => {
    setError(""); setMsg(""); setUploading(true);
    try {
      const token = localStorage.getItem("token") || "";
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/admin/upload-qr`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "上传失败");
      setMsg(`✅ 上传成功！大小 ${(data.size/1024).toFixed(1)}KB`);
      setQrUrl(`/qr-payment.png?v=${Date.now()}`);
    } catch (e: any) { setError(e.message); }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = ""; }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", maxWidth: 800 }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999" }}>管理员</div>
          <h1 style={{ fontSize: "1.6rem", fontWeight: 400, margin: "0.3rem 0", fontFamily: "Georgia,serif" }}>系统 <span style={{ fontStyle: "italic" }}>设置</span></h1>
        </div>

        <div style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, border: "1px solid #eee", marginBottom: "1rem" }}>
          <h3 style={{ marginTop: 0 }}>收款码</h3>
          <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "1rem" }}>用户在充值页会看到这张二维码</div>

          <div style={{ display: "flex", gap: "1.5rem", alignItems: "flex-start" }}>
            <div style={{ background: "#fafaf7", padding: "1rem", borderRadius: 10, width: 200 }}>
              <img src={qrUrl} alt="当前收款码" onError={(e: any) => e.target.style.display = "none"}
                style={{ width: "100%", display: "block" }} />
            </div>
            <div style={{ flex: 1 }}>
              <input ref={fileRef} type="file" accept="image/*"
                onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f); }}
                disabled={uploading}
                style={{ display: "block", marginBottom: "1rem" }} />
              {msg && <div style={{ color: "#0a0", fontSize: "0.85rem", marginBottom: "0.5rem" }}>{msg}</div>}
              {error && <div style={{ color: "#c00", fontSize: "0.85rem", marginBottom: "0.5rem" }}>{error}</div>}
              <div style={{ fontSize: "0.8rem", color: "#999" }}>
                支持 PNG/JPG，最大 5MB。<br/>
                上传后立刻生效，无需重启。
              </div>
            </div>
          </div>
        </div>

        <div style={{ background: "#fff8ea", border: "1px solid #f5d884", padding: "1rem", borderRadius: 10, fontSize: "0.85rem", color: "#7a5400" }}>
          💡 充值流程：用户扫码付款 → 发订单号给你 → 你到 <a href="/admin/orders" style={{ color: "#7a5400", fontWeight: 500 }}>订单管理</a> 点击"确认入账"
        </div>
      </main>
    </div>
  );
}
