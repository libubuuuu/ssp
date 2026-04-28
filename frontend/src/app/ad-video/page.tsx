"use client";
import { useState, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";

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

  // Step 1: 上传
  const [productFile, setProductFile] = useState<File | null>(null);
  const [productPreview, setProductPreview] = useState("");
  const [bgFile, setBgFile] = useState<File | null>(null);
  const [bgPreview, setBgPreview] = useState("");

  // Step 2: 审核 + 脚本(从 /analyze 返回)
  const [audit, setAudit] = useState<Audit | null>(null);
  const [script, setScript] = useState<Script | null>(null);

  // Step 3: 首帧预览
  const [productImageUrl, setProductImageUrl] = useState(""); // fal storage URL
  const [bgImageUrl, setBgImageUrl] = useState("");
  const [previewImageUrl, setPreviewImageUrl] = useState("");

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
    setLoadingMsg("Claude 正在审核图片并生成脚本...");

    try {
      const fd = new FormData();
      fd.append("file", productFile);
      const r = await fetch(`${API_BASE}/api/ad-video/analyze`, {
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
      setStep(2);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  const uploadImage = async (file: File): Promise<string> => {
    const fd = new FormData();
    fd.append("file", file);
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

      setLoadingMsg("Nano Banana 2 合成首帧...");
      const r = await fetch(`${API_BASE}/api/ad-video/preview`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token()}`,
        },
        body: JSON.stringify({
          product_image_url: pUrl,
          background_image_url: bUrl || null,
          model_description: script.model_description,
          scene_visual_prompt: script.scenes[0]?.visual_prompt || "",
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "首帧合成失败");
      if (typeof d.cost === "number" && d.cost > 0) adjustLocalUserCredits(-d.cost);

      setPreviewImageUrl(d.image_url);
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
          script,
          duration: 15,
          aspect_ratio: "9:16",
          resolution: "1080p",
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
            上传白底产品图 · Claude 自动审核与撰稿 · Seedance 2.0 生成口播视频
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
          <Card title="第一步:上传产品图" desc="建议白底图、4:5 或 1:1、主体居中、光线均匀">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <UploadBox
                label="产品图(必填)"
                preview={productPreview}
                onFile={onProductFile}
                required
              />
              <UploadBox
                label="背景图(可选)"
                preview={bgPreview}
                onFile={onBgFile}
                hint="不传则由 AI 自动生成场景"
              />
            </div>
            <PrimaryButton onClick={callAnalyze} disabled={!productFile} marginTop>
              开始 AI 审核(消耗 1 积分) →
            </PrimaryButton>
          </Card>
        )}

        {/* Step 2: 审核 + 脚本 */}
        {step === 2 && audit && script && (
          <>
            <Card title="审核通过" desc="Claude 已分析图片,以下为生成的分镜脚本(可编辑)">
              <AuditGrid audit={audit} />
            </Card>

            <Card title="分镜脚本" desc={`${script.scenes.length} 个分镜 · 共 15 秒 · 可逐字编辑或点'重新生成'让 AI 改写`}>
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

        {/* Step 3: 首帧预览 */}
        {step === 3 && previewImageUrl && (
          <>
            <Card title="首帧预览" desc="这就是视频开场画面,满意后再生成视频(避免浪费积分)">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <div>
                  <div style={smallLabel}>原始产品图</div>
                  <img src={productPreview} alt="产品图预览" style={{ width: "100%", borderRadius: 12, background: "#fff" }} />
                </div>
                <div>
                  <div style={smallLabel}>AI 合成首帧</div>
                  <img src={previewImageUrl} alt="首帧预览" style={{ width: "100%", borderRadius: 12, background: "#fff", border: "2px solid #0d0d0d" }} />
                </div>
              </div>
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
