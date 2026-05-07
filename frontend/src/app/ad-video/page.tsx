"use client";
// P169(2026-05-07):用户要求隐藏 /ad-video 入口,本页改 redirect。
// 原代码备份在 page.tsx.disabled,需要恢复时把 .disabled 重命名回来即可。
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function AdVideoDisabledPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/dashboard");
  }, [router]);
  return null;
}
