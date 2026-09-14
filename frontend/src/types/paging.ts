/** 服务端分页的统一响应形状（选股 picks 与回测明细共用）。 */
export interface Paged<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
