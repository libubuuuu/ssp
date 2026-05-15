import Link from "next/link";

export default function SiteFooter() {
  return (
    <footer style={{
      borderTop: "1px solid #eee",
      background: "#fafaf7",
      padding: "1.5rem 1.5rem 2rem",
      fontSize: "0.8rem",
      color: "#888",
      textAlign: "center",
      lineHeight: 1.7,
    }}>
      <div style={{ maxWidth: "1100px", margin: "0 auto", display: "flex", flexWrap: "wrap", gap: "1.2rem", justifyContent: "center", alignItems: "center" }}>
        <span>© 2026 Xiao Li AI</span>
        <Link href="/privacy" style={{ color: "#666" }}>隐私政策</Link>
        <Link href="/terms" style={{ color: "#666" }}>用户协议</Link>
        <Link href="/cookie" style={{ color: "#666" }}>Cookie 政策</Link>
      </div>
    </footer>
  );
}
