/**
 * 把 unknown error 转成展示用 string,兼容 Error / string / { message } 三类常见形态。
 *
 * 六十二续:tsconfig.strict 开了 useUnknownInCatchVariables,
 * 所有 `catch (e)` 里 e 是 unknown 不能直接读 e.message。
 * 这是统一 narrow helper,替代散落 19 处 `catch (e: any) { setError(e.message) }`。
 */
export function errMsg(e: unknown, fallback = "未知错误"): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  if (e && typeof e === "object" && "message" in e && typeof (e as { message: unknown }).message === "string") {
    return (e as { message: string }).message;
  }
  return fallback;
}
