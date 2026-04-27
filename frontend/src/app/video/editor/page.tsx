"use client";

import Link from "next/link";

export default function VideoEditorPage() {
  return (
    <div className="max-w-2xl mx-auto py-16 px-6 text-center">
      <h1 className="text-2xl font-bold mb-3">视频剪辑台</h1>
      <p className="text-zinc-500 mb-8">即将上线</p>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-6 text-left text-sm leading-relaxed text-zinc-300">
        <p>
          视频剪辑台(分镜解析、分镜重生成、脚本翻译、视频合成)还在开发中,
          <strong className="text-amber-400">不会扣除积分</strong>。
        </p>
        <p className="mt-3">已经能用的相邻功能:</p>
        <ul className="mt-2 list-disc pl-5 space-y-1">
          <li>
            <Link href="/video" className="text-amber-400 hover:underline">
              视频(图生视频 / 视频元素替换 / 视频翻拍)
            </Link>
          </li>
          <li>
            <Link href="/avatar" className="text-amber-400 hover:underline">
              数字人(图片 + 音频)
            </Link>
          </li>
        </ul>
      </div>
    </div>
  );
}
