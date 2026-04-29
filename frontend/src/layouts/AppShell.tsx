import {
  BarChartOutlined,
  DatabaseOutlined,
  LineChartOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  OrderedListOutlined,
} from "@ant-design/icons";
import { Button, Layout, Menu, Space, Tooltip, Typography } from "antd";
import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

const { Content, Sider } = Layout;
const { Text } = Typography;

function selectedKey(pathname: string) {
  if (pathname.startsWith("/backtests/selection-backtest")) {
    return "backtests/selection-backtest";
  }
  if (pathname.startsWith("/backtests")) {
    return "backtests/history";
  }
  if (pathname.startsWith("/market-data")) {
    return "market-data";
  }
  return "selections";
}

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [openKeys, setOpenKeys] = useState<string[]>(
    location.pathname.startsWith("/backtests") ? ["backtests"] : [],
  );

  useEffect(() => {
    if (location.pathname.startsWith("/backtests")) {
      setOpenKeys(["backtests"]);
    }
  }, [location.pathname]);

  return (
    <Layout className={collapsed ? "main-layout sidebar-collapsed" : "main-layout"}>
      <Sider
        className="main-sider"
        width={276}
        collapsedWidth={72}
        collapsed={collapsed}
        trigger={null}
      >
        <div className="brand-block">
          <div className="brand-mark">日</div>
          <div className="brand-copy">
            <div className="brand-title">日线观势</div>
            <Text className="muted-text">A股日线量化工作台</Text>
          </div>
        </div>

        <Tooltip title={collapsed ? "展开侧边栏" : "收起侧边栏"} placement="right">
          <Button
            className="sider-toggle"
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
          />
        </Tooltip>

        <div className="sider-section-title">功能区</div>
        <Menu
          className="main-menu"
          mode="inline"
          selectedKeys={[selectedKey(location.pathname)]}
          openKeys={collapsed ? [] : openKeys}
          onOpenChange={(keys) => setOpenKeys(keys as string[])}
          onClick={({ key }) => navigate(`/${key}`)}
          items={[
            {
              key: "selections",
              icon: <OrderedListOutlined />,
              label: "选股",
            },
            {
              key: "backtests",
              icon: <BarChartOutlined />,
              label: "回测",
              children: [
                {
                  key: "backtests/history",
                  label: "根据选股历史回测",
                },
                {
                  key: "backtests/selection-backtest",
                  label: "选股回测",
                },
              ],
            },
            {
              key: "market-data",
              icon: <DatabaseOutlined />,
              label: "行情数据",
            },
          ]}
        />

        <div className="sider-foot">
          <Space direction="vertical" size={4}>
            <Text className="muted-text">author: kangdi.liu</Text>
            <Text className="muted-text">
              <LineChartOutlined /> A股日线级高性能量化选股系统
            </Text>
          </Space>
        </div>
      </Sider>

      <Layout className="main-content-layout">
        <Content className="main-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
