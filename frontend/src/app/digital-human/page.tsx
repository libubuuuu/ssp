"use client";

import Link from "next/link";

export default function DigitalHumanPage() {
  return (
    <div className="max-w-2xl mx-auto py-16 px-6 text-center">
      <h1 className="text-2xl font-bold mb-3">数字人(图片 + 脚本)</h1>
      <p className="text-zinc-500 mb-8">即将上线</p>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-6 text-left text-sm leading-relaxed text-zinc-300">
        <p>
          这个能力(上传图片 + 输入脚本生成数字人对口型视频)还在排期接入,
          暂不可用。<strong className="text-amber-400">不会扣除积分</strong>。
        </p>
        <p className="mt-3">
          已经能用的相邻功能:
        </p>
        <ul className="mt-2 list-disc pl-5 space-y-1">
          <li>
            <Link href="/avatar" className="text-amber-400 hover:underline">
              数字人(图片 + 音频)
            </Link>{" "}
            — 上传半身照和音频文件,精准对口型
          </li>
          <li>
            <Link href="/voice-clone" className="text-amber-400 hover:underline">
              语音克隆 / TTS
            </Link>{" "}
            — 文本转语音,可作为数字人音频输入
          </li>
        </ul>
      </div>
    </div>
  );
}
