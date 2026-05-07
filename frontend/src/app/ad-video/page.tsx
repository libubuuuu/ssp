"use client";
import { useState, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";
import { compressImage } from "@/lib/utils/imageCompress";
import { parseMarkdown, toAdVideoScript, MARKDOWN_TEMPLATE_SAMPLE } from "@/lib/scriptMarkdown";

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

// P37: 删 preview 首帧后,流程从 4 步精简到 3 步
type Step = 1 | 2 | 3;

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
  const [duration, setDuration] = useState(12);  // P40: v1.5/pro 单段上限
  const [region, setRegion] = useState<"CN" | "Global">("CN");  // P100: 国内抖音 / 海外 TikTok
  // P180(2026-05-08):脚本模式 — auto = AI 自动生成 / paste = 用户粘贴 markdown
  const [scriptMode, setScriptMode] = useState<"auto" | "paste">("auto");
  const [pastedMarkdown, setPastedMarkdown] = useState<string>("");
  const [pasteError, setPasteError] = useState<string>("");
  // P186/P187(2026-05-08):参考视频 — grid + 中间帧 + VLM 判人物
  const [styleRefVideo, setStyleRefVideo] = useState<File | null>(null);
  const [styleRefGridUrl, setStyleRefGridUrl] = useState<string>("");
  const [styleRefMiddleUrl, setStyleRefMiddleUrl] = useState<string>("");  // P187:中间帧
  const [styleRefHasPeople, setStyleRefHasPeople] = useState<boolean | null>(null);  // P187:VLM 检测
  const [styleRefUploading, setStyleRefUploading] = useState<boolean>(false);
  const [styleRefError, setStyleRefError] = useState<string>("");
  // P133(2026-05-05):用户敲"Kling AI Avatar v2 Standard"($0.0562/s,5s = $0.28),
  // 砍掉视频引擎下拉选项 — 后端硬编码用 fal-ai/kling-video/ai-avatar/v2/standard。
  // 用户怒"v3 pro $0.84/5s 那么贵"。这个字段保留是为了兼容老 jobs API,实际后端 P133 不读。
  const [talkingHead, _setTalkingHead] = useState<string>("fal-ai/kling-video/ai-avatar/v2/standard");

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
  // P142:几宫格原图 + N 张子图(分镜图)
  const [gridImageUrl, setGridImageUrl] = useState("");
  const [panelImageUrls, setPanelImageUrls] = useState<string[]>([]);
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
      // P111: 背景场景图(可选)同时上传 — VLM 写脚本时也要看,定 overall_setting / 话术情境
      if (bgFile) {
        const compressedBg = await compressImage(bgFile);
        fd.append("background_file", compressedBg);
      }
      // P100: region 透传(国内抖音 / 海外 TikTok)
      fd.append("region", region);
      const r = await fetch(`${API_BASE}/api/ad-video/analyze?total_duration=${duration}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      const d = await r.json();
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : (d.detail?.message || "审核失败"));
      if (typeof d.cost === "number" && d.cost > 0) adjustLocalUserCredits(-d.cost);

      setAudit(d.audit);
      // P180:粘贴模式 — 用户的 markdown 脚本覆盖 VLM 生成的脚本
      if (scriptMode === "paste" && pastedMarkdown.trim()) {
        try {
          const parsed = parseMarkdown(pastedMarkdown);
          const adScript = toAdVideoScript(parsed);
          // 字段缺失兜底:复用 VLM 给的 model_description(粘贴的脚本里没写时)
          if (!adScript.model_description.trim() && d.script?.model_description) {
            adScript.model_description = d.script.model_description;
          }
          setScript(adScript);
          // P184:粘贴模式从 markdown total_duration 字段或 scenes 算出总时长,覆盖默认 12s
          let pastedTotal = parsed.total_duration_sec || 0;
          if (!pastedTotal) {
            // 从 scenes 算:解析每段 time_range 的 duration 之和
            for (const s of adScript.scenes) {
              const m = (s.time_range || "").match(/(\d+(?:\.\d+)?)\s*[-~到至]\s*(\d+(?:\.\d+)?)/);
              if (m) pastedTotal += parseFloat(m[2]) - parseFloat(m[1]);
            }
          }
          if (pastedTotal > 0 && pastedTotal <= 300) {
            setDuration(Math.round(pastedTotal));
          }
        } catch (parseErr: unknown) {
          // 解析失败 fallback 到 VLM 生成的脚本,但提示用户
          setScript(d.script);
          setErr(`粘贴的脚本解析失败,已 fallback 到 AI 生成版本:${parseErr instanceof Error ? parseErr.message : String(parseErr)}`);
        }
      } else {
        setScript(d.script);
      }
      // /analyze 内部已上传到 fal storage,直接复用 URL,后面 /preview 不用再传
      if (d.product_image_url) {
        setProductImageUrl(d.product_image_url);
      }
      // P34: 反面图(若上传了)
      if (d.product_back_image_url) {
        setProductBackImageUrl(d.product_back_image_url);
      }
      // P111: 背景图 URL(若上传了)— /preview 阶段直接复用,免重传
      if (d.background_image_url) {
        setBgImageUrl(d.background_image_url);
      }
      setStep(2);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  // P186/P187:上传参考视频 → 后端抽 grid + 中间帧 + VLM 判人物
  const uploadStyleRefVideo = async (f: File) => {
    setStyleRefVideo(f);
    setStyleRefGridUrl("");
    setStyleRefMiddleUrl("");
    setStyleRefHasPeople(null);
    setStyleRefError("");
    setStyleRefUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`${API_BASE}/api/ad-video/extract-style-frames`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "提取参考视频失败");
      setStyleRefGridUrl(d.grid_image_url);
      setStyleRefMiddleUrl(d.middle_frame_url || "");
      setStyleRefHasPeople(typeof d.has_people === "boolean" ? d.has_people : null);
    } catch (e) {
      setStyleRefError(errMsg(e, "上传 / 抽帧失败"));
      setStyleRefVideo(null);
    } finally {
      setStyleRefUploading(false);
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

      // P182(2026-05-08):粘贴模式 + 没上传背景 + 脚本里有 overall_setting
      // → GPT-Image 2 自动生成一张干净背景图(N 段共享)
      // P189(2026-05-08):上传了参考视频时,中间帧已经能当背景,跳过 P182 避免多此一举
      if (scriptMode === "paste" && !bUrl && script.overall_setting && !styleRefMiddleUrl) {
        setLoadingMsg("根据脚本自动生成背景图(GPT-Image 2,30-180s)...");
        try {
          const bgRes = await fetch(`${API_BASE}/api/ad-video/generate-background`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
            body: JSON.stringify({ scene_description: script.overall_setting, aspect_ratio: "9:16" }),
          });
          const bgData = await bgRes.json();
          if (bgRes.ok && bgData.image_url) {
            bUrl = bgData.image_url;
            setBgImageUrl(bUrl);
            if (typeof bgData.cost === "number" && bgData.cost > 0) adjustLocalUserCredits(-bgData.cost);
          } else {
            // 失败不阻塞,fallback 让 GPT-Image 2 在合成首帧时自己想象背景
            console.warn("自动生成背景失败,fallback 到首帧自带:", bgData.detail);
          }
        } catch (bgErr) {
          console.warn("自动生成背景异常:", bgErr);
        }
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
          style_reference_image_url: null,  // P191(2026-05-08):砍掉 grid 不再传 GPT(冗余)
          // P192(2026-05-08):只有黏贴脚本模式才传参考视频帧 — auto 模式按提示词做,不蹭参考视频
          reference_video_frame_url: scriptMode === "paste" ? (styleRefMiddleUrl || null) : null,
          ref_video_has_people: scriptMode === "paste" ? styleRefHasPeople : null,
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
    if (!script) return;
    // P37: 不再需要首帧 URL — reference-to-video 直接从产品图出发
    setErr("");
    setLoading(true);
    setLoadingMsg("提交视频生成任务...");

    try {
      // 确保产品图已上传到 fal storage(callAnalyze 通常已上传过,这里兜底)
      let pUrl = productImageUrl;
      if (!pUrl && productFile) {
        setLoadingMsg("上传产品图...");
        pUrl = await uploadImage(productFile);
        setProductImageUrl(pUrl);
      }
      if (!pUrl) {
        throw new Error("缺产品图");
      }

      // 背景图(可选)
      let bUrl = bgImageUrl;
      if (bgFile && !bUrl) {
        setLoadingMsg("上传背景图...");
        bUrl = await uploadImage(bgFile);
        setBgImageUrl(bUrl);
      }

      // P184:实时按 scenes 算总时长(用户增删段后,duration state 可能滞后)
      const liveDuration = Math.max(5, Math.round(computeTotalDuration(script.scenes)));
      setLoadingMsg("视频生成中...");
      const r = await fetch(`${API_BASE}/api/ad-video/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token()}`,
        },
        body: JSON.stringify({
          image_url: pUrl,  // 兼容字段
          // P36: 直接喂 reference-to-video
          product_image_url: pUrl,
          product_back_image_url: productBackImageUrl || null,
          background_image_url: bUrl || null,
          style_reference_image_url: null,  // P191(2026-05-08):砍掉 grid 不再传 GPT(冗余)
          // P192(2026-05-08):只有黏贴脚本模式才传参考视频帧 — auto 模式按提示词做,不蹭参考视频
          reference_video_frame_url: scriptMode === "paste" ? (styleRefMiddleUrl || null) : null,
          ref_video_has_people: scriptMode === "paste" ? styleRefHasPeople : null,
          script,
          duration: liveDuration,
          aspect_ratio: "9:16",
          resolution: "720p",
          enable_audio: true,
          talking_head_endpoint: talkingHead,  // P105: 对口型模型选择
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "提交失败");
      if (typeof d.cost === "number" && d.cost > 0) adjustLocalUserCredits(-d.cost);

      setStep(3);
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
          // P142:几宫格分镜图展示
          if (j.result.grid_image_url) setGridImageUrl(j.result.grid_image_url);
          if (Array.isArray(j.result.panel_image_urls)) setPanelImageUrls(j.result.panel_image_urls);
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

  // P184(2026-05-08):时间轴工具
  const parseTimeRange = (tr: string): { start: number; end: number; duration: number } => {
    const m = (tr || "").match(/(\d+(?:\.\d+)?)\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*s?/);
    if (!m) return { start: 0, end: 5, duration: 5 };
    const start = parseFloat(m[1]);
    const end = parseFloat(m[2]);
    return { start, end, duration: Math.max(0.5, end - start) };
  };
  const formatTimeRange = (start: number, end: number): string => {
    const fmt = (n: number) => Number.isInteger(n) ? String(n) : n.toFixed(1);
    return `${fmt(start)}-${fmt(end)}s`;
  };
  /** 重新对齐 scenes 的 time_range,从 0 开始无缝衔接,各段保留自己的 duration */
  const realignScenes = (scenes: Scene[]): Scene[] => {
    let cursor = 0;
    return scenes.map((s) => {
      const { duration } = parseTimeRange(s.time_range);
      const start = cursor;
      const end = cursor + duration;
      cursor = end;
      return { ...s, time_range: formatTimeRange(start, end) };
    });
  };
  const computeTotalDuration = (scenes: Scene[]): number => {
    return scenes.reduce((acc, s) => acc + parseTimeRange(s.time_range).duration, 0);
  };

  const updateScene = (idx: number, key: keyof Scene, value: string) => {
    if (!script) return;
    let newScenes = script.scenes.map((s, i) => (i === idx ? { ...s, [key]: value } : s));
    // P184:用户改 time_range → 自动重新对齐后续段(避免段间空白/重叠)
    if (key === "time_range") newScenes = realignScenes(newScenes);
    setScript({ ...script, scenes: newScenes });
  };

  /** P184:删除分镜 — 后续段 time_range 自动往前补 */
  const deleteScene = (idx: number) => {
    if (!script || script.scenes.length <= 1) return;
    const filtered = script.scenes.filter((_, i) => i !== idx);
    const realigned = realignScenes(filtered).map((s, i) => ({ ...s, id: i + 1 }));
    setScript({ ...script, scenes: realigned });
  };

  /** P184:新增分镜 — 默认 5 秒接在末尾 */
  const addScene = () => {
    if (!script) return;
    const lastEnd = script.scenes.length > 0
      ? parseTimeRange(script.scenes[script.scenes.length - 1].time_range).end
      : 0;
    const newId = script.scenes.length + 1;
    const newScene: Scene = {
      id: newId,
      time_range: formatTimeRange(lastEnd, lastEnd + 5),
      purpose: "",
      shot_language: "medium-shot",
      content: "新增镜头",
      visual_prompt: "Photorealistic commercial fashion shoot, medium shot, model presenting the item",
      speech: "",
    };
    setScript({ ...script, scenes: [...script.scenes, newScene] });
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
    // P180:重置粘贴模式相关状态
    setScriptMode("auto");
    setPastedMarkdown("");
    setPasteError("");
    // P186/P187:重置参考视频相关
    setStyleRefVideo(null);
    setStyleRefGridUrl("");
    setStyleRefMiddleUrl("");
    setStyleRefHasPeople(null);
    setStyleRefError("");
    if (pollRef.current) clearInterval(pollRef.current);
  };

  // ============== Render ==============

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        {/* 标题 */}
        <div style={{ marginBottom: "2rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>AI 创作工具</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>
            AI 带货
            <span style={{ fontStyle: "italic" }}> 视频</span>
          </h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            上传白底产品图 · AI 自动审核与撰稿 · 一键生成口播带货视频
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
            {/* P180:脚本模式切换 — auto / paste */}
            <div style={{ marginBottom: 18, padding: "0.6rem 0.8rem", background: "#f9f7f2", borderRadius: 10 }}>
              <div style={{ fontSize: "0.85rem", fontWeight: 500, marginBottom: 8 }}>脚本来源</div>
              <div style={{ display: "flex", gap: 8 }}>
                <label style={{ flex: 1, padding: "0.6rem 0.8rem", border: scriptMode === "auto" ? "2px solid #0d0d0d" : "1px solid #ddd", background: scriptMode === "auto" ? "#fff" : "transparent", borderRadius: 8, cursor: "pointer" }}>
                  <input type="radio" name="scriptMode" checked={scriptMode === "auto"} onChange={() => setScriptMode("auto")} style={{ marginRight: 6 }} />
                  <strong style={{ fontSize: "0.88rem" }}>AI 自动生成脚本</strong>
                  <div style={{ fontSize: "0.75rem", color: "#777", marginTop: 2 }}>VLM 看图自己写,适合不知道写什么的用户</div>
                </label>
                <label style={{ flex: 1, padding: "0.6rem 0.8rem", border: scriptMode === "paste" ? "2px solid #0d0d0d" : "1px solid #ddd", background: scriptMode === "paste" ? "#fff" : "transparent", borderRadius: 8, cursor: "pointer" }}>
                  <input type="radio" name="scriptMode" checked={scriptMode === "paste"} onChange={() => setScriptMode("paste")} style={{ marginRight: 6 }} />
                  <strong style={{ fontSize: "0.88rem" }}>我自己粘贴脚本</strong>
                  <div style={{ fontSize: "0.75rem", color: "#777", marginTop: 2 }}>粘贴 markdown 跳过 AI 生成,可从「视频脚本提取」工具拷贝过来</div>
                </label>
              </div>
            </div>

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

            {/* P186(2026-05-08):粘贴模式可选上传参考视频(GPT 出图借风格)*/}
            {scriptMode === "paste" && (
              <div style={{ marginTop: 18 }}>
                <label style={{ display: "block", fontSize: "0.9rem", color: "#444", marginBottom: 6, fontWeight: 500 }}>
                  风格参考视频(可选,GPT 出图会借这视频的视觉风格)
                </label>
                <label style={{ display: "block", border: "2px dashed #ddd", borderRadius: 10, padding: "0.8rem", textAlign: "center", cursor: styleRefUploading ? "wait" : "pointer", background: styleRefVideo ? "#f9f7f2" : "#fff" }}>
                  <input type="file" accept="video/*" style={{ display: "none" }} disabled={styleRefUploading}
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadStyleRefVideo(f); }} />
                  {styleRefUploading ? (
                    <div style={{ color: "#666", fontSize: "0.85rem" }}>上传 + ffmpeg 抽帧中...</div>
                  ) : styleRefVideo && styleRefGridUrl ? (
                    <div>
                      <div style={{ fontSize: "0.82rem", color: "#0d8a3e", marginBottom: 6 }}>✓ {styleRefVideo.name}</div>
                      <img src={styleRefGridUrl} alt="参考帧 grid 预览" style={{ maxWidth: 240, borderRadius: 6 }} />
                      {styleRefHasPeople === true && (
                        <div style={{ marginTop: 8, padding: "0.5rem 0.7rem", background: "#e8f5ec", border: "1px solid #b6dcc1", borderRadius: 6, fontSize: "0.78rem", color: "#0d6831" }}>
                          <div style={{ marginBottom: 4 }}>✓ AI 检测到参考视频<strong>含人物</strong> → 生成模特出镜 + 口播带货视频(Kling Avatar)</div>
                          <button onClick={(e) => { e.preventDefault(); setStyleRefHasPeople(false); }}
                            style={{ background: "transparent", border: "1px solid #b6dcc1", padding: "2px 8px", borderRadius: 4, fontSize: "0.72rem", cursor: "pointer", color: "#0d6831" }}>
                            AI 判错了 → 手动改成无人物
                          </button>
                        </div>
                      )}
                      {styleRefHasPeople === false && (
                        <div style={{ marginTop: 8, padding: "0.5rem 0.7rem", background: "#fff8e6", border: "1px solid #f5d77a", borderRadius: 6, fontSize: "0.78rem", color: "#7a5800" }}>
                          <div style={{ marginBottom: 4 }}>ℹ️ 参考视频<strong>无人物</strong> → 生成纯产品展示视频(无模特,seedance,有口播脚本时自动配 TTS)</div>
                          <button onClick={(e) => { e.preventDefault(); setStyleRefHasPeople(true); }}
                            style={{ background: "transparent", border: "1px solid #f5d77a", padding: "2px 8px", borderRadius: 4, fontSize: "0.72rem", cursor: "pointer", color: "#7a5800" }}>
                            AI 判错了 → 手动改成有人物
                          </button>
                        </div>
                      )}
                      <div style={{ fontSize: "0.72rem", color: "#888", marginTop: 6 }}>
                        参考视频中间帧会作为场景背景锁,出图风格/构图会贴近参考视频
                      </div>
                    </div>
                  ) : (
                    <div style={{ color: "#999", fontSize: "0.85rem" }}>点击上传参考视频(MP4/MOV,≤50MB)— 不传也能生成,GPT 自由发挥</div>
                  )}
                </label>
                {styleRefError && (
                  <div style={{ marginTop: 6, padding: "0.4rem 0.7rem", background: "#fff3f3", border: "1px solid #fcc", color: "#c33", borderRadius: 6, fontSize: "0.8rem" }}>{styleRefError}</div>
                )}
              </div>
            )}

            {/* P180:粘贴模式时显示 markdown 输入框 */}
            {scriptMode === "paste" && (
              <div style={{ marginTop: 18 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <label style={{ fontSize: "0.9rem", color: "#444", fontWeight: 500 }}>粘贴 markdown 脚本</label>
                  <button
                    type="button"
                    onClick={() => { setPastedMarkdown(MARKDOWN_TEMPLATE_SAMPLE); setPasteError(""); }}
                    style={{ fontSize: "0.75rem", color: "#0d0d0d", background: "transparent", border: "1px solid #ddd", padding: "0.3rem 0.6rem", borderRadius: 6, cursor: "pointer" }}>
                    填入模板示例
                  </button>
                </div>
                <textarea
                  value={pastedMarkdown}
                  onChange={(e) => { setPastedMarkdown(e.target.value); setPasteError(""); }}
                  rows={12}
                  placeholder={`# 视频脚本\n\n**总时长:** 15s\n**整体场景:** 客厅,自然光\n\n## 镜 1 · 中景 · 0-5s\n**动作:** xxx\n**画面:** xxx\n**口播:** xxx\n\n... 点"填入模板示例"看完整格式`}
                  style={{ width: "100%", padding: "0.7rem 0.9rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.83rem", fontFamily: "monospace", resize: "vertical", lineHeight: 1.5 }}
                />
                {pasteError && (
                  <div style={{ marginTop: 6, padding: "0.5rem 0.8rem", background: "#fff3f3", border: "1px solid #fcc", color: "#c33", borderRadius: 6, fontSize: "0.82rem" }}>
                    {pasteError}
                  </div>
                )}
                <div style={{ fontSize: "0.78rem", color: "#888", marginTop: 6 }}>
                  💡 从「视频脚本提取」(<a href="/video/extract" style={{ color: "#0d0d0d" }}>侧边栏 ⌬ 入口</a>)拷贝粘贴最快;粘贴后系统会解析填到分镜表,你还能手动改。
                </div>
              </div>
            )}
            {/* P184(2026-05-08):粘贴模式不需要选总时长,从脚本里 time_range 自动算 */}
            {scriptMode === "auto" ? (
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
                  <option value={5}>5 秒(1 个分镜 · 5s)</option>
                  <option value={8}>8 秒(2 个分镜 · 5s+3s)</option>
                  <option value={10}>10 秒(2 个分镜 · 5s+5s)</option>
                  <option value={12}>12 秒(2 个分镜 · 5s+7s)</option>
                  <option value={30}>30 秒(3 个分镜 · 各 10s)</option>
                  <option value={60}>60 秒(6 个分镜 · 各 10s)</option>
                  <option value={120}>120 秒(12 个分镜 · 各 10s)</option>
                  <option value={180}>180 秒(18 个分镜 · 各 10s)</option>
                  <option value={300}>300 秒(30 个分镜 · 各 10s · 共 5 分钟)</option>
                </select>
                <div style={{ fontSize: "0.8rem", color: "#888", marginTop: 6 }}>
                  超过 15 秒会自动按分镜分段生成,最后无缝拼接
                </div>
              </div>
            ) : (
              <div style={{ marginTop: 20, padding: "0.6rem 0.8rem", background: "#f0f7fb", borderRadius: 8, fontSize: "0.82rem", color: "#1a4068" }}>
                💡 粘贴脚本模式不需要选总时长 — 系统会从你脚本里的 `**总时长:**` 字段或所有 `time_range` 自动算出。
              </div>
            )}
            {/* P100: 国内 / 海外 region 选择 */}
            <div style={{ marginTop: 20 }}>
              <label style={{ display: "block", fontSize: "0.9rem", color: "#444", marginBottom: 8, fontWeight: 500 }}>
                目标市场
              </label>
              <select
                value={region}
                onChange={(e) => setRegion(e.target.value as "CN" | "Global")}
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
                <option value="CN">CN 国内抖音</option>
                <option value="Global">海外 TikTok</option>
              </select>
            </div>
            {/* P133:视频引擎硬编码为 Kling AI Avatar v2 Standard,无下拉选项 */}
            <div style={{ marginTop: 20 }}>
              <label style={{ display: "block", fontSize: "0.9rem", color: "#444", marginBottom: 8, fontWeight: 500 }}>
                视频引擎
              </label>
              <div
                style={{
                  width: "100%",
                  padding: "0.7rem 0.9rem",
                  border: "1px solid #ddd",
                  borderRadius: 8,
                  fontSize: "0.95rem",
                  background: "#f9f9f9",
                  color: "#444",
                }}
              >
                AI 数字人口播 · 模特口型自动同步
              </div>
              <div style={{ fontSize: "0.8rem", color: "#888", marginTop: 6 }}>
                自动合成「模特 + 产品 + 场景」首帧,配 AI 配音生成口型同步视频,无生硬拼接。
                <br />
              </div>
            </div>
            <PrimaryButton
              onClick={() => {
                // P180:粘贴模式先客户端 parse 验证
                if (scriptMode === "paste") {
                  if (!pastedMarkdown.trim()) {
                    setPasteError("请粘贴 markdown 脚本,或切回「AI 自动生成」模式");
                    return;
                  }
                  try {
                    const parsed = parseMarkdown(pastedMarkdown);
                    if (parsed.scenes.length === 0) {
                      setPasteError("解析后没有任何镜头,请检查是否有 `## 镜 1 · 景别 · 时间` 这种标题行");
                      return;
                    }
                    setPasteError("");
                  } catch (e: unknown) {
                    setPasteError(e instanceof Error ? e.message : String(e));
                    return;
                  }
                }
                callAnalyze();
              }}
              disabled={!productFile}
              marginTop>
              {scriptMode === "paste" ? "解析脚本 + AI 审核产品图(消耗 1 积分) →" : "开始 AI 审核(消耗 1 积分) →"}
            </PrimaryButton>
          </Card>
        )}

        {/* Step 2: 审核 + 脚本 */}
        {step === 2 && audit && script && (
          <>
            <Card title="审核通过" desc="小九已分析图片,以下为生成的分镜脚本(可编辑)">
              <AuditGrid audit={audit} />
            </Card>

            {/* P182(2026-05-08):非服装大类 — 提示用户用「视频脚本提取」工具拿更好的脚本 */}
            {(() => {
              const cat = (audit.category || "").trim();
              const isClothing = ["服装", "鞋", "包", "配饰"].some(p => cat.startsWith(p));
              if (isClothing || !cat) return null;
              return (
                <div style={{ background: "#fff8e6", border: "1px solid #f5d77a", borderRadius: 10, padding: "0.9rem 1.1rem", marginBottom: "1rem", fontSize: "0.88rem", color: "#7a5800" }}>
                  <div style={{ fontWeight: 500, marginBottom: 4 }}>💡 你的产品视觉不太明显(类目:{cat})</div>
                  <div style={{ lineHeight: 1.6 }}>
                    数码 / 小工具 / 日用 类产品 AI 自动写脚本时容易抓不到卖点(模特拿小物件画面单调)。
                    <strong>建议先用「视频脚本提取」工具</strong>(侧栏 ⌬ 图标)从一个同类爆款视频提取脚本,
                    粘贴回来覆盖 AI 生成的版本 → 出片效果会好很多。
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <a href="/video/extract" style={{ background: "#0d0d0d", color: "#fff", padding: "0.4rem 0.9rem", borderRadius: 6, textDecoration: "none", fontSize: "0.82rem" }}>
                      去提取脚本 →
                    </a>
                  </div>
                </div>
              );
            })()}

            <Card title="分镜脚本" desc={`${script.scenes.length} 个分镜 · 共 ${computeTotalDuration(script.scenes).toFixed(0)} 秒 · 可逐字编辑 / 删段 / 加段(时间轴自动对齐)`}>
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
                  onDelete={() => deleteScene(idx)}
                  canDelete={script.scenes.length > 1}
                />
              ))}
              <button onClick={addScene}
                style={{ width: "100%", padding: "0.8rem", background: "transparent", border: "2px dashed #ccc", borderRadius: 10, fontSize: "0.9rem", color: "#666", cursor: "pointer", marginTop: 8 }}>
                + 新增分镜(末尾追加 5 秒)
              </button>
            </Card>

            <ActionRow>
              <GhostButton onClick={() => setStep(1)}>← 重新上传</GhostButton>
              <PrimaryButton onClick={callGenerate}>
                生成视频(消耗 30 积分) →
              </PrimaryButton>
            </ActionRow>
          </>
        )}

        {/* P37: 删 step 3 首帧预览(reference-to-video 不再需要),原 step 4 改 step 3 */}
        {step === 3 && (
          <Card title="视频生成" desc={videoUrl ? "完成!可下载或分享" : jobProgress || "正在排队..."}>
            {!videoUrl && (
              <div style={{ background: "#fff", padding: "3rem", borderRadius: 12, textAlign: "center" }}>
                <div style={{ fontSize: "0.9rem", color: "#666" }}>{jobProgress || "排队中..."}</div>
                <div style={{ fontSize: "0.75rem", color: "#999", marginTop: 8 }}>
                  视频生成一般需要 1-3 分钟,可关闭页面去做别的事,任务在后台跑
                </div>
              </div>
            )}
            {videoUrl && (
              <div style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, display: "flex", justifyContent: "center" }}>
                <video src={videoUrl} controls playsInline style={{ maxWidth: 360, width: "100%", aspectRatio: "9/16", borderRadius: 8, background: "#000" }} />
              </div>
            )}
            {/* P142:几宫格原图 + N 张子图分镜展示 */}
            {videoUrl && (gridImageUrl || panelImageUrls.length > 0) && (
              <div style={{ background: "#fff", padding: "1.5rem", borderRadius: 12, marginTop: 12 }}>
                <div style={{ fontSize: "0.95rem", fontWeight: 500, color: "#333", marginBottom: 12 }}>
                  分镜图(每个分镜用到的画面)
                </div>
                <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
                  {gridImageUrl && (
                    <div style={{ flex: "0 0 auto" }}>
                      <div style={{ fontSize: "0.78rem", color: "#888", marginBottom: 6 }}>整张几宫格</div>
                      <a href={gridImageUrl} target="_blank" rel="noreferrer">
                        <img src={gridImageUrl} alt="storyboard grid" style={{ maxHeight: 280, borderRadius: 8, border: "1px solid #eee" }} />
                      </a>
                    </div>
                  )}
                  {panelImageUrls.length > 0 && (
                    <div style={{ flex: "1 1 200px" }}>
                      <div style={{ fontSize: "0.78rem", color: "#888", marginBottom: 6 }}>
                        裁切后 {panelImageUrls.length} 张子图(每段画面)
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(120px,1fr))", gap: 8 }}>
                        {panelImageUrls.map((url, i) => (
                          <a key={i} href={url} target="_blank" rel="noreferrer" style={{ position: "relative" }}>
                            <img src={url} alt={`panel ${i + 1}`} style={{ width: "100%", aspectRatio: "9/16", objectFit: "cover", borderRadius: 6, border: "1px solid #eee" }} />
                            <div style={{ position: "absolute", top: 4, left: 4, background: "rgba(0,0,0,0.6)", color: "#fff", fontSize: "0.7rem", padding: "2px 6px", borderRadius: 4 }}>
                              段 {i + 1}
                            </div>
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
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

function SceneCard({ scene, onChange, onRegen, onDelete, canDelete }: {
  scene: Scene;
  onChange: (key: keyof Scene, value: string) => void;
  onRegen: () => void;
  onDelete: () => void;
  canDelete: boolean;
}) {
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
        <div style={{ display: "flex", gap: 6 }}>
          <button onClick={onRegen} style={{ background: "transparent", border: "1px solid #ccc", padding: "4px 10px", borderRadius: 8, fontSize: "0.75rem", cursor: "pointer", color: "#666" }}>
            ↻ 重新生成
          </button>
          {canDelete && (
            <button onClick={() => { if (confirm(`确认删除镜头 ${scene.id}?后续段会自动往前补,无空隙。`)) onDelete(); }}
              style={{ background: "transparent", border: "1px solid #fcc", padding: "4px 10px", borderRadius: 8, fontSize: "0.75rem", cursor: "pointer", color: "#c33" }}>
              ✕ 删除
            </button>
          )}
        </div>
      </div>

      <FieldBlock label="时间段(改了会自动对齐后续段)">
        <input value={scene.time_range} onChange={(e) => onChange("time_range", e.target.value)}
          style={{ width: 120, padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.85rem" }} />
      </FieldBlock>
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
