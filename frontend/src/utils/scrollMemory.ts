/**
 * 滚动位置按**路由**记忆（sessionStorage，键为 pathname）。
 *
 * 为什么不用 history.state：实测（2026-09-15）回退后 react-router 会把条目 state
 * 重写为 {"idx":n}，第三方写入的键会丢；sessionStorage 不受影响，还顺带支持刷新后恢复。
 * 键只取 pathname（不含查询串）——排序/翻页改变查询串不应丢滚动位置。
 */
const key = () => `scroll:${window.location.pathname}`;

/** 读该路由记住的滚动位置；没有记过返回 0。 */
export function readRememberedScroll(): number {
  const raw = sessionStorage.getItem(key());
  const value = raw === null ? NaN : Number(raw);
  return Number.isFinite(value) ? value : 0;
}

/** 把滚动位置写进该路由的记忆（调用方自行节流；隐私模式下静默失败）。 */
export function rememberScroll(y: number): void {
  try {
    sessionStorage.setItem(key(), String(y));
  } catch {
    // 忽略：禁用存储时不影响主流程
  }
}
