/**
 * 法律文档页 wrapper — privacy / terms / cookie 三页共用
 *
 * 六十六续:
 * - markdown 源文件在 src/legal/*.md(从 docs/legal/ 同步而来)
 * - server component fs.readFileSync(build 时读,不在 runtime)
 * - react-markdown + remark-gfm 渲染 GFM(table / 链接 / 列表)
 *
 * 设计:
 * - 占位符 {{XXX}} 由 ENV 替换或保留(等用户法务审阅后换);本组件原样渲染,
 *   未替换占位符在页面可见,提醒用户上线前替换
 */
import fs from "fs";
import path from "path";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface LegalPageProps {
  filename: string;  // 'privacy-policy' | 'terms-of-service' | 'cookie-consent'
  title: string;
}

export default function LegalPage({ filename, title }: LegalPageProps) {
  // build 时读文件(server component,Next.js 构建期间执行)
  const filePath = path.join(process.cwd(), "src/legal", `${filename}.md`);
  const md = fs.readFileSync(filePath, "utf-8");

  return (
    <main style={{ maxWidth: "780px", margin: "0 auto", padding: "3rem 1.5rem", lineHeight: "1.7", fontSize: "0.95rem", color: "#222" }}>
      <article className="legal-prose">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      </article>
      <style>{`
        .legal-prose h1 { font-size: 2rem; font-weight: 600; margin: 1rem 0 1.5rem; font-family: Georgia, serif; }
        .legal-prose h2 { font-size: 1.4rem; font-weight: 600; margin: 2rem 0 1rem; padding-bottom: 0.4rem; border-bottom: 1px solid #eee; }
        .legal-prose h3 { font-size: 1.1rem; font-weight: 600; margin: 1.5rem 0 0.7rem; }
        .legal-prose h4 { font-size: 1rem; font-weight: 600; margin: 1rem 0 0.5rem; }
        .legal-prose p { margin: 0.7rem 0; }
        .legal-prose ul, .legal-prose ol { margin: 0.7rem 0; padding-left: 1.6rem; }
        .legal-prose li { margin: 0.3rem 0; }
        .legal-prose blockquote { border-left: 3px solid #ddd; padding: 0.3rem 1rem; margin: 1rem 0; color: #666; background: #fafaf7; }
        .legal-prose code { background: #f5f5f5; padding: 0.1rem 0.4rem; border-radius: 3px; font-family: monospace; font-size: 0.85em; }
        .legal-prose table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.85rem; }
        .legal-prose th, .legal-prose td { border: 1px solid #ddd; padding: 0.5rem 0.7rem; text-align: left; }
        .legal-prose th { background: #fafaf7; font-weight: 600; }
        .legal-prose a { color: #0d0d0d; text-decoration: underline; }
        .legal-prose hr { border: none; border-top: 1px solid #eee; margin: 2rem 0; }
        .legal-prose strong { font-weight: 600; }
      `}</style>
      <div style={{ marginTop: "3rem", paddingTop: "1.5rem", borderTop: "1px solid #eee", fontSize: "0.85rem", color: "#888", textAlign: "center" }}>
        {title} · <Link href="/" style={{ color: "#0d0d0d" }}>返回首页</Link>
      </div>
    </main>
  );
}
