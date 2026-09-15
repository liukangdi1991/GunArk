import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Paged } from "../types/paging";
import { currentSearchParams, replaceSearchParams } from "../utils/urlState";

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
  /**
   * 提供前缀即把「页码/每页/排序」同步到 URL 查询串（`<前缀>_o/_l/_sb/_sd`）。
   * 同页多张表各用一个前缀，互不覆盖；写入用 replace，翻页不塞满历史栈。
   */
  urlParamPrefix?: string;
}): PagedQuery<T> {
  const { queryKey, fetchPage, initialLimit = 15, urlParamPrefix } = options;

  // 初值只在挂载时取：之后 URL 由本 hook 写，避免与表单状态互相打架
  const initialState = useMemo<QueryState>(() => {
    if (!urlParamPrefix) {
      return { offset: 0, limit: initialLimit, sortDir: "asc" };
    }
    const searchParams = currentSearchParams();
    const readNumber = (name: string, fallback: number) => {
      const raw = searchParams.get(`${urlParamPrefix}_${name}`);
      if (raw === null || raw === "") {
        return fallback; // 注意 Number(null) === 0：缺参数必须走兜底，否则每页会被写成 1 行
      }
      const value = Number(raw);
      return Number.isFinite(value) && value >= 0 ? value : fallback;
    };
    return {
      offset: readNumber("o", 0),
      limit: Math.max(1, readNumber("l", initialLimit)),
      sortBy: searchParams.get(`${urlParamPrefix}_sb`) || undefined,
      sortDir: searchParams.get(`${urlParamPrefix}_sd`) === "desc" ? "desc" : "asc",
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [state, setState] = useState<QueryState>(initialState);
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

  // 状态 → 地址栏（replaceState，不经路由导航：避免重挂载导致详情重取与滚动跳顶）
  useEffect(() => {
    if (!urlParamPrefix) {
      return;
    }
    replaceSearchParams((params) => {
      const before = params.toString();
      params.set(`${urlParamPrefix}_o`, String(state.offset));
      params.set(`${urlParamPrefix}_l`, String(state.limit));
      if (state.sortBy) {
        params.set(`${urlParamPrefix}_sb`, state.sortBy);
        params.set(`${urlParamPrefix}_sd`, state.sortDir);
      } else {
        params.delete(`${urlParamPrefix}_sb`);
        params.delete(`${urlParamPrefix}_sd`);
      }
      return params.toString() !== before;
    });
  }, [state, urlParamPrefix]);

  const update = useCallback(
    (patch: Partial<QueryState>) => setState((cur) => ({ ...cur, ...patch })),
    [],
  );

  return { items, total, loading, error, ...state, update };
}
