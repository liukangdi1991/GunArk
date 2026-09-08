import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../services/apiClient";
import { getKline } from "../../services/marketData";
import type { AdjustMode, KlinePeriod, KlineResponse } from "../../types/kline";

export interface KlineViewState {
  payload: KlineResponse | null;
  loading: boolean;
  error: string | null;
  status: number | null;
}

/** 单一取数通路（M10/M13）：useEffect 发 GET（abort + 序号守卫）→ payload 状态化；
 *  KlineChart 只消费 payload，loader 不发请求。 */
export function useKline(
  code: string,
  period: KlinePeriod,
  adjust: AdjustMode,
  retryKey: number,
): KlineViewState {
  const [payload, setPayload] = useState<KlineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const seqRef = useRef(0);

  useEffect(() => {
    if (!/^\d{6}$/.test(code)) {
      setPayload(null);
      setStatus(422);
      setError("非法的股票代码。");
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const seq = ++seqRef.current;
    setLoading(true);
    getKline(code, period, adjust, { signal: controller.signal })
      .then((data) => {
        if (seq !== seqRef.current) return; // 乱序响应丢弃
        setPayload(data);
        setError(null);
        setStatus(200);
      })
      .catch((err: unknown) => {
        if (seq !== seqRef.current) return;
        if (err instanceof DOMException && err.name === "AbortError") return; // 静默
        setPayload(null);
        setStatus(err instanceof ApiError ? err.status : null);
        setError(err instanceof Error ? err.message : "加载K线数据失败。");
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
    return () => controller.abort();
  }, [code, period, adjust, retryKey]);

  return { payload, loading, error, status };
}
