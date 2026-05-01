"use client";
import { useState, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";
import { compressImage } from "@/lib/utils/imageCompress";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

// ============== Types ==============

interface Audit {
  is_valid: boolean;
  category: string;
  color: string;
  material: string;
  quality_score: number;
  issues: string[];
  violations: string[];
  target_audience: string;
}

interface Scene {
  id: number;
  time_range: string;
  purpose: string;
  shot_language: string;
  content: string;
  visual_prompt: string;
  speech: string;
}

interface Script {
  overall_setting: string;
  model_description: string;
  scenes: Scene[];
}

type Step = 1 | 2 | 3 | 4;

// ============== Page ==============

export default function AdVideoPage() {
  const [step, setStep] = useState<Step>(1);

  // Step 1: 上传 + 时长选择
  const [productFile, setProductFile] = useState<File | null>(null);
  const [productPreview, setProductPreview] = useState("");
  // P34:产品反面/侧面图(可选),锁住产品反面材质/logo/标签
  const [productBackFile, setProductBackFile] = useState<File | null>(null);
  const [productBackPreview, setProductBackPreview] = useState("");
  const [bgFile, setBgFile] = useState<File | null>(null);
  const [bgPreview, setBgPreview] = useState("");
  // P32:用户自定义视频总时长(5-300s),analyze 时透传给 VLM 出 N 段脚本
  const [duration, setDuration] = useState(15);

  // Step 2: 审核 + 脚本(从 /analyze 返回)
  const [audit, setAudit] = useState<Audit | null>(null);
  const [script, setScript] = useState<Script | null>(null);

  // Step 3: 首帧预览
  const [productImageUrl, setProductImageUrl] = useState(""); // fal storage URL
  const [productBackImageUrl, setProductBackImageUrl] = useState(""); // P34
  const [bgImageUrl, setBgImageUrl] = useState("");
  const [previewImageUrl, setPreviewImageUrl] = useState("");
  // P35: N 张分镜首帧(每段一张),后续 generate 直接复用,jobs.py 不再重复合
  const [sceneImageUrls, setSceneImageUrls] = useState<string[]>([]);

  // Step 4: 视频
  const [videoUrl, setVideoUrl] = useState("");
  const [jobProgress, setJobProgress] = useState("");

  // 通用
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("");
  const [err, setErr] = useState("");

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const token = () => localStorage.getItem("token") || "";

  // ============== File handling ==============

  const onProductFile = (f: File) => {
    setProductFile(f);
    setProductPreview(URL.createObjectURL(f));
  };
  const onProductBackFile = (f: File) => {
    setProductBackFile(f);
    setProductBackPreview(URL.createObjectURL(f));
  };
  const onBgFile = (f: File) => {
    setBgFile(f);
    setBgPreview(URL.createObjectURL(f));
  };

  // ============== API calls ==============

  const callAnalyze = async () => {
    if (!productFile) {
      setErr("请先上传产品图");
      return;
    }
    setErr("");
    setLoading(true);
    setLoadingMsg("正在压缩图片...");

    try {
      // 七十三续:前端压缩,5MB → 500KB
      const compressed = await compressImage(productFile);
      setLoadingMsg("小九正在审核图片并生成脚本...");
      const fd = new FormData();
      fd.append("file", compressed);
      // P34: 反面图(可选)同时上传
      if (productBackFile) {
        const compressedBack = await compressImage(productBackFile);
        fd.append("back_file", compressedBack);
      }
      const r = await fetch(`${API_BASE}/api/ad-video/analyze?total_duration=${duration}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      const d = await r.json();
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : (d.detail?.message || "审核失败"));
      if (typeof d.cost === "number" && d.cost > 0) adjustLocalUserCredits(-d.cost);

      setAudit(d.audit);
      setScript(d.script);
      // /analyze 内部已上传到 fal storage,直接复用 URL,后面 /preview 不用再传
      if (d.product_image_url) {
        setProductImageUrl(d.product_image_url);
      }
      // P34: 反面图(若上传了)
      if (d.product_back_image_url) {
        setProductBackImageUrl(d.product_back_image_url);
      }
      setStep(2);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  const uploadImage = async (file: File): Promise<string> => {
    // 七十三续:前端压缩
    const compressed = await compressImage(file);
    const fd = new FormData();
    fd.append("file", compressed);
    const r = await fetch(`${API_BASE}/api/ad-video/upload/image`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token()}` },
      body: fd,
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "图片上传失败");
    return d.url;
  };

  const callPreview = async () => {
    if (!productFile || !script) return;
    setErr("");
    setLoading(true);
    setLoadingMsg("正在合成首帧预览图...");

    try {
      // 先把产品图上传到 fal storage(只传一次)
      let pUrl = productImageUrl;
      if (!pUrl) {
        setLoadingMsg("上传产品图...");
        pUrl = await uploadImage(productFile);
        setProductImageUrl(pUrl);
      }

      // 背景图(可选)
      let bUrl = bgImageUrl;
      if (bgFile && !bUrl) {
        setLoadingMsg("上传背景图...");
        bUrl = await uploadImage(bgFile);
        setBgImageUrl(bUrl);
      }

      setLoadingMsg(`Seedream 合成 ${script.scenes.length} 张分镜首帧...`);
      const r = await fetch(`${API_BASE}/api/ad-video/preview`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token()}`,
        },
        body: JSON.stringify({
          product_image_url: pUrl,
          product_back_image_url: productBackImageUrl || null,
          background_image_url: bUrl || null,
          script: script,  // P35: 整个 script,后端循环出 N 张
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "首帧合成失败");
      if (typeof d.cost === "number" && d.cost > 0) adjustLocalUserCredits(-d.cost);

      // P35: 接收 N 张分镜首帧;image_url 是兼容字段(第 1 张)
      setPreviewImageUrl(d.image_url);
      setSceneImageUrls(d.scene_image_urls || (d.image_url ? [d.image_url] : []));
      setStep(3);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  const callGenerate = async () => {
    if (!previewImageUrl || !script) return;
    setErr("");
    setLoading(true);
    setLoadingMsg("提交视频生成任务...");

    try {
      const r = await fetch(`${API_BASE}/api/ad-video/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token()}`,
        },
        body: JSON.stringify({
          image_url: previewImageUrl,
          scene_image_urls: sceneImageUrls,  // P35
          // P36: 透传产品+背景图给后端 reference-to-video 用,跳过 Seedream 合成
          product_image_url: productImageUrl || null,
          product_back_image_url: productBackImageUrl || null,
          background_image_url: bgImageUrl || null,
          script,
          duration,
          aspect_ratio: "9:16",
          resolution: "720p",
          enable_audio: true,
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "提交失败");
      if (typeof d.cost === "number" && d.cost > 0) adjustLocalUserCredits(-d.cost);

      setStep(4);
      startPolling(d.job_id);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  // ============== 轮询 jobs ==============

  const startPolling = (jid: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    let sec = 0;
    pollRef.current = setInterval(async () => {
      sec += 5;
      try {
        const r = await fetch(`${API_BASE}/api/jobs/${jid}`, {
          headers: { Authorization: `Bearer ${token()}` },
        });
        const j = await r.json();
        if (j.status === "completed" && j.result?.video_url) {
          setVideoUrl(j.result.video_url);
          setJobProgress("");
          if (pollRef.current) clearInterval(pollRef.current);
        } else if (j.status === "failed") {
          setErr(j.error || "视频生成失败");
          setJobProgress("");
          if (pollRef.current) clearInterval(pollRef.current);
        } else {
          const m = Math.floor(sec / 60);
          const s = sec % 60;
          setJobProgress(`生成中 ${m}分${s}秒...`);
        }
      } catch {}
    }, 5000);
  };

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // ============== Scene 编辑 ==============

  const updateScene = (idx: number, key: keyof Scene, value: string) => {
    if (!script) return;
    const newScenes = script.scenes.map((s, i) => (i === idx ? { ...s, [key]: value } : s));
    setScript({ ...script, scenes: newScenes });
  };

  const regenScene = async (idx: number) => {
    if (!script) return;
    const instruction = window.prompt("请输入修改指令(中文):", "更激情一些");
    if (!instruction) return;

    setLoading(true);
    setLoadingMsg(`重新生成镜头 ${idx + 1}...`);
    try {
      const r = await fetch(`${API_BASE}/api/ad-video/scene/regenerate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token()}`,
        },
        body: JSON.stringify({
          original_scene: script.scenes[idx],
          instruction,
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "重新生成失败");
      if (typeof d.cost === "number" && d.cost > 0) adjustLocalUserCredits(-d.cost);

      const newScenes = [...script.scenes];
      newScenes[idx] = d.scene;
      setScript({ ...script, scenes: newScenes });
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setStep(1);
    setProductFile(null);
    setProductPreview("");
    setBgFile(null);
    setBgPreview("");
    setAudit(null);
    setScript(null);
    setProductImageUrl("");
    setBgImageUrl("");
    setPreviewImageUrl("");
    setVideoUrl("");
    setJobProgress("");
    setErr("");
    if (pollRef.current) clearInterval(pollRef.current);
  };

  // ============== Render ==============

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1100 }}>
        {/* 标题 */}
        <div style={{ marginBottom: "2rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>AI 创作工具</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>
            AI 带货
            <span style={{ fontStyle: "italic" }}> 视频</span>
          </h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            上传白底产品图 · 小九自动审核与撰稿 · Seedance 2.0 生成口播视频
          </div>
        </div>

        {/* 步骤指示器 */}
        <Steps current={step} />

        {/* 错误 */}
        {err && (
          <div style={{ background: "#fff3f3", border: "1px solid #fcc", color: "#c33", padding: "0.8rem 1rem", borderRadius: 10, marginBottom: "1rem", fontSize: "0.9rem" }}>
            {err}
          </div>
        )}

        {/* Step 1: 上传 */}
        {step === 1 && (
          <Card title="第一步:上传产品图" desc="建议白底图、4:5 或 1:1、主体居中、光线均匀。反面图能帮 AI 锁住材质/logo/标签细节,合成更真实">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
              <UploadBox
                label="产品正面(必填)"
                preview={productPreview}
                onFile={onProductFile}
                required
              />
              <UploadBox
                label="产品反面/侧面(可选)"
                preview={productBackPreview}
                onFile={onProductBackFile}
                hint="材质 / logo / 反面图案"
              />
              <UploadBox
                label="背景图(可选)"
                preview={bgPreview}
                onFile={onBgFile}
                hint="不传 AI 自动生成"
              />
            </div>
            <div style={{ marginTop: 20 }}>
              <label style={{ display: "block", fontSize: "0.9rem", color: "#444", marginBottom: 8, fontWeight: 500 }}>
                视频总时长
              </label>
              <select
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
                style={{
                  width: "100%",
                  padding: "0.7rem 0.9rem",
                  border: "1px solid #ddd",
                  borderRadius: 8,
                  fontSize: "0.95rem",
                  background: "#fff",
                  cursor: "pointer",
                }}
              >
                <option value={5}>5 秒(单镜)</option>
                <option value={10}>10 秒(单镜)</option>
                <option value={15}>15 秒(单镜)</option>
                <option value={30}>30 秒(3 段拼接)</option>
                <option value={60}>60 秒(6 段拼接)</option>
                <option value={120}>120 秒(12 段拼接)</option>
                <option value={180}>180 秒(18 段拼接)</option>
                <option value={300}>300 秒(30 段拼接,5 分钟)</option>
              </select>
              <div style={{ fontSize: "0.8rem", color: "#888", marginTop: 6 }}>
                超过 15 秒会自动拆段并发生成,每段独立首帧 + 独立视频,最后拼接
              </div>
            </div>
            <PrimaryButton onClick={callAnalyze} disabled={!productFile} marginTop>
              开始 AI 审核(消耗 1 积分) →
            </PrimaryButton>
          </Card>
        )}

        {/* Step 2: 审核 + 脚本 */}
        {step === 2 && audit && script && (
          <>
            <Card title="审核通过" desc="小九已分析图片,以下为生成的分镜脚本(可编辑)">
              <AuditGrid audit={audit} />
            </Card>

            <Card title="分镜脚本" desc={`${script.scenes.length} 个分镜 · 共 ${duration} 秒 · 可逐字编辑或点'重新生成'让 AI 改写`}>
              <FieldBlock label="整体设定">
                <textarea
                  value={script.overall_setting}
                  onChange={(e) => setScript({ ...script, overall_setting: e.target.value })}
                  style={textareaStyle}
                  rows={2}
                />
              </FieldBlock>
              <FieldBlock label="模特描述(英文,给视频模型)">
                <textarea
                  value={script.model_description}
                  onChange={(e) => setScript({ ...script, model_description: e.target.value })}
                  style={textareaStyle}
                  rows={2}
                />
              </FieldBlock>

              {script.scenes.map((sc, idx) => (
                <SceneCard
                  key={idx}
                  scene={sc}
                  onChange={(key, value) => updateScene(idx, key, value)}
                  onRegen={() => regenScene(idx)}
                />
              ))}
            </Card>

            <ActionRow>
              <GhostButton onClick={() => setStep(1)}>← 重新上传</GhostButton>
              <PrimaryButton onClick={callPreview}>
                生成首帧预览(消耗 2 积分) →
              </PrimaryButton>
            </ActionRow>
          </>
        )}

        {/* Step 3: 首帧预览 — P35: N 张分镜首帧网格 */}
        {step === 3 && (sceneImageUrls.length > 0 || previewImageUrl) && (
          <>
            <Card
              title={`分镜首帧预览(${sceneImageUrls.length || 1} 张)`}
              desc="每段分镜对应一张首帧,Seedance 用各自首帧生成本段视频。满意点'生成视频',否则点'重新生成首帧'重出"
            >
              {sceneImageUrls.length > 1 ? (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
                  {sceneImageUrls.map((url, idx) => (
                    <div key={idx} style={{ position: "relative" }}>
                      <div style={{ ...smallLabel, marginBottom: 4 }}>
                        段 {idx + 1}{script?.scenes?.[idx]?.time_range ? ` · ${script.scenes[idx].time_range}` : ""}
                      </div>
                      <img
                        src={url}
                        alt={`段 ${idx + 1} 首帧`}
                        style={{
                          width: "100%",
                          aspectRatio: "9 / 16",
                          objectFit: "cover",
                          borderRadius: 10,
                          background: "#fff",
                          border: "1.5px solid #ddd",
                        }}
                      />
                      {script?.scenes?.[idx]?.shot_language && (
                        <div style={{ fontSize: "0.72rem", color: "#666", marginTop: 4, lineHeight: 1.3 }}>
                          {script.scenes[idx].shot_language}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                  <div>
                    <div style={smallLabel}>原始产品图</div>
                    <img src={productPreview} alt="产品图预览" style={{ width: "100%", borderRadius: 12, background: "#fff" }} />
                  </div>
                  <div>
                    <div style={smallLabel}>AI 合成首帧</div>
                    <img src={sceneImageUrls[0] || previewImageUrl} alt="首帧预览" style={{ width: "100%", borderRadius: 12, background: "#fff", border: "2px solid #0d0d0d" }} />
                  </div>
                </div>
              )}
            </Card>

            <ActionRow>
              <GhostButton onClick={() => setStep(2)}>← 修改脚本</GhostButton>
              <SecondaryButton onClick={callPreview}>↻ 重新生成首帧(2 积分)</SecondaryButton>
              <PrimaryButton onClick={callGenerate}>
                生成视频(消耗 30 积分) →
              </PrimaryButton>
            </ActionRow>
          </>
        )}

        {/* Step 4: 视频结果 */}
        {step === 4 && (
          <Card title="视频生成" desc={videoUrl ? "完成!可下载或分享" : jobProgress || "正在排队..."}>
            {!videoUrl && (
              <div style={{ background: "#fff", padding: "3rem", borderRadius: 12, textAlign: "center" }}>
                <div style={{ fontSize: "0.9rem", color: "#666" }}>{jobProgress || "排队中..."}</div>
                <div style={{ fontSize: "0.75rem", color: "#999", marginTop: 8 }}>
                  Seedance 2.0 一般需要 1-3 分钟,可关闭页面去做别的事,任务在后台跑
                </div>
              </div>
            )}
            {videoUrl && (
              <div style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, display: "flex", justifyContent: "center" }}>
                <video src={videoUrl} controls playsInline style={{ maxWidth: 360, width: "100%", aspectRatio: "9/16", borderRadius: 8, background: "#000" }} />
              </div>
            )}
            {videoUrl && (
              <ActionRow>
                <GhostButton onClick={reset}>↻ 制作下一个</GhostButton>
                <a href={videoUrl} download="ad-video.mp4" target="_blank" style={{ ...secondaryButtonStyle, textDecoration: "none", display: "inline-flex", alignItems: "center" }}>
                  ⬇ 下载视频
                </a>
              </ActionRow>
            )}
          </Card>
        )}

        {/* 加载遮罩 */}
        {loading && (
          <div style={{
            position: "fixed", inset: 0, background: "rgba(20,20,20,0.6)", backdropFilter: "blur(8px)",
            zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <div style={{ background: "#fff", padding: "2rem 2.5rem", borderRadius: 16, textAlign: "center", minWidth: 320 }}>
              <div style={{
                width: 36, height: 36, border: "3px solid #eee", borderTopColor: "#0d0d0d",
                borderRadius: "50%", margin: "0 auto 1rem", animation: "adv-spin 0.8s linear infinite",
              }} />
              <div style={{ fontSize: "0.95rem", fontWeight: 500 }}>{loadingMsg}</div>
              <style>{`@keyframes adv-spin { to { transform: rotate(360deg); } }`}</style>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

// ============== Sub-components ==============

function Steps({ current }: { current: Step }) {
  const steps = [
    { n: 1, label: "上传产品图" },
    { n: 2, label: "审核与脚本" },
    { n: 3, label: "预览首帧" },
    { n: 4, label: "生成视频" },
  ];
  return (
    <div style={{ display: "flex", alignItems: "center", marginBottom: "1.5rem", background: "#fff", padding: "1rem 1.2rem", borderRadius: 12 }}>
      {steps.map((s, i) => (
        <div key={s.n} style={{ display: "flex", alignItems: "center", flex: i === steps.length - 1 ? "0" : "1" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, opacity: s.n === current ? 1 : s.n < current ? 0.7 : 0.35 }}>
            <span style={{ fontFamily: "Georgia,serif", fontStyle: "italic", fontSize: "1rem", fontWeight: 700, color: s.n < current ? "#0a0" : "#0d0d0d" }}>
              {String(s.n).padStart(2, "0")}
            </span>
            <span style={{ fontSize: "0.85rem", fontWeight: 500 }}>{s.label}</span>
          </div>
          {i < steps.length - 1 && <div style={{ flex: 1, height: 1, background: "#ddd", margin: "0 12px" }} />}
        </div>
      ))}
    </div>
  );
}

function Card({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <div style={{ background: "#fff", borderRadius: 16, padding: "1.5rem 1.7rem", marginBottom: "1.2rem", border: "1px solid #eee" }}>
      <div style={{ marginBottom: "1.2rem" }}>
        <h2 style={{ fontSize: "1.15rem", fontFamily: "Georgia,serif", fontWeight: 400, margin: 0 }}>{title}</h2>
        {desc && <div style={{ fontSize: "0.82rem", color: "#999", marginTop: 4 }}>{desc}</div>}
      </div>
      {children}
    </div>
  );
}

function UploadBox({ label, preview, onFile, required, hint }: { label: string; preview: string; onFile: (f: File) => void; required?: boolean; hint?: string }) {
  return (
    <div>
      <div style={smallLabel}>{label}{required && <span style={{ color: "#c33" }}> *</span>}</div>
      <label style={{
        display: "block", width: "100%", aspectRatio: "1",
        border: preview ? "1px solid #ddd" : "2px dashed #ccc",
        borderRadius: 12, cursor: "pointer", overflow: "hidden",
        background: preview ? "#fff" : "#fafaf7",
      }}>
        <input
          type="file"
          accept="image/*"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
          }}
        />
        {preview ? (
          <img src={preview} alt="图片预览" style={{ width: "100%", height: "100%", objectFit: "contain" }} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", color: "#999", fontSize: "0.85rem", gap: 8 }}>
            <span style={{ fontSize: "1.5rem" }}>⬆</span>
            <span>点击上传</span>
            {hint && <span style={{ fontSize: "0.7rem", color: "#bbb", textAlign: "center", padding: "0 1rem" }}>{hint}</span>}
          </div>
        )}
      </label>
    </div>
  );
}

function AuditGrid({ audit }: { audit: Audit }) {
  const items = [
    { k: "产品品类", v: audit.category },
    { k: "主要颜色", v: audit.color },
    { k: "材质", v: audit.material },
    { k: "质量评分", v: `${audit.quality_score} / 10` },
    { k: "目标人群", v: audit.target_audience },
  ];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
      {items.map((i) => (
        <div key={i.k} style={{ background: "#faf9f5", borderRadius: 10, padding: "10px 14px" }}>
          <div style={{ fontSize: "0.7rem", color: "#999", marginBottom: 4, letterSpacing: "0.05em" }}>{i.k}</div>
          <div style={{ fontSize: "0.9rem", fontWeight: 500 }}>{i.v || "—"}</div>
        </div>
      ))}
    </div>
  );
}

function FieldBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={smallLabel}>{label}</div>
      {children}
    </div>
  );
}

function SceneCard({ scene, onChange, onRegen }: { scene: Scene; onChange: (key: keyof Scene, value: string) => void; onRegen: () => void }) {
  return (
    <div style={{ background: "#faf9f5", borderRadius: 12, padding: "1.2rem", marginBottom: 12, borderLeft: "3px solid #0d0d0d" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span style={{ fontFamily: "Georgia,serif", fontStyle: "italic", fontSize: "1.6rem", fontWeight: 700 }}>
            {String(scene.id).padStart(2, "0")}
          </span>
          <div>
            <div style={{ fontSize: "0.9rem", fontWeight: 600 }}>{scene.purpose}</div>
            <div style={{ fontSize: "0.7rem", color: "#999" }}>{scene.time_range}</div>
          </div>
        </div>
        <button onClick={onRegen} style={{ background: "transparent", border: "1px solid #ccc", padding: "4px 10px", borderRadius: 8, fontSize: "0.75rem", cursor: "pointer", color: "#666" }}>
          ↻ 重新生成
        </button>
      </div>

      <FieldBlock label="镜头语言">
        <textarea value={scene.shot_language} onChange={(e) => onChange("shot_language", e.target.value)} style={textareaStyle} rows={2} />
      </FieldBlock>
      <FieldBlock label="场景内容">
        <textarea value={scene.content} onChange={(e) => onChange("content", e.target.value)} style={textareaStyle} rows={2} />
      </FieldBlock>
      <FieldBlock label="视觉提示词(英文,给视频模型)">
        <textarea value={scene.visual_prompt} onChange={(e) => onChange("visual_prompt", e.target.value)} style={textareaStyle} rows={3} />
      </FieldBlock>
      <FieldBlock label="说话内容(口播台词)">
        <textarea value={scene.speech} onChange={(e) => onChange("speech", e.target.value)} style={textareaStyle} rows={2} />
      </FieldBlock>
    </div>
  );
}

function ActionRow({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "center", justifyContent: "flex-end", marginBottom: "1.2rem" }}>
      {children}
    </div>
  );
}

function PrimaryButton({ onClick, disabled, children, marginTop }: { onClick: () => void; disabled?: boolean; children: React.ReactNode; marginTop?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "0.8rem 1.6rem",
        background: disabled ? "#ccc" : "#0d0d0d",
        color: "#fff",
        border: "none",
        borderRadius: 10,
        cursor: disabled ? "not-allowed" : "pointer",
        fontSize: "0.9rem",
        fontWeight: 500,
        marginTop: marginTop ? "1.2rem" : 0,
      }}
    >
      {children}
    </button>
  );
}

const secondaryButtonStyle: React.CSSProperties = {
  padding: "0.8rem 1.4rem",
  background: "#fff",
  color: "#0d0d0d",
  border: "1px solid #0d0d0d",
  borderRadius: 10,
  cursor: "pointer",
  fontSize: "0.88rem",
  fontWeight: 500,
};

function SecondaryButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} style={secondaryButtonStyle}>{children}</button>;
}

function GhostButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "0.8rem 1rem",
        background: "transparent",
        color: "#666",
        border: "none",
        cursor: "pointer",
        fontSize: "0.85rem",
      }}
    >
      {children}
    </button>
  );
}

const textareaStyle: React.CSSProperties = {
  width: "100%",
  padding: "0.6rem 0.7rem",
  border: "1px solid #ddd",
  borderRadius: 8,
  fontSize: "0.85rem",
  fontFamily: "inherit",
  lineHeight: 1.5,
  resize: "vertical",
  boxSizing: "border-box",
  background: "#fff",
};

const smallLabel: React.CSSProperties = {
  fontSize: "0.7rem",
  color: "#999",
  marginBottom: 6,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
};
