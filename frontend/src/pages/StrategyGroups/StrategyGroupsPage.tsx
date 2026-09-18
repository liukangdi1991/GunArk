import {
  AppstoreOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  UndoOutlined,
} from "@ant-design/icons";
import {
  Button,
  Card,
  Col,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createStrategyGroup,
  deleteStrategyGroup,
  listStrategyGroups,
  listStrategies,
  resetStrategyGroups,
  updateStrategyGroup,
} from "../../services/strategies";
import type { Strategy, StrategyGroup } from "../../types/strategy";

const { Title, Text } = Typography;

/**
 * 策略分组管理：自由编辑「哪些策略属于哪个组」。
 * 组数据是选股/回测页策略下拉的分组来源；五个指标域组只是默认值，
 * 任何增删改都会保留（后端仅分组表为空时才重新播种默认组）。
 */
export function StrategyGroupsPage() {
  const [messageApi, contextHolder] = message.useMessage();
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [groups, setGroups] = useState<StrategyGroup[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [renameTarget, setRenameTarget] = useState<StrategyGroup | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const nameById = useMemo(
    () => new Map(strategies.map((s) => [s.strategy_id, s.name])),
    [strategies],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [strategyPayload, groupPayload] = await Promise.all([
        listStrategies(),
        listStrategyGroups(),
      ]);
      setStrategies(strategyPayload.strategies || []);
      setGroups(groupPayload || []);
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : "加载策略分组失败。");
    } finally {
      setLoading(false);
    }
  }, [messageApi]);

  useEffect(() => {
    load();
  }, [load]);

  const run = useCallback(
    async (action: () => Promise<unknown>, successText: string) => {
      setBusy(true);
      try {
        await action();
        messageApi.success(successText);
        await load();
      } catch (error) {
        messageApi.error(error instanceof Error ? error.message : "操作失败。");
      } finally {
        setBusy(false);
      }
    },
    [load, messageApi],
  );

  const assignedIds = useMemo(
    () => new Set((groups ?? []).flatMap((g) => g.members.map((m) => m.strategy_id))),
    [groups],
  );
  const unassigned = useMemo(
    () => strategies.filter((s) => !assignedIds.has(s.strategy_id)),
    [strategies, assignedIds],
  );

  if (loading && !groups) {
    return <Spin style={{ display: "block", margin: "80px auto" }} />;
  }

  return (
    <div className="feature-page">
      {contextHolder}
      <Card>
        <Space direction="vertical" size={4} style={{ width: "100%" }}>
          <Title level={4} style={{ margin: 0 }}>
            策略分组
          </Title>
          <Text type="secondary">
            编辑策略与分组的归属关系；选股 / 回测页的策略下拉按此分组展示。未入组的策略会归入「其他」。
          </Text>
        </Space>
        <Space style={{ marginTop: 16 }}>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setNewGroupName("");
              setCreateOpen(true);
            }}
          >
            新建分组
          </Button>
          <Popconfirm
            title="恢复默认分组？"
            description="将清空当前全部分组，重置为五个指标域默认组。"
            okText="恢复"
            cancelText="取消"
            onConfirm={() => run(resetStrategyGroups, "已恢复默认分组")}
          >
            <Button icon={<UndoOutlined />} loading={busy}>
              恢复默认分组
            </Button>
          </Popconfirm>
        </Space>
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        {(groups ?? []).map((group) => {
          const memberIds = group.members.map((m) => m.strategy_id);
          const candidates = strategies.filter(
            (s) => !memberIds.includes(s.strategy_id),
          );
          return (
            <Col xs={24} lg={12} key={group.id}>
              <Card
                style={group.enabled === false ? { opacity: 0.55 } : undefined}
                title={
                  <Space>
                    {group.name}
                    <Text type="secondary">{memberIds.length} 个</Text>
                    {group.enabled === false ? <Tag>已隐藏</Tag> : null}
                  </Space>
                }
                extra={
                  <Space>
                    <Space size={4}>
                      <Text type="secondary" style={{ fontSize: 12 }}>下拉显示</Text>
                      <Switch
                        size="small"
                        checked={group.enabled !== false}
                        loading={busy}
                        onChange={(checked) =>
                          run(
                            () => updateStrategyGroup(group.id, { enabled: checked }),
                            checked ? "已显示该分组" : "已隐藏该分组",
                          )
                        }
                      />
                    </Space>
                    <Button
                      size="small"
                      type="text"
                      icon={<EditOutlined />}
                      onClick={() => {
                        setRenameTarget(group);
                        setRenameValue(group.name);
                      }}
                    />
                    <Popconfirm
                      title={`删除分组「${group.name}」？`}
                      description="组内策略会变为未分组，不影响策略本身。"
                      okText="删除"
                      cancelText="取消"
                      onConfirm={() =>
                        run(() => deleteStrategyGroup(group.id), "分组已删除")
                      }
                    >
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                }
              >
                <Space wrap size={[8, 8]}>
                  {group.members.length === 0 ? (
                    <Text type="secondary">暂无策略</Text>
                  ) : (
                    group.members.map((member) => (
                      <Tag
                        key={member.strategy_id}
                        closable
                        onClose={(e) => {
                          e.preventDefault();
                          run(
                            () =>
                              updateStrategyGroup(group.id, {
                                members: memberIds.filter((id) => id !== member.strategy_id),
                              }),
                            "已移出分组",
                          );
                        }}
                      >
                        {nameById.get(member.strategy_id) ?? member.strategy_id}
                      </Tag>
                    ))
                  )}
                </Space>
                <Select
                  style={{ width: "100%", marginTop: 12 }}
                  placeholder="添加策略到本组…"
                  value={null}
                  showSearch
                  optionFilterProp="label"
                  disabled={busy}
                  options={candidates.map((s) => ({ label: s.name, value: s.strategy_id }))}
                  onChange={(strategyId: string) =>
                    run(
                      () => updateStrategyGroup(group.id, { members: [...memberIds, strategyId] }),
                      "已加入分组",
                    )
                  }
                />
              </Card>
            </Col>
          );
        })}
      </Row>

      {unassigned.length > 0 ? (
        <Card title={`未分组 · ${unassigned.length}`} style={{ marginTop: 16 }}>
          <Space wrap size={[8, 8]}>
            {unassigned.map((s) => (
              <Tag key={s.strategy_id}>{s.name}</Tag>
            ))}
          </Space>
          <Text type="secondary" style={{ display: "block", marginTop: 8 }}>
            这些策略不在任何分组里，下拉中会出现在「其他」；点某个分组里的「添加策略」即可归组。
          </Text>
        </Card>
      ) : null}

      {!groups?.length ? (
        <Empty description="暂无分组" style={{ marginTop: 32 }} />
      ) : null}

      <Modal
        title="新建分组"
        open={createOpen}
        okText="创建"
        cancelText="取消"
        okButtonProps={{ disabled: !newGroupName.trim() }}
        onOk={async () => {
          await run(
            () => createStrategyGroup({ name: newGroupName.trim() }),
            "分组已创建",
          );
          setCreateOpen(false);
        }}
        onCancel={() => setCreateOpen(false)}
      >
        <Input
          placeholder="分组名称"
          value={newGroupName}
          maxLength={64}
          onChange={(e) => setNewGroupName(e.target.value)}
        />
      </Modal>

      <Modal
        title="重命名分组"
        open={renameTarget !== null}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ disabled: !renameValue.trim() || busy }}
        onOk={async () => {
          if (!renameTarget) return;
          await run(
            () => updateStrategyGroup(renameTarget.id, { name: renameValue.trim() }),
            "已重命名",
          );
          setRenameTarget(null);
        }}
        onCancel={() => setRenameTarget(null)}
      >
        <Input
          placeholder="分组名称"
          value={renameValue}
          maxLength={64}
          onChange={(e) => setRenameValue(e.target.value)}
        />
      </Modal>
    </div>
  );
}
