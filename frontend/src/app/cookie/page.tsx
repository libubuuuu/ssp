import LegalPage from "@/components/LegalPage";

export const metadata = {
  title: "Cookie 政策 | AI Lixiao",
  description: "{{COMPANY_SHORT}} Cookie 政策 — 类型 / 第三方 / 用户选择",
};

export default function CookiePage() {
  return <LegalPage filename="cookie-consent" title="Cookie 政策" />;
}
