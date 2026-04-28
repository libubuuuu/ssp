import LegalPage from "@/components/LegalPage";

export const metadata = {
  title: "用户协议 | AI Lixiao",
  description: "{{COMPANY_SHORT}} 用户协议 — 服务条款 / 行为规范 / AIGC / 付费",
};

export default function TermsPage() {
  return <LegalPage filename="terms-of-service" title="用户协议" />;
}
