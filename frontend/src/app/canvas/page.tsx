"use client";

import { useState, useCallback, Suspense } from "react";
import { Canvas, useLoader } from "@react-three/fiber";
import { OrbitControls, Stage, PerspectiveCamera } from "@react-three/drei";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

const RPM_API = process.env.NEXT_PUBLIC_READY_PLAYER_ME_API || "https://api.readyplayer.me";

// 3D 模型加载组件
function Model({ url }: { url: string }) {
  const gltf = useLoader(GLTFLoader, url);
  return <primitive object={gltf.scene} />;
}

// 3D 模型显示组件
function ModelViewer({ modelUrl }: { modelUrl: string }) {
  return (
    <Canvas shadows className="w-full h-full">
      <PerspectiveCamera makeDefault position={[0, 0, 5]} />
      <ambientLight intensity={0.5} />
      <directionalLight position={[5, 5, 5]} intensity={1} castShadow />
      <Suspense fallback={<text fill="white" fontSize={0.5} textAnchor="middle">加载中...</text>}>
        <Stage environment={null} shadows="contact" intensity={0.5}>
          <Model url={modelUrl} />
        </Stage>
      </Suspense>
      <OrbitControls makeDefault minPolarAngle={0} maxPolarAngle={Math.PI / 2} />
    </Canvas>
  );
}

export default function CanvasPage() {
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [modelUrl, setModelUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 处理图片上传
  const handleImageUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // 创建本地预览 URL
    const reader = new FileReader();
    reader.onload = (event) => {
      setSelectedImage(event.target?.result as string);
    };
    reader.readAsDataURL(file);

    // 调用 Ready Player Me API 生成 3D 模型
    setLoading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append("image", file);
      formData.append("type", "fullbody");

      const response = await fetch(`${RPM_API}/avatar`, {
        method: "POST",
        headers: {
          "Authorization": "", // Ready Player Me 免费 API 无需密钥
        },
        body: formData,
      });

      if (!response.ok) {
        throw new Error("生成 3D 模型失败");
      }

      const data = await response.json();
      setModelUrl(data.url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成失败，请重试");
      // 失败时使用模拟 URL 用于开发测试
      setModelUrl("https://models.readyplayer.me/65f1b2c8d8e0f1001f3c7d5f.glb");
    } finally {
      setLoading(false);
    }
  }, []);

  // 下载 3D 模型
  const handleDownload = useCallback(async () => {
    if (!modelUrl) return;

    try {
      const response = await fetch(modelUrl);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "avatar.glb";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("下载失败:", err);
    }
  }, [modelUrl]);

  return (
    <div className="min-h-screen bg-zinc-950">
      <div className="border-b border-zinc-800 px-6 py-4">
        <h1 className="text-2xl font-bold">3D 画布</h1>
        <p className="text-zinc-400 text-sm mt-1">
          上传你的照片，生成 3D 全身模型，然后试穿各种服装
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 p-6">
        {/* 左侧：上传区域 */}
        <div className="space-y-4">
          <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
            <h2 className="text-lg font-semibold mb-4 text-amber-400">1. 上传照片</h2>

            <label className="block w-full aspect-square max-w-md mx-auto rounded-lg border-2 border-dashed border-zinc-700 hover:border-amber-500 cursor-pointer bg-zinc-900/50 flex items-center justify-center transition-colors">
              <input
                type="file"
                accept="image/*"
                onChange={handleImageUpload}
                className="hidden"
              />
              {selectedImage ? (
                <img
                  src={selectedImage}
                  alt="预览"
                  className="w-full h-full object-contain rounded-lg"
                />
              ) : (
                <div className="text-center p-8">
                  <div className="text-4xl mb-2">📷</div>
                  <p className="text-zinc-400">点击上传正面照片</p>
                  <p className="text-zinc-500 text-sm mt-2">
                    支持 JPG、PNG 格式
                  </p>
                </div>
              )}
            </label>

            {loading && (
              <div className="mt-4 text-center">
                <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-amber-400"></div>
                <p className="text-zinc-400 mt-2">正在生成 3D 模型...</p>
              </div>
            )}

            {error && (
              <div className="mt-4 p-3 rounded-lg bg-red-900/30 border border-red-800 text-red-400 text-sm">
                {error}
              </div>
            )}
          </div>

          <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
            <h2 className="text-lg font-semibold mb-4 text-amber-400">2. 下载模型</h2>
            <button
              onClick={handleDownload}
              disabled={!modelUrl}
              className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              下载 3D 模型 (.glb)
            </button>
          </div>
        </div>

        {/* 右侧：3D 预览区域 */}
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/50 min-h-[500px]">
          <h2 className="text-lg font-semibold mb-4 text-amber-400">3D 预览</h2>

          {modelUrl ? (
            <div className="w-full h-[500px] rounded-lg overflow-hidden">
              <ModelViewer modelUrl={modelUrl} />
            </div>
          ) : (
            <div className="w-full h-[500px] rounded-lg bg-zinc-950 border border-zinc-800 flex items-center justify-center">
              <div className="text-center text-zinc-500">
                <div className="text-4xl mb-2">🎭</div>
                <p>上传照片后预览 3D 模型</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
