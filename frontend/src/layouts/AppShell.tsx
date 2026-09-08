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
  if (pathname.startsWith("/stocks")) {
    return ""; // 个股K线页不归属任何菜单（N11），不高亮「选股」
  }
  return "selections";
}

function TrendRadarLogo() {
  return (
    <svg className="trend-radar-logo" viewBox="0 0 48 48" role="img" aria-label="趋势雷达 Logo">
      <defs>
        <linearGradient id="trend-radar-sweep" x1="11" x2="39" y1="13" y2="39">
          <stop offset="0%" stopColor="#67d8ff" stopOpacity="0.82" />
          <stop offset="100%" stopColor="#2da44e" stopOpacity="0.18" />
        </linearGradient>
        <linearGradient id="trend-radar-line" x1="12" x2="37" y1="34" y2="19">
          <stop offset="0%" stopColor="#2da44e" />
          <stop offset="54%" stopColor="#67d8ff" />
          <stop offset="100%" stopColor="#ff6b6b" />
        </linearGradient>
      </defs>
      <circle className="trend-radar-ring" cx="24" cy="24" r="17.5" />
      <circle className="trend-radar-ring trend-radar-ring-soft" cx="24" cy="24" r="11.2" />
      <path className="trend-radar-sweep" d="M24 24 L24 6 A18 18 0 0 1 41.1 29.6 Z" />
      <path className="trend-radar-axis" d="M24 7 V41 M7 24 H41" />
      <path className="trend-radar-tick trend-radar-up" d="M12 32 L17 27 L22 30 L28 22 L34 25 L38 16" />
      <path className="trend-radar-candle trend-radar-down" d="M15 35 V30 M15 33 H19 M19 29 V25" />
      <path className="trend-radar-candle trend-radar-up" d="M29 30 V23 M29 26 H34 M34 21 V17" />
      <circle className="trend-radar-dot" cx="24" cy="24" r="2.4" />
    </svg>
  );
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
          <div className="brand-mark">
            <TrendRadarLogo />
          </div>
          <div className="brand-copy">
            <div className="brand-title">趋势雷达</div>
            <Text className="muted-text">TrendRadar</Text>
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
