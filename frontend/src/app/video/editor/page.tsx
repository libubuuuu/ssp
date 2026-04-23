"use client";

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface ShotCard {
  index: number;
  start_time: number;
  end_time: number;
  description: string;
  camera_movement: string;
  prompt: string;
  thumbnail_url?: string | null;
}

export default function VideoEditorPage() {
  const [videoUrl, setVideoUrl] = useState("");
  const [parsedShots, setParsedShots] = useState<ShotCard[]>([]);
  const [selectedShotIndex, setSelectedShotIndex] = useState<number | null>(null);
  const [editedDescription, setEditedDescription] = useState("");
  const [editedPrompt, setEditedPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [translateTargetLang, setTranslateTargetLang] = useState("en");
  const [translatedText, setTranslatedText] = useState("");

  // 解析视频
  const handleParseVideo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!videoUrl) return;

    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/api/video/editor/parse`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_url: videoUrl }),
      });

      const data = await res.json();

      if (data.shots) {
        setParsedShots(data.shots);
      } else {
        setError(data.detail || "解析失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  };

  // 选择分镜
  const handleSelectShot = (shot: ShotCard) => {
    setSelectedShotIndex(shot.index);
    setEditedDescription(shot.description);
    setEditedPrompt(shot.prompt);
  };

  // 更新分镜
  const handleUpdateShot = async () => {
    if (selectedShotIndex === null) return;

    try {
      const res = await fetch(
        `${API_BASE}/api/video/editor/shot/${selectedShotIndex}/update`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            shot_index: selectedShotIndex,
            description: editedDescription,
            prompt: editedPrompt,
          }),
        }
      );

      const data = await res.json();

      if (data.message) {
        // 更新本地状态
        setParsedShots((prev) =>
          prev.map((shot) =>
            shot.index === selectedShotIndex
              ? { ...shot, description: editedDescription, prompt: editedPrompt }
              : shot
          )
        );
        alert("分镜已更新");
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "更新失败");
    }
  };

  // 重新生成视频片段
  const handleRegenerateShot = async () => {
    if (selectedShotIndex === null) return;

    setLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/video/editor/shot/${selectedShotIndex}/regenerate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            shot_index: selectedShotIndex,
            new_prompt: editedPrompt,
          }),
        }
      );

      const data = await res.json();

      if (data.task_id) {
        alert("重新生成任务已提交，请稍后查看结果");
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "生成失败");
    } finally {
      setLoading(false);
    }
  };

  // 翻译脚本
  const handleTranslate = async () => {
    if (!editedDescription) return;

    try {
      const res = await fetch(`${API_BASE}/api/video/editor/translate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: editedDescription,
          target_lang: translateTargetLang,
        }),
      });

      const data = await res.json();

      if (data.translated) {
        setTranslatedText(data.translated);
      }
    } catch (err) {
      alert("翻译失败");
    }
  };

  // 合成视频
  const handleComposeVideo = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/video/editor/compose`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shots: parsedShots }),
      });

      const data = await res.json();

      if (data.task_id) {
        alert("视频合成任务已提交，任务 ID: " + data.task_id);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "合成失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col">
      {/* 顶部：视频解析 */}
      <div className="p-4 border-b border-zinc-800">
        <h1 className="text-xl font-bold mb-4">Web 端视频剪辑台</h1>
        <form onSubmit={handleParseVideo} className="flex gap-3">
          <input
            type="url"
            value={videoUrl}
            onChange={(e) => setVideoUrl(e.target.value)}
            placeholder="输入视频链接进行分析..."
            className="flex-1 px-4 py-2 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none"
          />
          <button
            type="submit"
            disabled={loading || !videoUrl}
            className="px-6 py-2 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50"
          >
            {loading ? "解析中..." : "解析视频"}
          </button>
        </form>
      </div>

      {/* 主体：分镜卡片 + 编辑区 */}
      <div className="flex-1 flex overflow-hidden">
        {/* 左侧：分镜卡片列表 */}
        <div className="w-80 border-r border-zinc-800 overflow-y-auto p-4">
          <h2 className="text-sm font-semibold text-zinc-400 mb-3">分镜列表</h2>
          {parsedShots.length === 0 ? (
            <p className="text-zinc-600 text-sm">请先解析视频</p>
          ) : (
            <div className="space-y-2">
              {parsedShots.map((shot) => (
                <div
                  key={shot.index}
                  onClick={() => handleSelectShot(shot)}
                  className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                    selectedShotIndex === shot.index
                      ? "border-amber-500 bg-amber-500/10"
                      : "border-zinc-700 bg-zinc-900 hover:border-zinc-500"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-amber-400">
                      镜头 {shot.index + 1}
                    </span>
                    <span className="text-xs text-zinc-500">
                      {shot.start_time.toFixed(1)}s - {shot.end_time.toFixed(1)}s
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400 line-clamp-2">
                    {shot.description}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右侧：编辑区 */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {selectedShotIndex === null ? (
            <div className="flex-1 flex items-center justify-center text-zinc-500">
              请选择一个分镜进行编辑
            </div>
          ) : (
            <div className="flex-1 p-6 overflow-y-auto">
              <h2 className="text-lg font-semibold mb-4">
                编辑镜头 {selectedShotIndex + 1}
              </h2>

              <div className="space-y-6">
                {/* 画面描述 */}
                <div>
                  <label className="block text-sm text-zinc-400 mb-2">
                    画面描述
                  </label>
                  <textarea
                    value={editedDescription}
                    onChange={(e) => setEditedDescription(e.target.value)}
                    className="w-full h-24 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none"
                  />
                </div>

                {/* 生成提示词 */}
                <div>
                  <label className="block text-sm text-zinc-400 mb-2">
                    生成提示词 (英文)
                  </label>
                  <textarea
                    value={editedPrompt}
                    onChange={(e) => setEditedPrompt(e.target.value)}
                    className="w-full h-24 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none resize-none font-mono text-sm"
                  />
                </div>

                {/* 翻译功能 */}
                <div>
                  <label className="block text-sm text-zinc-400 mb-2">
                    多语言翻译
                  </label>
                  <div className="flex gap-3">
                    <select
                      value={translateTargetLang}
                      onChange={(e) => setTranslateTargetLang(e.target.value)}
                      className="px-4 py-2 rounded-lg bg-zinc-900 border border-zinc-700 focus:border-amber-500 outline-none"
                    >
                      <option value="en">英语</option>
                      <option value="zh">中文</option>
                      <option value="ja">日语</option>
                      <option value="ko">韩语</option>
                      <option value="fr">法语</option>
                      <option value="de">德语</option>
                      <option value="es">西班牙语</option>
                    </select>
                    <button
                      onClick={handleTranslate}
                      className="px-4 py-2 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
                    >
                      翻译
                    </button>
                  </div>
                  {translatedText && (
                    <div className="mt-3 p-3 rounded-lg bg-zinc-900 border border-zinc-700">
                      <p className="text-sm text-zinc-400">{translatedText}</p>
                    </div>
                  )}
                </div>

                {/* 操作按钮 */}
                <div className="flex gap-3">
                  <button
                    onClick={handleUpdateShot}
                    className="px-4 py-2 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400"
                  >
                    保存修改
                  </button>
                  <button
                    onClick={handleRegenerateShot}
                    disabled={loading}
                    className="px-4 py-2 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
                  >
                    {loading ? "生成中..." : "重新生成此段"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 底部：时间轴和合成按钮 */}
      <div className="h-48 border-t border-zinc-800 p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-zinc-400">时间轴</h2>
          <button
            onClick={handleComposeVideo}
            disabled={loading || parsedShots.length === 0}
            className="px-6 py-2 rounded-lg bg-green-600 text-white font-medium hover:bg-green-500 disabled:opacity-50"
          >
            {loading ? "合成中..." : "合成视频"}
          </button>
        </div>

        {/* 简单时间轴 */}
        <div className="h-20 bg-zinc-900 rounded-lg border border-zinc-700 p-2 overflow-x-auto">
          {parsedShots.length === 0 ? (
            <div className="h-full flex items-center justify-center text-zinc-600 text-sm">
              暂无分镜
            </div>
          ) : (
            <div className="flex h-full gap-1">
              {parsedShots.map((shot) => (
                <div
                  key={shot.index}
                  onClick={() => handleSelectShot(shot)}
                  className={`flex-shrink-0 h-full rounded border cursor-pointer ${
                    selectedShotIndex === shot.index
                      ? "border-amber-500 bg-amber-500/20"
                      : "border-zinc-600 bg-zinc-800 hover:border-zinc-400"
                  }`}
                  style={{ width: `${(shot.end_time - shot.start_time) * 20}px` }}
                  title={shot.description}
                >
                  <div className="text-xs p-1 truncate h-full overflow-hidden">
                    镜头 {shot.index + 1}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 总时长 */}
        <div className="mt-2 text-xs text-zinc-500">
          总时长：{parsedShots.reduce((acc, shot) => acc + (shot.end_time - shot.start_time), 0).toFixed(1)}s
        </div>
      </div>
    </div>
  );
}
