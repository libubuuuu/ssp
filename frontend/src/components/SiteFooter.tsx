/**
 * 站点页脚 — 挂 layout.tsx 全站显示
 *
 * ICP 备案要求所有页面都有备案号 footer。本组件包含:
 * - 备案号占位符(NEXT_PUBLIC_ICP_NUMBER / NEXT_PUBLIC_POLICE_NUMBER env)
 * - 隐私 / 用户协议 / Cookie 三链接
 * - 版权年份
 *
 * 用户拿到 ICP 后只需在 frontend/.env 配:
 *   NEXT_PUBLIC_ICP_NUMBER=京ICP备XXXXXXXX号
 *   NEXT_PUBLIC_POLICE_NUMBER=京公网安备XXXXXXXX号
 *   NEXT_PUBLIC_COMPANY_NAME=北京XX科技有限公司
 *
 * 未配置时显示"备案中"占位符,提醒用户上线前替换。
 */
import Link from "next/link";

const ICP = process.env.NEXT_PUBLIC_ICP_NUMBER || "";
const POLICE = process.env.NEXT_PUBLIC_POLICE_NUMBER || "";
const COMPANY = process.env.NEXT_PUBLIC_COMPANY_NAME || "AI Lixiao";

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
        <span>© {new Date().getFullYear()} {COMPANY}</span>
        <Link href="/privacy" style={{ color: "#666" }}>隐私政策</Link>
        <Link href="/terms" style={{ color: "#666" }}>用户协议</Link>
        <Link href="/cookie" style={{ color: "#666" }}>Cookie 政策</Link>
        {ICP ? (
          <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer" style={{ color: "#666" }}>
            {ICP}
          </a>
        ) : (
          <span style={{ color: "#c00" }}>(ICP 备案中)</span>
        )}
        {POLICE && (
          <a href="http://www.beian.gov.cn/portal/registerSystemInfo" target="_blank" rel="noopener noreferrer" style={{ color: "#666" }}>
            {POLICE}
          </a>
        )}
      </div>
    </footer>
  );
}
