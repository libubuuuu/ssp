"""人脸打码子进程 worker —— 由 video_clone_v2 以 `nice` 低优先级子进程调用。

为什么是子进程(而非线程):服务器只有 2 vCPU + 3.6GB 内存,打码 CPU 密集(60s 视频 ~3.5min)。
放子进程里:① nice 降优先级,不饿死线上 uvicorn;② 进程退出后内存全数回收,杜绝 uvicorn 长跑泄漏;
③ onnxruntime/opencv 万一段错误也炸不到主进程。调用方用全局 semaphore=1 保证同时只有一个在跑。

用法: python -m app.services.face_mask_worker <in_path> <out_path> [detect_every]
成功在 stdout 末行打印 `RESULT_JSON {...}`(face_mask_pro 的返回 dict);退出码 0=ok。
模型路径由调用方通过 INSIGHTFACE_HOME 环境变量注入(只读共享目录,不依赖运行时联网)。
"""
import json
import sys


def _run(in_path: str, out_path: str, detect_every: int) -> dict:
    try:
        from app.services.face_mask_pro import mask_faces_in_video_pro
        return mask_faces_in_video_pro(in_path, out_path, detect_every=detect_every)
    except Exception as e:
        # insightface/onnxruntime 缺失或崩溃 → 回退 face_blur(opencv haar/YuNet,无需额外模型)
        try:
            from app.services.face_blur import blur_faces_in_video
            ok = bool(blur_faces_in_video(in_path, out_path))
            return {"ok": True, "any_face": ok, "out": out_path if ok else None,
                    "fallback": "face_blur", "pro_error": str(e)[:200]}
        except Exception as e2:
            return {"ok": False,
                    "error": f"face_mask_pro 失败({str(e)[:100]}); face_blur 也失败({str(e2)[:100]})"}


def main() -> None:
    if len(sys.argv) < 3:
        print("RESULT_JSON " + json.dumps({"ok": False, "error": "用法: worker <in> <out> [detect_every]"}))
        sys.exit(2)
    in_path, out_path = sys.argv[1], sys.argv[2]
    detect_every = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    r = _run(in_path, out_path, detect_every)
    print("RESULT_JSON " + json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
