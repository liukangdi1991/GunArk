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

/** 写：返回是否发生变更（调用方据此决定要不要 setSearchParams）。 */
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
