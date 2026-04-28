import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  // 六十四续:关掉 @next/next/no-img-element
  // 项目决定:本站绝大多数 <img> 是用户上传 blob URL(URL.createObjectURL)+
  // FAL 生成动态 URL(尺寸未知),都不适合 next/Image(blob 无法 loader 优化、
  // 动态 URL 必须 fill 模式 + 父 relative)。
  // 唯一适合的 1 处静态 /qr-payment.png 已迁 next/Image(pricing/page.tsx)。
  // 维持 <img> 的 maintenance cost > LCP 优化的边际收益,关 rule 不再噪音。
  {
    rules: {
      "@next/next/no-img-element": "off",
    },
  },
]);

export default eslintConfig;
