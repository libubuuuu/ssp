"use client";

import { useState, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera, Environment, Html } from "@react-three/drei";
import { Suspense, useEffect } from "react";
import * as THREE from "three";

// 服装模型组件
function ClothingModel({ url, position = [0, 0, 0] }: { url: string; position?: [number, number, number] }) {
  const groupRef = useRef<THREE.Group>(null);

  useFrame((state) => {
    if (groupRef.current) {
      groupRef.current.rotation.y += 0.005;
    }
  });

  return (
    <group ref={groupRef} position={position}>
      {/* 这里会动态加载服装 GLB 模型 */}
      <mesh>
        <boxGeometry args={[1, 1.8, 0.5]} />
        <meshStandardMaterial color="#6366f1" metalness={0.5} roughness={0.5} />
      </mesh>
    </group>
  );
}

// 人体模型组件
function BodyModel({ url, position = [0, 0, 0] }: { url: string; position?: [number, number, number] }) {
  const groupRef = useRef<THREE.Group>(null);

  return (
    <group ref={groupRef} position={position}>
      {/* 简化的人体模型 - 实际会从 URL 加载 GLB */}
      {/* 头部 */}
      <mesh position={[0, 1.6, 0]}>
        <sphereGeometry args={[0.25, 32, 32]} />
        <meshStandardMaterial color="#f0d5be" metalness={0} roughness={0.8} />
      </mesh>
      {/* 身体 */}
      <mesh position={[0, 0.9, 0]}>
        <cylinderGeometry args={[0.3, 0.35, 0.7, 32]} />
        <meshStandardMaterial color="#f0d5be" metalness={0} roughness={0.8} />
      </mesh>
      {/* 躯干 */}
      <mesh position={[0, 0.4, 0]}>
        <cylinderGeometry args={[0.35, 0.3, 0.6, 32]} />
        <meshStandardMaterial color="#1f2937" metalness={0} roughness={0.8} />
      </mesh>
      {/* 左腿 */}
      <mesh position={[-0.2, -0.3, 0]}>
        <cylinderGeometry args={[0.12, 0.1, 0.7, 32]} />
        <meshStandardMaterial color="#1f2937" metalness={0} roughness={0.8} />
      </mesh>
      {/* 右腿 */}
      <mesh position={[0.2, -0.3, 0]}>
        <cylinderGeometry args={[0.12, 0.1, 0.7, 32]} />
        <meshStandardMaterial color="#1f2937" metalness={0} roughness={0.8} />
      </mesh>
    </group>
  );
}

// 试穿场景组件
function TryOnScene({ bodyModelUrl, clothingModelUrl }: { bodyModelUrl: string; clothingModelUrl: string }) {
  return (
    <>
      <PerspectiveCamera makeDefault position={[0, 0.5, 4]} fov={50} />
      <ambientLight intensity={0.6} />
      <directionalLight position={[5, 5, 5]} intensity={1} castShadow />
      <directionalLight position={[-5, 3, -5]} intensity={0.5} />

      <Suspense fallback={null}>
        <BodyModel url={bodyModelUrl} />
        <ClothingModel url={clothingModelUrl} />
      </Suspense>

      <OrbitControls
        makeDefault
        minPolarAngle={0}
        maxPolarAngle={Math.PI / 1.5}
        minDistance={2}
        maxDistance={8}
        target={[0, 0.5, 0]}
      />
    </>
  );
}

// 服装商品卡片组件
interface ClothingItem {
  id: string;
  name: string;
  price: number;
  model3DUrl: string;
  thumbnail: string;
  category: string;
}

const sampleClothingItems: ClothingItem[] = [
  {
    id: "1",
    name: "经典白 T 恤",
    price: 99,
    model3DUrl: "",
    thumbnail: "👕",
    category: "上衣",
  },
  {
    id: "2",
    name: "修身牛仔裤",
    price: 299,
    model3DUrl: "",
    thumbnail: "👖",
    category: "裤子",
  },
  {
    id: "3",
    name: "连衣长裙",
    price: 399,
    model3DUrl: "",
    thumbnail: "👗",
    category: "裙子",
  },
  {
    id: "4",
    name: "休闲外套",
    price: 599,
    model3DUrl: "",
    thumbnail: "🧥",
    category: "外套",
  },
];

export default function TryOnPage() {
  const [bodyModel, setBodyModel] = useState<string | null>(null);
  const [selectedClothing, setSelectedClothing] = useState<ClothingItem | null>(null);
  const [activeCategory, setActiveCategory] = useState<string>("全部");

  const categories = ["全部", "上衣", "裤子", "裙子", "外套"];

  const filteredItems = activeCategory === "全部"
    ? sampleClothingItems
    : sampleClothingItems.filter(item => item.category === activeCategory);

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* 顶部导航 */}
      <div className="border-b border-zinc-800 px-6 py-4">
        <h1 className="text-2xl font-bold">3D 试穿</h1>
        <p className="text-zinc-400 text-sm mt-1">
          选择服装，在您的 3D 模型上试穿预览
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 p-6">
        {/* 左侧：3D 预览区域 */}
        <div className="lg:col-span-2">
          <div className="w-full h-[600px] rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
            {bodyModel || selectedClothing ? (
              <Canvas shadows>
                <TryOnScene bodyModelUrl={bodyModel || ""} clothingModelUrl={selectedClothing?.model3DUrl || ""} />
              </Canvas>
            ) : (
              <div className="w-full h-full flex items-center justify-center">
                <div className="text-center text-zinc-500">
                  <div className="text-5xl mb-4">👤</div>
                  <p>请先上传您的 3D 模型</p>
                  <a href="/canvas" className="text-amber-400 hover:underline mt-2 inline-block">
                    前往 3D 画布 →
                  </a>
                </div>
              </div>
            )}
          </div>

          {/* 控制按钮 */}
          <div className="flex gap-4 mt-4">
            <button className="flex-1 py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 transition-colors">
              🔄 旋转查看
            </button>
            <button className="flex-1 py-3 rounded-lg bg-zinc-800 text-white font-medium hover:bg-zinc-700 transition-colors">
              📷 截图保存
            </button>
            {selectedClothing && (
              <button className="flex-1 py-3 rounded-lg bg-green-600 text-white font-medium hover:bg-green-500 transition-colors">
                🛒 加入购物车
              </button>
            )}
          </div>
        </div>

        {/* 右侧：服装选择区域 */}
        <div className="space-y-4">
          {/* 分类筛选 */}
          <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50">
            <h2 className="text-lg font-semibold mb-3 text-amber-400">服装分类</h2>
            <div className="flex flex-wrap gap-2">
              {categories.map((cat) => (
                <button
                  key={cat}
                  onClick={() => setActiveCategory(cat)}
                  className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                    activeCategory === cat
                      ? "bg-amber-500 text-black"
                      : "bg-zinc-800 text-zinc-400 hover:text-white"
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          {/* 服装列表 */}
          <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50 max-h-[500px] overflow-y-auto">
            <h2 className="text-lg font-semibold mb-3 text-amber-400">服装列表</h2>
            <div className="space-y-3">
              {filteredItems.map((item) => (
                <div
                  key={item.id}
                  onClick={() => setSelectedClothing(item)}
                  className={`p-3 rounded-lg border cursor-pointer transition-all ${
                    selectedClothing?.id === item.id
                      ? "border-amber-500 bg-amber-500/10"
                      : "border-zinc-700 bg-zinc-800/50 hover:border-zinc-600"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <span className="text-3xl">{item.thumbnail}</span>
                    <div className="flex-1">
                      <p className="font-medium text-sm">{item.name}</p>
                      <p className="text-zinc-400 text-xs">¥{item.price}</p>
                    </div>
                    {selectedClothing?.id === item.id && (
                      <span className="text-amber-400">✓</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
