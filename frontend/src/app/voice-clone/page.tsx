"use client";

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface VoicePreset {
  id: string;
  name: string;
  gender: string;
  style: string;
}

export default function VoiceClonePage() {
  const [mode, setMode] = useState<"clone" | "tts">("clone");
  const [referenceAudio, setReferenceAudio] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [selectedVoice, setSelectedVoice] = useState("default");
  const [voicePresets, setVoicePresets] = useState<VoicePreset[]>([]);
  const [clonedVoiceId, setClonedVoiceId] = useState<string | null>(null);
  const [resultAudioUrl, setResultAudioUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 加载预设音色
  useState(() => {
    fetch(`${API_BASE}/api/avatar/voice/presets`)
      .then((res) => res.json())
      .then((data) => {
        if (data.voices) setVoicePresets(data.voices);
      })
      .catch(() => {});
  });

  // 处理参考音频上传
  const handleReferenceAudioUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      setReferenceAudio(e.target?.result as string);
    };
    reader.readAsDataURL(file);
  };

  // 克隆声音
  const handleCloneVoice = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!referenceAudio || !text) {
      setError("请上传参考音频并输入文案");
      return;
    }

    setLoading(true);
    setError(null);
    setClonedVoiceId(null);
    setResultAudioUrl(null);

    try {
      const res = await fetch(`${API_BASE}/api/avatar/voice/clone`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reference_audio_url: referenceAudio,
          text: text,
          model: "qwen3-tts",
        }),
      });

      const data = await res.json();

      if (data.voice_id) {
        setClonedVoiceId(data.voice_id);
        setResultAudioUrl(data.audio_url);
      } else {
        setError(data.detail || "克隆失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  };

  // 文本转语音
  const handleTTS = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text) {
      setError("请输入文案");
      return;
    }

    setLoading(true);
    setError(null);
    setResultAudioUrl(null);

    try {
      const res = await fetch(`${API_BASE}/api/avatar/voice/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: text,
          voice_id: selectedVoice,
          speed: 1.0,
          pitch: 1.0,
        }),
      });

      const data = await res.json();

      if (data.audio_url) {
        setResultAudioUrl(data.audio_url);
      } else {
        setError(data.detail || "生成失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto py-12 px-6">
      <h1 className="text-2xl font-bold mb-2">语音克隆引擎</h1>
      <p className="text-zinc-400 mb-8 text-sm">
        上传 5-10 秒参考音频，提取音色特征，生成专属配音
      </p>

      {/* 模式切换 */}
      <div className="flex gap-3 mb-8">
        <button
          onClick={() => setMode("clone")}
          className={`px-4 py-2 rounded-lg transition-colors ${
            mode === "clone" ? "bg-amber-500 text-black" : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
          }`}
        >
          声音克隆
        </button>
        <button
          onClick={() => setMode("tts")}
          className={`px-4 py-2 rounded-lg transition-colors ${
            mode === "tts" ? "bg-amber-500 text-black" : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
          }`}
        >
          文本转语音
        </button>
      </div>

      {/* 声音克隆模式 */}
      {mode === "clone" && (
        <form onSubmit={handleCloneVoice} className="space-y-6">
          {/* 参考音频上传 */}
          <div>
            <label className="block text-sm text-zinc-400 mb-2">
              参考音频 *
            </label>
            <div className="space-y-3">
              <label className="block w-full h-32 border-2 border-dashed border-zinc-700 rounded-lg hover:border-amber-500 transition-colors cursor-pointer flex items-center justify-center">
                <input
                  type="file"
                  accept="audio/*"
                  onChange={handleReferenceAudioUpload}
                  className="hidden"
                />
                <div className="text-center text-zinc-500">
                  <p className="text-2xl mb-2">🎙️</p>
                  <p className="text-sm">点击上传参考音频</p>
                  <p className="text-xs mt-1">5-10 秒，清晰的人声录音</p>
                </div>
              </label>
              {referenceAudio && (
                <div className="p-3 rounded-lg bg-zinc-900 border border-zinc-700">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">🔊</span>
                    <span className="text-sm text-zinc-400 flex-1">
                      已上传参考音频
                    </span>
                    <button
                      type="button"
                      onClick={() => setReferenceAudio(null)}
                      className="w-6 h-6 bg-red-500 rounded-full text-white text-sm flex items-center justify-center hover:bg-red-600"
                    >
                      ✕
                    </button>
                  </div>
                  <audio src={referenceAudio} controls className="w-full mt-2" />
                </div>
              )}
            </div>
          </div>

          {/* 文案输入 */}
          <div>
            <label className="block text-sm text-zinc-400 mb-2">
              要转换的文案 *
            </label>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="输入要转换为语音的文案..."
              className="w-full h-32 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none"
              required
            />
          </div>

          {/* 提交按钮 */}
          <button
            type="submit"
            disabled={loading || !referenceAudio || !text}
            className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? "处理中..." : "克隆声音并生成配音"}
          </button>
        </form>
      )}

      {/* 文本转语音模式 */}
      {mode === "tts" && (
        <form onSubmit={handleTTS} className="space-y-6">
          {/* 音色选择 */}
          <div>
            <label className="block text-sm text-zinc-400 mb-2">
              选择音色
            </label>
            <div className="grid grid-cols-2 gap-3">
              {voicePresets.map((voice) => (
                <button
                  key={voice.id}
                  type="button"
                  onClick={() => setSelectedVoice(voice.id)}
                  className={`p-4 rounded-lg border text-left transition-colors ${
                    selectedVoice === voice.id
                      ? "border-amber-500 bg-amber-500/10"
                      : "border-zinc-700 bg-zinc-900 hover:border-zinc-500"
                  }`}
                >
                  <div className="font-medium text-zinc-200">{voice.name}</div>
                  <div className="text-xs text-zinc-500 mt-1">
                    {voice.gender} · {voice.style}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* 文案输入 */}
          <div>
            <label className="block text-sm text-zinc-400 mb-2">
              文案 *
            </label>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="输入要转换为语音的文案..."
              className="w-full h-32 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none"
              required
            />
          </div>

          {/* 提交按钮 */}
          <button
            type="submit"
            disabled={loading || !text}
            className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? "生成中..." : "生成配音"}
          </button>
        </form>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="mt-6 p-4 rounded-lg bg-red-900/20 border border-red-700">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}

      {/* 结果音频 */}
      {resultAudioUrl && (
        <div className="mt-6 p-6 rounded-lg bg-zinc-900 border border-zinc-700">
          <p className="text-sm text-zinc-400 mb-3">生成的音频</p>
          <audio src={resultAudioUrl} controls className="w-full" />
          <div className="flex items-center justify-between mt-4">
            <span className="text-sm text-zinc-400">
              {clonedVoiceId ? `克隆音色：${clonedVoiceId}` : "TTS 生成"}
            </span>
            <a
              href={resultAudioUrl}
              download
              target="_blank"
              className="px-4 py-2 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors text-sm"
            >
              下载音频
            </a>
          </div>
        </div>
      )}

      {/* 使用提示 */}
      <div className="mt-12 p-6 rounded-lg bg-zinc-900/50 border border-zinc-800">
        <h3 className="text-sm font-semibold text-zinc-300 mb-3">使用提示</h3>
        <ul className="space-y-2 text-sm text-zinc-500">
          <li>• 参考音频建议 5-10 秒，清晰的人声录音</li>
          <li>• 避免背景音乐和环境噪音</li>
          <li>• 克隆的声音可用于数字人驱动</li>
          <li>• 支持中文、英文、日文、韩文等多种语言</li>
        </ul>
      </div>
    </div>
  );
}
