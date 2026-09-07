export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function requestJson<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const { timeoutMs = 15000, ...rest } = init ?? {};
  // 超时与调用方 signal（如轮询 cleanup abort）组合；任一触发即中止。
  const timeoutController = new AbortController();
  const timer = setTimeout(() => timeoutController.abort(), timeoutMs);
  const signal = rest.signal
    ? (AbortSignal.any([rest.signal, timeoutController.signal]))
    : timeoutController.signal;

  try {
    const response = await fetch(path, {
      ...rest,
      signal,
      headers: {
        Accept: "application/json",
        ...(rest.body ? { "Content-Type": "application/json" } : {}),
        ...rest.headers,
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message =
        typeof payload?.detail === "string" ? payload.detail : `请求失败: ${response.status}`;
      // 带上状态码：调用方按 409/400 分支，而不是匹配后端文案
      throw new ApiError(message, response.status);
    }
    return payload as T;
  } catch (error) {
    // 超时 abort 转成可读错误；外部 signal 触发（cleanup）时调用方以 cancelled 标志忽略
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("请求超时", 0);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
