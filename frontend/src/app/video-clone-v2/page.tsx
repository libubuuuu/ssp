"use client";
import { Fragment, useEffect, useRef, useState } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

// 多段 drop 区颜色(最多 4 区)
const RANGE_COLORS = ["#dc2626", "#f97316", "#9333ea", "#06b6d4"] as const;
const MAX_RANGES = 4;

// 2026-05-10 砍单档:Tier type 删除
type Role = "product" | "person" | "scene" | "reference";
type SourceType = "ai" | "original";
type ReplacementMode = "partial" | "full";
type Status =
  | "idle"
  | "uploading"
  | "ready"
  | "submitting"
  | "processing"
  | "completed"
  | "failed";

interface SegmentChoice {
  idx: number;
  start: number;
  duration: number;
  thumbnail_url: string | null;
  // 2026-05-10 砍单档:allowed_tiers 字段删除(后端 SegmentChoice 模型同步删)
}
interface PreviewResp {
  type: "single" | "ultimate";
  segments: SegmentChoice[];
  preview_token: string;
  scene_count: number;
}
interface EstimateResp {
  type: string;
  ai_segments_count: number;
  total_segments: number;
  total_credits: number;
  total_rmb_display: string;
  estimated_minutes: number;
}
interface ImageItem {
  url: string;
  role: Role;
  filename: string;
}
interface Template {
  id: string;
  label: string;
  template: string;
}
interface TrimCandidate {
  label: string;
  position: "head" | "middle" | "tail";
  start: number;
  end: number;
  motion_score: number;
  recommended: boolean;
}
interface CheckDurationResp {
  needs_trim: boolean;
  current_duration: number;
  target_duration: number;
  drop_seconds: number;
  suggestions: TrimCandidate[];
}
interface JobView {
  job_id: string;
  type: string;
  replacement_mode: string;
  status: string;
  progress: { completed: number; total_ai: number; total_original: number };
  segments: Array<{ idx: number; source_type: string; status: string; output_url: string | null }>;
  final_video_url: string | null;
  final_video_url_watermarked: string | null;
  final_video_url_raw: string | null;
  total_credits_charged: number;
  total_credits_refunded: number;
  error: string | null;
}

const ROLE_LABELS: Record<Role, string> = {
  product: "产品",
  person: "人物",
  scene: "场景",
  reference: "参考",
};

// 2026-05-10 砍单档:TIER_DISPLAY 删除,改 SEGMENT_PRICE 单档常量
// 占位定价用原 standard 档值,commit 5 真测后基于 fal cost 重定
const SEGMENT_PRICE = {
  rmb: "19.9",
  credits: 20,
  label: "AI 替换",
} as const;

// 每段独立选择
interface SegmentSelection {
  source_type: SourceType;
  // 2026-05-10 砍单档:tier 字段删除
}

export default function VideoCloneV2Page() {
  // 上传状态
  const [video, setVideo] = useState<{ url: string; duration: number; sha256: string } | null>(null);
  const [uploadingVideo, setUploadingVideo] = useState(false);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [pendingRole, setPendingRole] = useState<Role>("product");

  // ─── B+ 阶段:check-duration 弹窗 + 多段 drop ranges ───
  // 用户可任意选 N 段非连续要丢弃的区间,总和 = drop_seconds 才能确认
  const [trimPopup, setTrimPopup] = useState<CheckDurationResp | null>(null);
  // 完成后传给 /create:trim 字段保留主段(legacy)+ trim_drop_ranges 数组(全段)
  const [trim, setTrim] = useState<{
    start: number; end: number; trimmed: number;
    ranges: Array<{ start: number; end: number }>
  } | null>(null);
  // 弹窗打开时的多段编辑状态
  const [dropRanges, setDropRanges] = useState<Array<{ start: number; end: number }>>([]);

  // 切片预览(post-trim)
  const [preview, setPreview] = useState<PreviewResp | null>(null);

  // 2026-05-10 砍单档:全局 tier state 删除(single 模式无需选档位)

  // ultimate 模式:每段独立选择
  const [segSelections, setSegSelections] = useState<Record<number, SegmentSelection>>({});

  // ultimate 模式:replacement_mode
  const [replacementMode, setReplacementMode] = useState<ReplacementMode>("full");

  // prompt + 模板
  const [prompt, setPrompt] = useState("");
  const [aiOptimizing, setAiOptimizing] = useState(false);
  // 2026-05-11:目标市场 toggle(CN 国内 / Global 海外),影响 AI prompt 优化的语言风格
  const [region, setRegion] = useState<"CN" | "Global">("CN");
  const [templates, setTemplates] = useState<Template[]>([]);

  // 估价
  const [estimate, setEstimate] = useState<EstimateResp | null>(null);

  // 提交
  const [disclaimerChecked, setDisclaimerChecked] = useState(false);
  const [submitStatus, setSubmitStatus] = useState<Status>("idle");
  const [error, setError] = useState("");

  // 任务追踪
  const [job, setJob] = useState<JobView | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 无标识版下载二次确认
  const [showRawConfirm, setShowRawConfirm] = useState(false);
  const [rawAck1, setRawAck1] = useState(false);
  const [rawAck2, setRawAck2] = useState(false);
  const [rawAck3, setRawAck3] = useState(false);

  function token(): Record<string, string> {
    const t = typeof window !== "undefined" ? localStorage.getItem("token") : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  // ─── 拉模板 ───
  useEffect(() => {
    fetch(`${API_BASE}/api/video/clone-v2/prompt-templates`, {
      credentials: "include",
      headers: token(),
    })
      .then((r) => r.json())
      .then((d) => setTemplates(d.templates || []))
      .catch(() => {});
  }, []);

  // ─── 视频上传后 → check-duration → 必要时弹 trim,否则直接 preview-segments ───
  useEffect(() => {
    if (!video) return;
    setError("");
    fetch(`${API_BASE}/api/video/clone-v2/check-duration`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...token() },
      body: JSON.stringify({
        video_duration_sec: video.duration,
        video_sha256: video.sha256,
        video_url: video.url,
      }),
    })
      .then((r) => r.json())
      .then((d: CheckDurationResp) => {
        if (d.needs_trim) {
          // 弹窗:3 个推荐位置(head / middle / tail)+ 自己拖选(主推)
          setTrimPopup(d);
        } else {
          setTrim(null);
          fetchPreview(video.url, video.duration);
        }
      })
      .catch((e) => setError(`check-duration 失败:${e}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [video]);

  function fetchPreview(url: string, duration: number) {
    fetch(`${API_BASE}/api/video/clone-v2/preview-segments`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...token() },
      body: JSON.stringify({ video_url: url, video_duration_sec: duration }),
    })
      .then((r) => r.json())
      .then((d: PreviewResp) => {
        setPreview(d);
        if (d.type === "single") {
          // 单档:不需要 tier 推断,single 模式段卡片直接走
          setSegSelections({});
        } else {
          // ultimate:每段默认 ai(单档无 tier)
          const init: Record<number, SegmentSelection> = {};
          for (const s of d.segments) {
            init[s.idx] = { source_type: "ai" };
          }
          setSegSelections(init);
        }
      })
      .catch((e) => setError(`切片预览失败:${e}`));
  }

  // ─── trim 弹窗:多段 drop ranges 提交 ───
  // 推荐 radio / 自由编辑都通过 dropRanges 状态,确认时一并提交
  function applyMultiTrim(ranges: Array<{ start: number; end: number }>) {
    if (!video || !trimPopup) return;
    const total = ranges.reduce((s, r) => s + (r.end - r.start), 0);
    setTrim({
      start: ranges[0].start,
      end: ranges[0].end,
      trimmed: total,
      ranges: ranges.slice(),
    });
    setTrimPopup(null);
    setDropRanges([]);
    const effective = video.duration - total;
    fetchPreview(video.url, effective);
  }
  // 推荐位置 = 把 dropRanges 重置成单段(用户还能继续编辑/加段)
  function pickRecommended(picked: TrimCandidate) {
    setDropRanges([{ start: picked.start, end: picked.end }]);
  }

  // ─── 拉估价(single + ultimate 共用)───
  useEffect(() => {
    if (!preview) return;
    // 单档:body segments 不传 tier(后端 SegmentPlanItem 已加 extra="forbid",传 tier 会被拒 422)
    const segments = preview.type === "single"
      ? [{ idx: preview.segments[0].idx, source_type: "ai" }]
      : preview.segments.map((s) => {
          const sel = segSelections[s.idx];
          if (!sel || sel.source_type === "ai") {
            return { idx: s.idx, source_type: "ai" };
          }
          return { idx: s.idx, source_type: "original" };
        });
    fetch(`${API_BASE}/api/video/clone-v2/estimate`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...token() },
      body: JSON.stringify({
        type: preview.type,
        replacement_mode: preview.type === "ultimate" ? replacementMode : "full",
        segments,
      }),
    })
      .then((r) => r.json())
      .then((d) => {
        if (d.detail) { setError(d.detail); return; }
        setError("");
        setEstimate(d);
      })
      .catch(() => {});
  }, [preview, segSelections, replacementMode]);

  // ─── 视频上传 handler ───
  async function handleVideoUpload(file: File) {
    setUploadingVideo(true);
    setError("");
    setTrim(null);
    setTrimPopup(null);
    setDropRanges([]);
    setPreview(null);
    setEstimate(null);
    setSegSelections({});
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API_BASE}/api/video/clone-v2/upload/video`, {
        method: "POST",
        credentials: "include",
        headers: token(),
        body: fd,
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "上传失败");
      setVideo({ url: d.video_url, duration: d.duration_sec, sha256: d.sha256 || "" });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploadingVideo(false);
    }
  }

  // ─── 图片上传 handler ───
  async function handleImageUpload(file: File, role: Role) {
    setUploadingImage(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("role", role);
      const r = await fetch(`${API_BASE}/api/video/clone-v2/upload/image`, {
        method: "POST",
        credentials: "include",
        headers: token(),
        body: fd,
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "上传失败");
      setImages((arr) => [...arr, { url: d.image_url, role: d.role, filename: file.name }].slice(0, 3));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploadingImage(false);
    }
  }

  function applyTemplate(t: Template) { setPrompt(t.template); }

  async function handleAiOptimize() {
    const desc = prompt.trim();
    if (!desc) {
      setError("请先写一下你的想法,再点 AI 优化");
      return;
    }
    setError(null);
    setAiOptimizing(true);
    try {
      const r = await fetch(`${API_BASE}/api/video/clone-v2/generate-prompt`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", ...token() },
        body: JSON.stringify({ user_description: desc, region }),
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status}: ${txt.slice(0, 200)}`);
      }
      const data = await r.json();
      if (data.generated_prompt) {
        setPrompt(data.generated_prompt);
      }
    } catch (e) {
      setError(`AI 优化失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setAiOptimizing(false);
    }
  }

  // ─── ultimate 段更新 helper ───
  function updateSeg(idx: number, patch: Partial<SegmentSelection>) {
    setSegSelections((prev) => ({
      ...prev,
      [idx]: { ...prev[idx], ...patch } as SegmentSelection,
    }));
  }

  // ─── 提交 ───
  async function handleSubmit() {
    if (!video || !preview || preview.segments.length === 0) {
      setError("请先上传视频"); return;
    }
    if (!prompt.trim()) { setError("请填写或选择 prompt"); return; }
    if (!disclaimerChecked) { setError("请勾选《视频复刻 V2 上传声明书》"); return; }

    // 构造 segments(single / ultimate 不同)
    // 单档:body segments 不传 tier(后端 forbid 会拒)
    let segments: Array<{ idx: number; source_type: SourceType }>;
    if (preview.type === "single") {
      segments = [{ idx: preview.segments[0].idx, source_type: "ai" }];
    } else {
      segments = preview.segments.map((s) => {
        const sel = segSelections[s.idx];
        if (!sel || sel.source_type === "ai") {
          return { idx: s.idx, source_type: "ai" };
        }
        return { idx: s.idx, source_type: "original" };
      });
      // ultimate 必须至少 1 段 ai(后端校验,前端先卡)
      if (!segments.some((s) => s.source_type === "ai")) {
        setError("至少要有 1 段选 AI 生成"); return;
      }
    }

    setSubmitStatus("submitting");
    setError("");
    try {
      const body: Record<string, unknown> = {
        type: preview.type,
        replacement_mode: preview.type === "ultimate" ? replacementMode : "full",
        segments,
        video_url: video.url,
        video_duration_sec: video.duration,
        video_sha256: video.sha256,
        image_urls: images.map((i) => ({ url: i.url, role: i.role })),
        prompt,
        disclaimer_acknowledged: true,
      };
      if (trim) {
        body.trim_drop_ranges = trim.ranges.map((r) => [r.start, r.end]);
        body.trimmed_seconds = trim.trimmed;
        // legacy 字段保留(后端会自动转,但前端也写一份方便审计)
        body.trim_start = trim.start;
        body.trim_end = trim.end;
      }
      const r = await fetch(`${API_BASE}/api/video/clone-v2/create`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", ...token() },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "提交失败");
      setSubmitStatus("processing");
      pollJob(d.job_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitStatus("idle");
    }
  }

  // ─── 轮询 job ───
  function pollJob(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/video/clone-v2/jobs/${jobId}`, {
          credentials: "include",
          headers: token(),
        });
        const d: JobView = await r.json();
        if (!r.ok) {
          setError(`任务查询失败:${(d as unknown as { detail: string }).detail}`);
          if (pollRef.current) clearInterval(pollRef.current);
          return;
        }
        setJob(d);
        if (["completed", "failed", "refunded", "cancelled"].includes(d.status)) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setSubmitStatus(d.status === "completed" ? "completed" : "failed");
          if (d.status !== "completed") {
            setError(d.error || `任务 ${d.status}`);
          }
        }
      } catch {
        // 静默继续轮询
      }
    };
    tick();
    pollRef.current = setInterval(tick, 4000);
  }

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function reset() {
    if (pollRef.current) clearInterval(pollRef.current);
    setVideo(null); setImages([]); setPrompt(""); setEstimate(null);
    setPreview(null); setSubmitStatus("idle"); setError("");
    setDisclaimerChecked(false); setJob(null); setShowRawConfirm(false);
    setRawAck1(false); setRawAck2(false); setRawAck3(false);
    setTrim(null); setTrimPopup(null); setDropRanges([]); setSegSelections({});
    setReplacementMode("full");
  }

  // ─── UI ───
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>AI 创作工具</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>
            视频<span style={{ fontStyle: "italic" }}> 复刻 V2</span> · Seedance r2v 双版本下载
          </h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            支持 4-64 秒视频 · ¥19.9 / 段 · 输出含 xiaoLi ai · AI 生成水印 + 无标识版自选下载
          </div>
        </div>

        {/* Step 1:上传视频 */}
        <Section title="1. 上传参考视频(MP4/MOV,≤50MB,4-64 秒)">
          {!video && (
            <div style={{ background: "#f0f9ff", border: "1px solid #bae6fd", borderRadius: 8, padding: "0.7rem 1rem", marginBottom: 12, fontSize: "0.85rem", color: "#075985", lineHeight: 1.6 }}>
              <b>💡 效果小贴士</b>:建议上传<b>单镜头视频</b>(无明显切换),效果最稳定。多镜头视频在切换处个别帧可能效果欠佳。
            </div>
          )}
          {!video ? (
            <FileInput
              accept="video/*"
              disabled={uploadingVideo}
              label={uploadingVideo ? "上传中..." : "选择视频"}
              onFile={handleVideoUpload}
            />
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <video src={video.url} controls style={{ width: 200, borderRadius: 8, background: "#000" }} />
              <div style={{ fontSize: "0.85rem", color: "#666" }}>
                时长:{video.duration.toFixed(1)} 秒
                {trim && (
                  <div style={{ fontSize: "0.78rem", color: "#a16207", marginTop: 4 }}>
                    已裁剪保留 [{trim.start.toFixed(1)}s, {trim.end.toFixed(1)}s]<br />
                    丢弃 {trim.trimmed.toFixed(1)} 秒
                  </div>
                )}
                <br />
                <button onClick={() => { setVideo(null); setPreview(null); setTrim(null); setTrimPopup(null); }} style={btnLink}>更换</button>
              </div>
            </div>
          )}
        </Section>

        {/* Step 2:上传参考图(产品 / 人物 / 场景 各 0-3 张,总上限 9 张对齐 fal) */}
        <Section title="2. 上传参考图(产品 / 人物 / 场景 各 0-3 张)">
          {(["product", "person", "scene"] as Role[]).map((role) => {
            const roleImages = images.map((img, idx) => ({ img, idx })).filter((x) => x.img.role === role);
            return (
              <div key={role} style={{ marginBottom: 16, paddingBottom: 12, borderBottom: "1px dashed #e5e7eb" }}>
                <div style={{ fontSize: "0.85rem", fontWeight: 600, color: "#374151", marginBottom: 8 }}>
                  {ROLE_LABELS[role]}图 <span style={{ fontWeight: 400, color: "#9ca3af", fontSize: "0.78rem" }}>({roleImages.length}/3)</span>
                </div>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
                  {roleImages.map((x, i) => (
                    <div key={x.idx} style={{ position: "relative" }}>
                      <img src={x.img.url} alt={x.img.filename} style={{ width: 80, height: 80, objectFit: "cover", borderRadius: 8, border: "1px solid #ddd" }} />
                      <div style={{ fontSize: "0.7rem", color: "#666", marginTop: 4, textAlign: "center" }}>@{ROLE_LABELS[role]}{i + 1}</div>
                      <button onClick={() => setImages((arr) => arr.filter((_, j) => j !== x.idx))} style={{ position: "absolute", top: -6, right: -6, background: "#fff", border: "1px solid #ddd", borderRadius: "50%", width: 20, height: 20, cursor: "pointer", fontSize: "0.7rem" }}>✕</button>
                    </div>
                  ))}
                  {roleImages.length < 3 && (
                    <FileInput
                      accept="image/*"
                      disabled={uploadingImage}
                      label={uploadingImage ? "上传中..." : `+ 添加${ROLE_LABELS[role]}图`}
                      onFile={(f) => handleImageUpload(f, role)}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </Section>

        {/* 2026-05-10 砍单档:single 模式不再需要档位选择 UI(整段删除)
            用户上传 ≤ 8s 视频走 single → 直接进 Step 4 prompt + 模板
            单价显示在 Step 4 价格预估里(SEGMENT_PRICE.rmb)
            Step 编号重排留给 commit 3.5 单独 commit */}

        {/* Step 3 - ultimate:多段独立选档 + replacement_mode */}
        {preview && preview.type === "ultimate" && preview.segments.length > 0 && (
          <Section title={`3. 切片预览(${preview.segments.length} 段)+ 每段独立选档`}>
            <div style={{ marginBottom: 12, display: "flex", gap: 16, alignItems: "center", fontSize: "0.85rem", color: "#666" }}>
              <span>替换模式:</span>
              {(["full", "partial"] as ReplacementMode[]).map((m) => (
                <label key={m} style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                  <input
                    type="radio"
                    checked={replacementMode === m}
                    onChange={() => setReplacementMode(m)}
                  />
                  <span>{m === "full" ? "全方位(产品+模特+场景全替换)" : "局部(仅产品/部分细节)"}</span>
                </label>
              ))}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
              {preview.segments.map((s) => {
                const sel = segSelections[s.idx] ?? { source_type: "ai" as SourceType };
                return (
                  <div key={s.idx} style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10, padding: 12 }}>
                    {/* ⭐ 缩略图:点播放该 8s 段,直观看每段是啥再决定 AI/原 */}
                    {/* effective_time → original_time 映射:plan 用 effective 0-based,
                        视频元素需要 original 时间(因为 video.url 是用户上传的整片) */}
                    {video && (
                      <div style={{ marginBottom: 8 }}>
                        <SegmentPreview
                          src={video.url}
                          start={effectiveToOriginalTime(s.start, trim)}
                          duration={s.duration}
                          maxHeight={200}
                          fillWidth
                        />
                      </div>
                    )}

                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
                      <div style={{ fontWeight: 500, color: "#1e40af" }}>段 {s.idx + 1}</div>
                      <div style={{ fontSize: "0.78rem", color: "#999" }}>
                        {s.start.toFixed(1)}-{(s.start + s.duration).toFixed(1)}s · {s.duration.toFixed(1)}秒
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
                      {(["ai", "original"] as SourceType[]).map((st) => {
                        const selected = sel.source_type === st;
                        return (
                          <button
                            key={st}
                            onClick={() => updateSeg(s.idx, { source_type: st })}
                            style={{
                              flex: 1,
                              padding: "0.4rem 0.6rem",
                              border: selected ? "1.5px solid #2563eb" : "1px solid #ddd",
                              borderRadius: 6,
                              background: selected ? "#eff6ff" : "#fff",
                              cursor: "pointer",
                              fontSize: "0.78rem",
                            }}
                          >
                            {st === "ai" ? "AI 生成" : "原视频保留"}
                          </button>
                        );
                      })}
                    </div>

                    {/* 2026-05-10 砍单档:档位选择 UI 删除,改成动态单价显示 */}
                    <div style={{ fontSize: "0.78rem", marginTop: 4 }}>
                      {sel.source_type === "ai" ? (
                        <span style={{ color: "#666" }}>
                          {SEGMENT_PRICE.label} · ¥{SEGMENT_PRICE.rmb} / 段
                        </span>
                      ) : (
                        <span style={{ color: "#999" }}>
                          保留原视频 · 免费
                        </span>
                      )}
                    </div>

                    {sel.source_type === "original" && (
                      <div style={{ fontSize: "0.75rem", color: "#999", marginTop: 4 }}>
                        段保留原视频,不调 AI,不扣积分
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </Section>
        )}

        {/* Step 4:Prompt */}
        {video && (
          <Section title="4. 提示词(写大白话 → AI 优化 / 选模板 / 手动改)">
            {/* 2026-05-11 目标市场 toggle:CN 国内→中文 prompt / Global 海外→英文 prompt */}
            <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 10 }}>
              <label style={{ fontSize: "0.85rem", color: "#444", fontWeight: 500 }}>目标市场:</label>
              <div style={{ display: "flex", gap: 0, border: "1px solid #d1d5db", borderRadius: 8, overflow: "hidden" }}>
                {(["CN", "Global"] as const).map((r) => (
                  <button
                    key={r}
                    onClick={() => setRegion(r)}
                    style={{
                      padding: "0.4rem 0.9rem",
                      border: "none",
                      background: region === r ? "#2563eb" : "#fff",
                      color: region === r ? "#fff" : "#374151",
                      cursor: "pointer",
                      fontSize: "0.85rem",
                      fontWeight: region === r ? 500 : 400,
                    }}
                  >
                    {r === "CN" ? "国内抖音(中文)" : "海外 TikTok(英文)"}
                  </button>
                ))}
              </div>
              <span style={{ fontSize: "0.78rem", color: "#9ca3af" }}>
                AI 优化时会按此切换 prompt 语言
              </span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
              {templates.map((t) => (
                <button key={t.id} onClick={() => applyTemplate(t)} style={chipBtn}>{t.label}</button>
              ))}
            </div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="写大白话:想要视频里什么换成什么。例:想把视频里的裤子换成上传的款式 / 把人物换掉。点 ✨ AI 优化 自动转成专业 prompt"
              style={{ width: "100%", minHeight: 80, padding: 10, border: "1px solid #ddd", borderRadius: 8, fontSize: "0.9rem", fontFamily: "inherit", resize: "vertical" }}
            />
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              <button
                onClick={handleAiOptimize}
                disabled={aiOptimizing || !prompt.trim()}
                style={{
                  padding: "0.5rem 1rem",
                  borderRadius: 8,
                  border: "none",
                  background: aiOptimizing || !prompt.trim() ? "#94a3b8" : "#7c3aed",
                  color: "#fff",
                  cursor: aiOptimizing || !prompt.trim() ? "not-allowed" : "pointer",
                  fontSize: "0.85rem",
                  fontWeight: 500,
                }}
              >
                {aiOptimizing ? "优化中..." : "✨ AI 优化"}
              </button>
              <span style={{ fontSize: "0.78rem", color: "#9ca3af" }}>
                写完想法,点这里让 AI 帮你转成最佳 prompt
              </span>
            </div>
          </Section>
        )}

        {/* Step 5:价格汇总 + 声明 + 提交 */}
        {estimate && (
          <Section title="5. 价格汇总 + 上传声明">
            <div style={{ background: "#f0f7fb", padding: "1rem 1.2rem", borderRadius: 10, fontSize: "0.95rem", color: "#456", marginBottom: 12 }}>
              <div>段数:<b>{estimate.total_segments}</b> · AI 段:<b>{estimate.ai_segments_count}</b></div>
              <div style={{ fontSize: "1.3rem", color: "#2563eb", margin: "8px 0" }}>
                总价 <b>¥{estimate.total_rmb_display}</b> · 实扣 <b>{estimate.total_credits}</b> 积分
              </div>
              <div style={{ fontSize: "0.8rem", color: "#789" }}>预计耗时:{estimate.estimated_minutes} 分钟</div>
            </div>

            <div style={{ background: "#fffbea", border: "1px solid #fde68a", padding: "1rem 1.2rem", borderRadius: 10, marginBottom: 12, fontSize: "0.85rem", lineHeight: 1.7 }}>
              <div style={{ fontWeight: 500, marginBottom: 6 }}>📜 视频复刻 V2 上传声明书(摘要)</div>
              <div style={{ color: "#666" }}>
                提交即承诺:上传内容合法合规、不涉及禁止类别(明星脸/政治人物/未成年人/品牌侵权/违禁商品/恶意 deepfake);若使用他人形象已取得书面同意;平台不做技术审核,我是合规第一责任人;违规导致行政/民事/刑事责任全额自担。
              </div>
              <div style={{ marginTop: 6 }}>
                完整条款:<a href="/legal/video-clone-v2-disclaimer" target="_blank" style={{ color: "#2563eb" }}>查看完整声明书 →</a>
              </div>
            </div>

            <label style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: "0.9rem", marginBottom: 14, cursor: "pointer" }}>
              <input type="checkbox" checked={disclaimerChecked} onChange={(e) => setDisclaimerChecked(e.target.checked)} style={{ marginTop: 4 }} />
              <span>我已阅读并同意《视频复刻 V2 上传声明书》全部 7 项承诺 + 5 项告知</span>
            </label>

            {error && (
              <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", color: "#991b1b", padding: "0.8rem 1rem", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>{error}</div>
            )}

            <button
              onClick={handleSubmit}
              disabled={submitStatus !== "idle" || !disclaimerChecked || !estimate || !prompt.trim()}
              style={{
                ...primaryBtn,
                opacity: submitStatus === "idle" && disclaimerChecked && prompt.trim() ? 1 : 0.4,
                cursor: submitStatus === "idle" && disclaimerChecked && prompt.trim() ? "pointer" : "not-allowed",
              }}
            >
              {submitStatus === "submitting" ? "提交中..." :
                submitStatus === "processing" ? "生成中..." :
                  `确认生成(¥${estimate.total_rmb_display})`}
            </button>
          </Section>
        )}

        {/* Step 6:任务进度 */}
        {job && submitStatus === "processing" && (
          <Section title="6. 生成中">
            <div style={{ fontSize: "0.9rem", color: "#666", marginBottom: 8 }}>
              进度:{job.progress.completed} / {job.progress.total_ai + job.progress.total_original}
            </div>
            <div style={{ height: 8, background: "#e5e7eb", borderRadius: 4, overflow: "hidden" }}>
              <div style={{
                width: `${(job.progress.completed / Math.max(1, job.progress.total_ai + job.progress.total_original)) * 100}%`,
                height: "100%", background: "#2563eb", transition: "width 0.5s",
              }} />
            </div>
            <div style={{ fontSize: "0.78rem", color: "#999", marginTop: 8 }}>状态:{job.status}</div>
          </Section>
        )}

        {/* Step 7:完成,双版本下载 */}
        {job && submitStatus === "completed" && (
          <Section title="✅ 生成完成">
            <video src={job.final_video_url_watermarked || job.final_video_url || ""} controls style={{ width: "100%", maxWidth: 480, borderRadius: 10, background: "#000", marginBottom: 16 }} />

            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
              <a href={job.final_video_url_watermarked || ""} download style={primaryBtn}>
                ⬇ 下载合规版(带 AI 水印)
              </a>
              <button onClick={() => setShowRawConfirm(true)} style={{ ...primaryBtn, background: "#666" }}>
                下载无标识版(需声明)
              </button>
              <button onClick={reset} style={btnLink}>重新生成</button>
            </div>

            {showRawConfirm && (
              <div style={{ background: "#fef2f2", border: "1px solid #fca5a5", padding: "1rem 1.2rem", borderRadius: 10, fontSize: "0.85rem", lineHeight: 1.7 }}>
                <div style={{ fontWeight: 500, marginBottom: 8, color: "#991b1b" }}>⚠️ 下载无标识版前请逐项勾选</div>
                <label style={{ display: "flex", gap: 6, marginBottom: 6, cursor: "pointer" }}>
                  <input type="checkbox" checked={rawAck1} onChange={(e) => setRawAck1(e.target.checked)} />
                  <span>我理解此视频不含 AI 标识水印</span>
                </label>
                <label style={{ display: "flex", gap: 6, marginBottom: 6, cursor: "pointer" }}>
                  <input type="checkbox" checked={rawAck2} onChange={(e) => setRawAck2(e.target.checked)} />
                  <span>我承诺在传播时自行添加 AI 标识符合《互联网信息服务深度合成管理规定》第 17 条</span>
                </label>
                <label style={{ display: "flex", gap: 6, marginBottom: 10, cursor: "pointer" }}>
                  <input type="checkbox" checked={rawAck3} onChange={(e) => setRawAck3(e.target.checked)} />
                  <span>因未加标识公开传播产生的行政、民事、刑事责任由我全额承担,平台不负任何连带责任</span>
                </label>
                <a
                  href={rawAck1 && rawAck2 && rawAck3 ? job.final_video_url_raw || undefined : undefined}
                  download={rawAck1 && rawAck2 && rawAck3}
                  onClick={(e) => { if (!(rawAck1 && rawAck2 && rawAck3)) e.preventDefault(); }}
                  style={{
                    ...primaryBtn,
                    background: rawAck1 && rawAck2 && rawAck3 ? "#dc2626" : "#999",
                    pointerEvents: rawAck1 && rawAck2 && rawAck3 ? "auto" : "none",
                    opacity: rawAck1 && rawAck2 && rawAck3 ? 1 : 0.5,
                  }}
                >
                  确认下载无标识版
                </a>
              </div>
            )}
          </Section>
        )}

        {submitStatus === "failed" && (
          <Section title="❌ 生成失败">
            <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", color: "#991b1b", padding: "0.8rem 1rem", borderRadius: 8, fontSize: "0.85rem" }}>
              {error || "任务失败,积分已退还"}
            </div>
            <button onClick={reset} style={{ ...primaryBtn, marginTop: 12 }}>重试</button>
          </Section>
        )}

        {error && submitStatus === "idle" && !trimPopup && (
          <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", color: "#991b1b", padding: "0.8rem 1rem", borderRadius: 8, fontSize: "0.85rem", marginTop: 12 }}>{error}</div>
        )}

        {/* check-duration trim 弹窗:多段 drop ranges 编辑 */}
        {trimPopup && (
          <TrimMultiPopup
            trimPopup={trimPopup}
            video={video}
            dropRanges={dropRanges}
            setDropRanges={setDropRanges}
            onPickRecommended={pickRecommended}
            onConfirm={applyMultiTrim}
            onCancel={() => { setTrimPopup(null); setVideo(null); setDropRanges([]); }}
          />
        )}

      </main>
    </div>
  );
}

// ─── 小组件 ─────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: "#fff", borderRadius: 14, padding: "1.4rem 1.6rem", marginBottom: "1.2rem" }}>
      <div style={{ fontSize: "0.92rem", color: "#333", marginBottom: "0.9rem", fontWeight: 500 }}>{title}</div>
      {children}
    </div>
  );
}

// 把切片后的 effective 时间映射回原视频时间(用于 <video currentTime>)
// trim 是 DROP 区间 [trim.start, trim.end]
//   - 无 trim:effective = original
//   - effective time < drop_start:落在第一段 keep [0, drop_start],original = effective
//   - effective time >= drop_start:落在第二段 keep [drop_end, full],original = effective - drop_start + drop_end
function effectiveToOriginalTime(
  t: number,
  trim: { start: number; end: number; trimmed: number } | null,
): number {
  if (!trim) return t;
  if (t < trim.start) return t;
  return t - trim.start + trim.end;
}

// 视频预览:HTML5 video 同步到指定 start 时间,点击播放该段(duration 秒后自动暂停回首帧)
// 保持原始宽高比:objectFit=contain 保不变形;
// fillWidth=true → 容器固定撑满父级宽度,9:16 加 letterbox 黑边(段卡片用,网格视觉一致)
// fillWidth=false → 容器跟视频原生宽度走(主预览弹窗用,无黑边)
function SegmentPreview({
  src, start, duration, maxHeight = 200, fillWidth = false,
}: { src: string; start: number; duration: number; maxHeight?: number; fillWidth?: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [playing, setPlaying] = useState(false);

  // 视频元数据加载完(或已加载) → 把 currentTime 跳到 start
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const setStart = () => { try { v.currentTime = start; } catch {} };
    if (v.readyState >= 1) {
      setStart();
    } else {
      v.addEventListener("loadedmetadata", setStart, { once: true });
      return () => v.removeEventListener("loadedmetadata", setStart);
    }
  }, [src, start]);

  function togglePlay() {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      try { v.currentTime = start; } catch {}
      v.play().catch(() => {});
      setPlaying(true);
      if (stopTimerRef.current) clearTimeout(stopTimerRef.current);
      stopTimerRef.current = setTimeout(() => {
        v.pause();
        try { v.currentTime = start; } catch {}
        setPlaying(false);
      }, Math.max(500, duration * 1000));
    } else {
      v.pause();
      try { v.currentTime = start; } catch {}
      setPlaying(false);
      if (stopTimerRef.current) clearTimeout(stopTimerRef.current);
    }
  }

  useEffect(() => () => {
    if (stopTimerRef.current) clearTimeout(stopTimerRef.current);
  }, []);

  return (
    <div
      onClick={togglePlay}
      style={{
        position: "relative", cursor: "pointer", borderRadius: 6,
        overflow: "hidden", background: "#000",
        display: "flex", justifyContent: "center", alignItems: "center",
        // 防 metadata 没加载完时容器塌缩,给个 min-height
        minHeight: 60,
      }}
    >
      <video
        ref={videoRef}
        src={src}
        muted
        preload="metadata"
        playsInline
        style={{
          width: fillWidth ? "100%" : "auto",
          maxWidth: "100%",
          height: fillWidth ? "auto" : "auto",
          maxHeight,
          objectFit: "contain",
          display: "block",
          background: "#000",
        }}
      />
      {!playing && (
        <div style={{
          position: "absolute", inset: 0, display: "flex",
          alignItems: "center", justifyContent: "center", pointerEvents: "none",
        }}>
          <div style={{
            background: "rgba(0,0,0,0.6)", color: "#fff", borderRadius: "50%",
            width: 32, height: 32, display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 13, paddingLeft: 3,
          }}>▶</div>
        </div>
      )}
    </div>
  );
}

// 多段 drop ranges 弹窗:剪映/Premiere 风格 — 时间轴拖圆点画选区,多颜色,选中跟预览
function TrimMultiPopup({
  trimPopup, video, dropRanges, setDropRanges, onPickRecommended, onConfirm, onCancel,
}: {
  trimPopup: CheckDurationResp;
  video: { url: string; duration: number; sha256: string } | null;
  dropRanges: Array<{ start: number; end: number }>;
  setDropRanges: (r: Array<{ start: number; end: number }>) => void;
  onPickRecommended: (picked: TrimCandidate) => void;
  onConfirm: (ranges: Array<{ start: number; end: number }>) => void;
  onCancel: () => void;
}) {
  const totalDuration = trimPopup.current_duration;
  const targetDrop = trimPopup.drop_seconds;
  const TOL = 0.05;

  // 当前选中的区(预览跟着这个;拖动 / 点列表行 / 点时间轴红块都会切)
  const [selectedIdx, setSelectedIdx] = useState(0);

  // 弹窗首次打开:默认单段(对齐"推荐位置")
  useEffect(() => {
    if (dropRanges.length === 0) {
      const recommended = trimPopup.suggestions.find((s) => s.recommended) ?? trimPopup.suggestions[0];
      if (recommended) {
        setDropRanges([{ start: recommended.start, end: recommended.end }]);
      } else {
        const start = Math.max(0, (totalDuration - targetDrop) / 2);
        setDropRanges([{ start, end: start + targetDrop }]);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 区数量缩了 → selectedIdx 修复
  useEffect(() => {
    if (selectedIdx >= dropRanges.length && dropRanges.length > 0) {
      setSelectedIdx(0);
    }
  }, [dropRanges.length, selectedIdx]);

  // 重叠允许 — 总丢弃按并集算(重叠部分只算一次),跟后端 /create 内联实现等价
  const totalDropped = totalDroppedAsUnion(dropRanges);
  const isValid =
    dropRanges.length > 0
    && Math.abs(totalDropped - targetDrop) < TOL
    && dropRanges.every((r) => r.end > r.start && r.start >= 0 && r.end <= totalDuration + TOL);

  function deleteRange(i: number) {
    if (dropRanges.length <= 1) return;
    const next = dropRanges.filter((_, idx) => idx !== i);
    setDropRanges(next);
    if (selectedIdx >= next.length) setSelectedIdx(Math.max(0, next.length - 1));
  }
  function addRange() {
    if (dropRanges.length >= MAX_RANGES) return;
    // 计算所有 gap(包含首尾),挑最宽的那个,新区域占其中部
    const sorted = [...dropRanges].sort((a, b) => a.start - b.start);
    const gaps: Array<{ start: number; end: number }> = [];
    let cur = 0;
    for (const r of sorted) {
      if (r.start - cur > 0.05) gaps.push({ start: cur, end: r.start });
      cur = Math.max(cur, r.end);
    }
    if (totalDuration - cur > 0.05) gaps.push({ start: cur, end: totalDuration });
    if (gaps.length === 0) return; // 整片已被 drop 占满,不允许再加
    const widest = gaps.reduce((a, b) => (b.end - b.start > a.end - a.start ? b : a));
    const gapW = widest.end - widest.start;
    const remaining = Math.max(0, targetDrop - totalDropped);
    // 默认 1 秒,但夹紧到 gap 宽度的 80%(留 0.1 边)和剩余还需丢弃额度
    const want = Math.min(1.0, Math.max(0.3, remaining));
    const w = Math.min(want, gapW - 0.2);
    if (w < 0.2) return; // gap 太窄
    const start = widest.start + 0.1 + (gapW - 0.2 - w) / 2;
    setDropRanges([...dropRanges, { start, end: start + w }]);
    setSelectedIdx(dropRanges.length);
  }

  // 预览跟选中区
  const sel = dropRanges[selectedIdx] ?? dropRanges[0];
  const previewStart = sel?.start ?? 0;
  const previewDuration = sel ? Math.max(0, sel.end - sel.start) : 0;
  const selColor = RANGE_COLORS[selectedIdx % RANGE_COLORS.length];

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
      padding: "1rem",
    }}>
      <div style={{ background: "#fff", borderRadius: 14, padding: "1.6rem 1.8rem", maxWidth: 760, width: "100%", maxHeight: "92vh", overflowY: "auto" }}>
        <div style={{ fontSize: "1.05rem", fontWeight: 500, marginBottom: 6 }}>
          视频 {totalDuration.toFixed(1)} 秒不是 8 秒整数倍
        </div>
        <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: 14, lineHeight: 1.5 }}>
          需要丢弃 <b>{targetDrop.toFixed(1)} 秒</b> 让总时长 = <b>{trimPopup.target_duration.toFixed(0)} 秒</b>
          <br /><span style={{ color: "#999", fontSize: "0.78rem" }}>时间轴上拖圆点画选区,可加多段(最多 {MAX_RANGES} 段),总和等于 {targetDrop.toFixed(1)} 秒就行</span>
        </div>

        {/* 视频预览:跟选中区同步,保持原始比例 */}
        {video && previewDuration > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: "0.82rem", color: "#666", marginBottom: 5 }}>
              当前预览:
              <span style={{ color: selColor, fontWeight: 500, marginLeft: 4 }}>
                ● 区 {selectedIdx + 1}
              </span>
              <span style={{ color: "#666", marginLeft: 6 }}>
                ({sel.start.toFixed(1)}s – {sel.end.toFixed(1)}s · {previewDuration.toFixed(1)}s)
              </span>
            </div>
            <SegmentPreview
              key={selectedIdx}  // 切区时强制重挂载,避免 video tag 旧 currentTime 残留
              src={video.url}
              start={previewStart}
              duration={previewDuration}
              maxHeight={280}
            />
          </div>
        )}

        {/* ⭐ 时间轴 + 拖动圆点 */}
        <DraggableTimeline
          totalDuration={totalDuration}
          ranges={dropRanges}
          selectedIdx={selectedIdx}
          onChange={setDropRanges}
          onSelect={setSelectedIdx}
        />

        {/* 推荐位置(一键预填,替换为单段) */}
        <div style={{ marginTop: 14, marginBottom: 12 }}>
          <div style={{ fontSize: "0.82rem", color: "#666", marginBottom: 6 }}>
            推荐位置(一键替换为单段;再继续编辑/加段):
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {trimPopup.suggestions.map((s, i) => (
              <button
                key={i}
                onClick={() => { onPickRecommended(s); setSelectedIdx(0); }}
                style={{
                  padding: "0.45rem 0.8rem",
                  border: s.recommended ? "1.5px solid #16a34a" : "1px solid #ddd",
                  borderRadius: 6,
                  background: s.recommended ? "#f0fdf4" : "#fff",
                  fontSize: "0.78rem", cursor: "pointer",
                }}
              >
                {s.label}{s.recommended ? " 👍" : ""}
                <span style={{ color: "#999", marginLeft: 4 }}>
                  {s.motion_score >= 0 ? `${s.motion_score.toFixed(1)}` : "—"}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* 区域列表 — 点行切换选中,删除按钮 */}
        <div style={{ background: "#f7f7f5", border: "1px solid #d4d4d4", padding: "0.9rem 1.1rem", borderRadius: 10, marginBottom: 14 }}>
          <div style={{ fontSize: "0.82rem", color: "#666", marginBottom: 8 }}>
            丢弃区域列表(点行切换预览):
          </div>
          {dropRanges.map((r, i) => {
            const color = RANGE_COLORS[i % RANGE_COLORS.length];
            const isSel = selectedIdx === i;
            return (
              <div
                key={i}
                onClick={() => setSelectedIdx(i)}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  background: isSel ? "#eff6ff" : "#fff",
                  border: isSel ? "2px solid #2563eb" : "1px solid #e5e7eb",
                  borderRadius: 8, padding: "0.55rem 0.8rem", marginBottom: 6,
                  cursor: "pointer",
                  fontSize: "0.85rem",
                }}
              >
                <span style={{
                  display: "inline-block", width: 12, height: 12, borderRadius: "50%",
                  background: color, flexShrink: 0,
                }} />
                <span style={{ fontWeight: 500, color, minWidth: 36 }}>区 {i + 1}</span>
                <span style={{ flex: 1, color: "#444" }}>
                  {r.start.toFixed(1)}s – {r.end.toFixed(1)}s
                  <span style={{ color: "#999", marginLeft: 6 }}>· 宽 {(r.end - r.start).toFixed(1)}s</span>
                </span>
                {dropRanges.length > 1 && (
                  <button
                    onClick={(e) => { e.stopPropagation(); deleteRange(i); }}
                    style={{ ...btnLink, padding: "0.2rem 0.5rem", color: "#dc2626", fontSize: "0.78rem" }}
                  >
                    删除
                  </button>
                )}
              </div>
            );
          })}
          {dropRanges.length < MAX_RANGES && (
            <button
              onClick={addRange}
              style={{
                ...btnLink, padding: "0.45rem 0.8rem", border: "1px dashed #999",
                color: "#444", display: "block", width: "100%", textAlign: "center",
                marginTop: 4,
              }}
            >
              + 添加新区域(还可加 {MAX_RANGES - dropRanges.length} 个)
            </button>
          )}
        </div>

        {/* 总数显示 */}
        <div style={{
          padding: "0.7rem 1rem", borderRadius: 8, marginBottom: 14,
          background: isValid ? "#f0fdf4" : "#fef3c7",
          border: `1px solid ${isValid ? "#86efac" : "#fde68a"}`,
          fontSize: "0.85rem",
        }}>
          总丢弃:<b>{totalDropped.toFixed(1)} 秒</b> / 目标 <b>{targetDrop.toFixed(1)} 秒</b>
          {" "}
          {isValid ? "✅" : (totalDropped < targetDrop
            ? `(还差 ${(targetDrop - totalDropped).toFixed(1)} 秒)`
            : `(超 ${(totalDropped - targetDrop).toFixed(1)} 秒)`)}
        </div>

        {/* 操作 */}
        <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
          <button onClick={onCancel} style={{ ...btnLink, padding: "0.65rem 1.1rem" }}>
            取消(换个视频)
          </button>
          <button
            onClick={() => isValid && onConfirm(dropRanges)}
            disabled={!isValid}
            style={{
              ...primaryBtn,
              background: "#dc2626",
              opacity: isValid ? 1 : 0.5,
              cursor: isValid ? "pointer" : "not-allowed",
            }}
          >
            确认丢弃这 {targetDrop.toFixed(1)} 秒
          </button>
        </div>
      </div>
    </div>
  );
}

// 剪映风格的可拖动时间轴:每段一对圆点 + 中间填充色,拖圆点改起止
function DraggableTimeline({
  totalDuration, ranges, selectedIdx, onChange, onSelect,
}: {
  totalDuration: number;
  ranges: Array<{ start: number; end: number }>;
  selectedIdx: number;
  onChange: (r: Array<{ start: number; end: number }>) => void;
  onSelect: (i: number) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ rangeIdx: number; endpoint: "start" | "end" } | null>(null);
  // 用 ref 持最新 ranges/onChange,避免 pointermove 监听每帧重绑导致 race
  const rangesRef = useRef(ranges);
  const onChangeRef = useRef(onChange);
  const totalDurationRef = useRef(totalDuration);
  useEffect(() => { rangesRef.current = ranges; }, [ranges]);
  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);
  useEffect(() => { totalDurationRef.current = totalDuration; }, [totalDuration]);

  function clientXToTime(clientX: number): number {
    const t = trackRef.current;
    if (!t) return 0;
    const rect = t.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    const rel = (clientX - rect.left) / rect.width;
    const td = totalDurationRef.current;
    return Math.max(0, Math.min(td, rel * td));
  }

  // 全局 pointer 监听(用户可能拖出时间轴范围)— 挂一次,通过 ref 读最新值
  // 重叠允许 → 不再拦邻区,只夹在自己 start/end 间 + track [0, totalDuration]
  useEffect(() => {
    function handleMove(e: PointerEvent) {
      const d = dragRef.current;
      if (!d) return;
      e.preventDefault();
      const t = clientXToTime(e.clientX);
      const cur = rangesRef.current;
      const next = cur.map((r, idx) => {
        if (idx !== d.rangeIdx) return r;
        if (d.endpoint === "start") {
          // 起点:0 ↗ 自己 end-0.1
          return { ...r, start: Math.max(0, Math.min(t, r.end - 0.1)) };
        } else {
          // 终点:自己 start+0.1 ↘ totalDuration
          return { ...r, end: Math.max(r.start + 0.1, Math.min(t, totalDurationRef.current)) };
        }
      });
      onChangeRef.current(next);
    }
    function handleUp() { dragRef.current = null; }
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    window.addEventListener("pointercancel", handleUp);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      window.removeEventListener("pointercancel", handleUp);
    };
  }, []);

  function handleDotDown(rangeIdx: number, endpoint: "start" | "end", e: React.PointerEvent) {
    e.preventDefault();
    e.stopPropagation();
    try { (e.target as HTMLElement).setPointerCapture(e.pointerId); } catch {}
    dragRef.current = { rangeIdx, endpoint };
    onSelect(rangeIdx);
  }

  const HEIGHT = 70;
  const TOP = 12;
  const BOTTOM_AXIS = 18;
  const tickStep = 4;
  const tickCount = Math.floor(totalDuration / tickStep);

  return (
    <div
      ref={trackRef}
      style={{
        position: "relative",
        width: "100%",
        height: HEIGHT,
        background: "#e5e7eb",
        borderRadius: 8,
        userSelect: "none",
        touchAction: "none",
      }}
    >
      {/* 4s 间隔刻度线 + 数字标尺 */}
      {Array.from({ length: tickCount + 1 }).map((_, i) => {
        const t = i * tickStep;
        // 末刻度跟"终点"距离 < 1.5s 时省掉,避免数字重叠
        const tooCloseToEnd = totalDuration - t < 1.5 && t > 0;
        if (tooCloseToEnd) return null;
        const leftPct = (t / totalDuration) * 100;
        return (
          <Fragment key={`tick-${i}`}>
            <div style={{
              position: "absolute",
              left: `${leftPct}%`,
              top: TOP, bottom: BOTTOM_AXIS,
              width: 1, background: "#cbd5e1",
              pointerEvents: "none",
            }} />
            <div style={{
              position: "absolute",
              left: `${leftPct}%`,
              bottom: 2,
              transform: i === 0 ? "translateX(0)" : "translateX(-50%)",
              fontSize: 10, color: "#999",
              pointerEvents: "none",
              whiteSpace: "nowrap",
            }}>
              {t}s
            </div>
          </Fragment>
        );
      })}
      {/* "终点"标签:固定在最右,跟最近的 4s 刻度让位 */}
      <div style={{
        position: "absolute",
        right: 2, bottom: 2,
        fontSize: 10, color: "#666",
        pointerEvents: "none",
        whiteSpace: "nowrap",
      }}>
        {totalDuration.toFixed(1)}s 终
      </div>

      {/* 各 drop 区:填充色块 + 起/止圆点 */}
      {ranges.map((r, i) => {
        const color = RANGE_COLORS[i % RANGE_COLORS.length];
        const startPct = Math.max(0, Math.min(100, (r.start / totalDuration) * 100));
        const endPct   = Math.max(0, Math.min(100, (r.end   / totalDuration) * 100));
        const widthPct = Math.max(0, endPct - startPct);
        const isSel = selectedIdx === i;
        return (
          <Fragment key={`r-${i}`}>
            {/* 填充色块(可点切换选中) */}
            <div
              onClick={() => onSelect(i)}
              style={{
                position: "absolute",
                left: `${startPct}%`,
                width: `${widthPct}%`,
                top: TOP, bottom: BOTTOM_AXIS,
                background: color,
                opacity: isSel ? 0.95 : 0.55,
                outline: isSel ? "2px solid #fff" : "none",
                cursor: "pointer",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "#fff", fontSize: "0.78rem", fontWeight: 600,
              }}
            >
              {i + 1}
            </div>

            {/* 起点圆点 */}
            <div
              onPointerDown={(e) => handleDotDown(i, "start", e)}
              title={`区 ${i + 1} 起点`}
              style={{
                position: "absolute",
                left: `${startPct}%`,
                top: `${TOP + (HEIGHT - TOP - BOTTOM_AXIS) / 2 - 8}px`,
                marginLeft: -8,
                width: 16, height: 16,
                borderRadius: "50%",
                background: color,
                border: "2.5px solid #fff",
                boxShadow: "0 2px 4px rgba(0,0,0,0.45)",
                cursor: "grab",
                touchAction: "none",
                zIndex: 10,
              }}
            />

            {/* 终点圆点 */}
            <div
              onPointerDown={(e) => handleDotDown(i, "end", e)}
              title={`区 ${i + 1} 终点`}
              style={{
                position: "absolute",
                left: `${endPct}%`,
                top: `${TOP + (HEIGHT - TOP - BOTTOM_AXIS) / 2 - 8}px`,
                marginLeft: -8,
                width: 16, height: 16,
                borderRadius: "50%",
                background: color,
                border: "2.5px solid #fff",
                boxShadow: "0 2px 4px rgba(0,0,0,0.45)",
                cursor: "grab",
                touchAction: "none",
                zIndex: 10,
              }}
            />
          </Fragment>
        );
      })}

    </div>
  );
}

// 总丢弃 = ranges 的并集时长(重叠部分只算一次),
// 跟后端 /create 内联实现(merge 后 sum)等价 — 两端口径必须一致
function totalDroppedAsUnion(ranges: Array<{ start: number; end: number }>): number {
  if (ranges.length === 0) return 0;
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  const merged: Array<{ start: number; end: number }> = [];
  for (const r of sorted) {
    if (merged.length === 0 || r.start > merged[merged.length - 1].end + 0.05) {
      merged.push({ ...r });
    } else {
      merged[merged.length - 1].end = Math.max(merged[merged.length - 1].end, r.end);
    }
  }
  return merged.reduce((s, r) => s + (r.end - r.start), 0);
}

// 时间轴 + 滑块:可视化丢弃区间位置,native range slider 控制(触屏+键盘免费)
function DragTrack({
  totalDuration, dropDuration, value, onChange,
}: {
  totalDuration: number;
  dropDuration: number;
  value: { start: number; end: number } | null;
  onChange: (v: { start: number; end: number }) => void;
}) {
  // 首次挂载:把丢弃区间放在视频中间(中性默认)
  useEffect(() => {
    if (!value) {
      const start = Math.max(0, (totalDuration - dropDuration) / 2);
      onChange({ start, end: start + dropDuration });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!value) return <div style={{ height: 80 }} />;

  const TRACK_HEIGHT = 44;
  const startPct = (value.start / totalDuration) * 100;
  const widthPct = (dropDuration / totalDuration) * 100;
  const tickStep = 4;
  const tickCount = Math.floor(totalDuration / tickStep);
  const startMax = Math.max(0, totalDuration - dropDuration);

  return (
    <div>
      {/* 视觉时间轴(只读显示当前选区位置) */}
      <div
        style={{
          position: "relative",
          width: "100%",
          height: TRACK_HEIGHT,
          background: "#e5e7eb",
          borderRadius: 8,
          overflow: "hidden",
          userSelect: "none",
        }}
      >
        {/* 4s 间隔刻度线(对齐 8s 段切片节奏) */}
        {Array.from({ length: tickCount + 1 }).map((_, i) => (
          <div key={i} style={{
            position: "absolute",
            left: `${((i * tickStep) / totalDuration) * 100}%`,
            top: 0, bottom: 16, width: 1, background: "#cbd5e1",
          }} />
        ))}

        {/* 红色丢弃区(只读显示 — 拖滑块来移动) */}
        <div
          style={{
            position: "absolute",
            left: `${startPct}%`,
            width: `${widthPct}%`,
            top: 0, bottom: 16,
            background: "#dc2626",
            opacity: 0.85,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
            fontSize: "0.78rem",
            fontWeight: 500,
            transition: "left 0.1s linear",
          }}
        >
          丢弃
        </div>

        {/* 时间轴底注 */}
        <div style={{
          position: "absolute", bottom: 0, left: 0, right: 0,
          height: 16, display: "flex", justifyContent: "space-between",
          padding: "0 6px", fontSize: "0.7rem", color: "#666",
          pointerEvents: "none", alignItems: "center",
        }}>
          <span>0s</span>
          <span>{totalDuration.toFixed(1)}s</span>
        </div>
      </div>

      {/* 滑块:native range,触屏+键盘+鼠标全免费 */}
      <input
        type="range"
        min={0}
        max={startMax}
        step={0.1}
        value={value.start}
        onChange={(e) => {
          const s = parseFloat(e.target.value);
          onChange({ start: s, end: s + dropDuration });
        }}
        style={{ width: "100%", marginTop: 10, accentColor: "#dc2626" }}
        aria-label="拖动选择丢弃区间起点"
      />
    </div>
  );
}

function FileInput({
  accept, disabled, label, onFile,
}: {
  accept: string; disabled: boolean; label: string; onFile: (f: File) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        onClick={() => ref.current?.click()}
        disabled={disabled}
        style={{
          padding: "0.6rem 1.2rem",
          border: "1px solid #ddd",
          borderRadius: 8,
          background: "#fff",
          cursor: disabled ? "not-allowed" : "pointer",
          fontSize: "0.9rem",
        }}
      >
        {label}
      </button>
      <input
        ref={ref}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </>
  );
}

const primaryBtn: React.CSSProperties = {
  padding: "0.7rem 1.4rem",
  border: "none",
  borderRadius: 10,
  background: "#2563eb",
  color: "#fff",
  fontSize: "0.95rem",
  fontWeight: 500,
  cursor: "pointer",
  textDecoration: "none",
  display: "inline-block",
};
const btnLink: React.CSSProperties = {
  padding: "0.4rem 0.8rem",
  border: "1px solid transparent",
  borderRadius: 6,
  background: "transparent",
  color: "#2563eb",
  fontSize: "0.85rem",
  cursor: "pointer",
};
const chipBtn: React.CSSProperties = {
  padding: "0.4rem 0.9rem",
  border: "1px solid #ddd",
  borderRadius: 16,
  background: "#fff",
  fontSize: "0.82rem",
  cursor: "pointer",
};
const selectStyle: React.CSSProperties = {
  padding: "0.4rem 0.6rem",
  border: "1px solid #ddd",
  borderRadius: 6,
  fontSize: "0.9rem",
  background: "#fff",
};
