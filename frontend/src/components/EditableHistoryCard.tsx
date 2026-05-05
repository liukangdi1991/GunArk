import { DeleteOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Checkbox, List, Popconfirm, Space } from "antd";
import type { ReactNode } from "react";
import { useMemo } from "react";

interface EditableHistoryCardProps<T extends { execution_key: string }> {
  title: string;
  emptyText: string;
  records: T[];
  loading: boolean;
  editing: boolean;
  deleting: boolean;
  selectedKeys: string[];
  onEditingChange: (editing: boolean) => void;
  onSelectedKeysChange: (keys: string[]) => void;
  onToggleRecord: (executionKey: string, checked: boolean) => void;
  onDelete: (executionKeys?: string[]) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  onOpen: (executionKey: string) => void;
  renderTitle: (record: T) => ReactNode;
  renderDescription: (record: T) => ReactNode;
  renderExtra: (record: T) => ReactNode;
}

export function EditableHistoryCard<T extends { execution_key: string }>(props: EditableHistoryCardProps<T>) {
  const {
    title,
    emptyText,
    records,
    loading,
    editing,
    deleting,
    selectedKeys,
    onEditingChange,
    onSelectedKeysChange,
    onToggleRecord,
    onDelete,
    onRefresh,
    onOpen,
    renderTitle,
    renderDescription,
    renderExtra,
  } = props;

  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);
  const allKeys = useMemo(() => records.map((record) => record.execution_key), [records]);
  const allSelected = records.length > 0 && selectedKeys.length === records.length;
  const partiallySelected = selectedKeys.length > 0 && selectedKeys.length < records.length;

  return (
    <Card
      className="workbench-card"
      title={title}
      extra={
        <Space size={8} wrap>
          {editing ? (
            <>
              <Checkbox
                checked={allSelected}
                indeterminate={partiallySelected}
                onChange={(event) => onSelectedKeysChange(event.target.checked ? allKeys : [])}
              >
                全选
              </Checkbox>
              <Popconfirm
                title={`删除选中的${title}？`}
                description={`将删除 ${selectedKeys.length} 条历史记录及对应本地结果文件。`}
                okText="删除"
                cancelText="取消"
                disabled={!selectedKeys.length}
                onConfirm={() => void onDelete(selectedKeys)}
              >
                <Button size="small" danger icon={<DeleteOutlined />} loading={deleting} disabled={!selectedKeys.length}>
                  删除选中
                </Button>
              </Popconfirm>
              <Popconfirm
                title={`清空全部${title}？`}
                description={`将删除所有${title}记录及对应本地结果文件。`}
                okText="清空"
                cancelText="取消"
                disabled={!records.length}
                onConfirm={() => void onDelete()}
              >
                <Button size="small" danger loading={deleting} disabled={!records.length}>
                  清空
                </Button>
              </Popconfirm>
              <Button
                size="small"
                onClick={() => {
                  onEditingChange(false);
                  onSelectedKeysChange([]);
                }}
              >
                取消
              </Button>
            </>
          ) : (
            <Button size="small" onClick={() => onEditingChange(true)}>
              编辑
            </Button>
          )}
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void onRefresh()}>
            刷新
          </Button>
        </Space>
      }
    >
      <List
        className="selection-run-list"
        loading={loading}
        dataSource={records}
        locale={{ emptyText }}
        renderItem={(record) => (
          <List.Item
            onClick={() => {
              if (editing) {
                onToggleRecord(record.execution_key, !selectedSet.has(record.execution_key));
                return;
              }
              onOpen(record.execution_key);
            }}
          >
            {editing ? (
              <Checkbox
                checked={selectedSet.has(record.execution_key)}
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => onToggleRecord(record.execution_key, event.target.checked)}
              />
            ) : null}
            <List.Item.Meta title={renderTitle(record)} description={renderDescription(record)} />
            {renderExtra(record)}
          </List.Item>
        )}
      />
    </Card>
  );
}
