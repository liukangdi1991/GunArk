import { Alert, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { PagedQuery } from "../hooks/usePagedQuery";

export interface PagedTableProps<T extends object> {
  query: PagedQuery<T>;
  columns: ColumnsType<T>;
  rowKey: (record: T) => string;
  scrollX?: number;
  emptyText?: string;
}

/**
 * 服务端分页表：把 antd Table 的受控分页与排序桥接到 usePagedQuery。
 * 排序由后端执行（白名单列），前端不做本地排序——否则会出现「只排当前页」的错觉。
 */
export function PagedTable<T extends object>({
  query,
  columns,
  rowKey,
  scrollX,
  emptyText,
}: PagedTableProps<T>) {
  return (
    <>
      {query.error ? (
        <Alert
          type="error"
          showIcon
          message="明细加载失败"
          description={query.error}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      <Table<T>
        rowKey={rowKey}
        columns={columns}
        dataSource={query.items}
        loading={query.loading}
        size="middle"
        locale={{ emptyText }}
        scroll={scrollX ? { x: scrollX } : undefined}
        pagination={{
          current: Math.floor(query.offset / query.limit) + 1,
          pageSize: query.limit,
          total: query.total,
          showSizeChanger: true,
          showTotal: (value) => `共 ${value} 条`,
        }}
        onChange={(pagination, _filters, sorter) => {
          const limit = pagination.pageSize ?? query.limit;
          const offset = ((pagination.current ?? 1) - 1) * limit;
          const single = Array.isArray(sorter) ? sorter[0] : sorter;
          query.update({
            offset,
            limit,
            sortBy: single?.order ? String(single.field) : undefined,
            sortDir: single?.order === "descend" ? "desc" : "asc",
          });
        }}
      />
    </>
  );
}
