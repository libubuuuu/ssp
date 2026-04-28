import LegalPage from "@/components/LegalPage";

export const metadata = {
  title: "隐私政策 | AI Lixiao",
  description: "{{COMPANY_SHORT}} 隐私政策 — 个人信息收集 / 使用 / 共享 / 用户权利",
};

export default function PrivacyPage() {
  return <LegalPage filename="privacy-policy" title="隐私政策" />;
}
