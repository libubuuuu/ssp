"use client";

import Link from "next/link";

export default function VideoWorkflowPage() {
  return (
    <div className="max-w-2xl mx-auto py-12 px-6">
      <Link href="/video" className="text-amber-400 hover:underline mb-6 inline-block">
        ← 返回视频生成
      </Link>
      <h1 className="text-2xl font-bold mb-4">图生视频工作流</h1>
      <p className="text-zinc-500 mb-6">
        上传首帧或首尾帧，按镜头填写脚本与分镜，保持人物一致与整片连贯性（一镜到底观感）。
      </p>
      <div className="p-8 rounded-lg bg-zinc-900 border border-zinc-700 text-center text-zinc-500">
        工作流 UI 开发中...
      </div>
    </div>
  );
}
