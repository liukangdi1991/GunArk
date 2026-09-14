import { useCallback, useEffect, useRef, useState } from "react";
import type { Paged } from "../types/paging";

export interface PagedRequest {
  offset: number;
  limit: number;
  sortBy?: string;
  sortDir: "asc" | "desc";
  signal: AbortSignal;
}

interface QueryState {
  offset: number;
  limit: number;
  sortBy?: string;
  sortDir: "asc" | "desc";
}

export interface PagedQuery<T> extends QueryState {
  items: T[];
  total: number;
  loading: boolean;
  error: string;
  update: (patch: Partial<QueryState>) => void;
}

/**
 * 服务端分页的统一状态机（选股明细 / 回测明细共用）：
 * - abort + 序号守卫：切页/切过滤/卸载都不留悬挂请求，乱序响应丢弃；
 * - queryKey（过滤器或执行 key）变化 → 回第 1 页重取；
 * - total 变小导致当前页越界 → 自动退到最后一页；
 * - 失败保留上一页数据（翻页失败不清空，避免白屏式失败）。
 */
export function usePagedQuery<T>(options: {
  fetchPage: (req: PagedRequest) => Promise<Paged<T>>;
  queryKey: string;
  initialLimit?: number;
}): PagedQuery<T> {
  const { queryKey, fetchPage, initialLimit = 15 } = options;
  const [state, setState] = useState<QueryState>({
    offset: 0,
    limit: initialLimit,
    sortDir: "asc",
  });
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const seqRef = useRef(0);
  const keyRef = useRef(queryKey);
  const fetchRef = useRef(fetchPage);
  fetchRef.current = fetchPage;

  useEffect(() => {
    const keyChanged = keyRef.current !== queryKey;
    keyRef.current = queryKey;
    if (keyChanged && state.offset !== 0) {
      // 过滤器/执行 key 变了：回第 1 页，交给下一次 effect 拉取（本次不发请求，避免双请求）
      setState((cur) => ({ ...cur, offset: 0 }));
      return;
    }

    const controller = new AbortController();
    const seq = ++seqRef.current;
    setLoading(true);
    fetchRef
      .current({ ...state, signal: controller.signal })
      .then((page) => {
        if (seq !== seqRef.current) return;
        setItems(page.items);
        setTotal(page.total);
        setError("");
        if (page.items.length === 0 && page.total > 0 && state.offset >= page.total) {
          setState((cur) => ({
            ...cur,
            offset: Math.floor((page.total - 1) / cur.limit) * cur.limit,
          }));
        }
      })
      .catch((err: unknown) => {
        if (seq !== seqRef.current) return;
        if (err instanceof DOMException && err.name === "AbortError") return; // 卸载/切页的主动中止
        setError(err instanceof Error ? err.message : "加载失败。");
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
    return () => controller.abort();
  }, [state, queryKey]);

  const update = useCallback(
    (patch: Partial<QueryState>) => setState((cur) => ({ ...cur, ...patch })),
    [],
  );

  return { items, total, loading, error, ...state, update };
}
