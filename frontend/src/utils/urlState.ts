/**
 * 查询串里的「字符串列表」参数读写（视图状态同步到 URL 用）。
 *
 * 用途：表格的排序/页码/筛选写进 URL，浏览器前进后退（例如点进个股日 K 再返回）
 * 能恢复原样，而不是回退成默认值。
 */

/** 读：`?st=a,b,c` → `["a","b"]`；未设置或空串返回 null。 */
export function readListParam(params: URLSearchParams, name: string): string[] | null {
  const raw = params.get(name);
  if (!raw) {
    return null;
  }
  const items = raw.split(",").filter(Boolean);
  return items.length ? items : null;
}

/** 写：返回是否发生变更（调用方据此决定要不要落盘到地址栏）。 */
export function writeListParam(
  params: URLSearchParams,
  name: string,
  items: string[] | null,
): boolean {
  const current = params.get(name);
  const next = items && items.length ? items.join(",") : null;
  if (next === current) {
    return false;
  }
  if (next === null) {
    params.delete(name);
  } else {
    params.set(name, next);
  }
  return true;
}

/**
 * 直接改地址栏的查询串（不经 react-router 的导航）。
 *
 * 为什么不用 `useSearchParams().setSearchParams`：那会触发一次路由导航，
 * 导致路由元素**重挂载**——页面详情被重新拉取、内容重排，小窗口下滚动位置被夹回顶部
 * （用户实测报的「点排序跳回页首」）。这里以 `window.location.search` 为唯一真实来源，
 * 合并后 `history.replaceState`，既不产生历史记录、也不触发重挂载。
 *
 * 前进/后退仍然正常：浏览器恢复的是历史条目里的 URL（含本函数写入的参数），
 * react-router 在 popstate 时会重新读取地址栏。
 */
export function replaceSearchParams(mutate: (params: URLSearchParams) => boolean): void {
  const params = new URLSearchParams(window.location.search);
  if (!mutate(params)) {
    return;
  }
  const query = params.toString();
  // 合并保留既有 history.state（react-router 的 idx/usr、以及滚动位置记忆），
  // 不能传 null——那会把整份 state 抹掉
  const state = { ...((window.history.state ?? {}) as Record<string, unknown>) };
  window.history.replaceState(state, "", query ? `${window.location.pathname}?${query}` : window.location.pathname);
}

/** 读取当前地址栏（而非路由内部状态）的查询串。 */
export function currentSearchParams(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}
