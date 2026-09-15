import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Spin } from "antd";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AppShell } from "../layouts/AppShell";

/**
 * 路由级懒加载（P8）：主包只留框架（AppShell + antd 基础）。
 * 页面的重量级依赖随之分包——recharts 只在回测报告用、klinecharts 只在 K 线页用，
 * 首访其它页面不再为它们付费（原先主包 1.71MB）。
 */
const SelectionWorkspacePage = lazy(() =>
  import("../pages/Selections/SelectionWorkspacePage").then((m) => ({
    default: m.SelectionWorkspacePage,
  })),
);
const SelectionResultPage = lazy(() =>
  import("../pages/Selections/SelectionResultPage").then((m) => ({
    default: m.SelectionResultPage,
  })),
);
const BacktestWorkspacePage = lazy(() =>
  import("../pages/Backtests/BacktestWorkspacePage").then((m) => ({
    default: m.BacktestWorkspacePage,
  })),
);
const BacktestReportPage = lazy(() =>
  import("../pages/Backtests/BacktestReportPage").then((m) => ({
    default: m.BacktestReportPage,
  })),
);
const MarketDataPage = lazy(() =>
  import("../pages/MarketData/MarketDataPage").then((m) => ({ default: m.MarketDataPage })),
);
const ExecutionConsolePage = lazy(() =>
  import("../pages/ExecutionConsole/ExecutionConsolePage").then((m) => ({
    default: m.ExecutionConsolePage,
  })),
);
const StockKlinePage = lazy(() => import("../pages/Stocks/StockKlinePage"));

function page(element: ReactNode) {
  return <Suspense fallback={<Spin style={{ display: "block", margin: "80px auto" }} />}>{element}</Suspense>;
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      {
        index: true,
        element: <Navigate to="/selections" replace />,
      },
      {
        path: "app",
        element: <Navigate to="/selections" replace />,
      },
      {
        path: "selections",
        element: page(<SelectionWorkspacePage />),
      },
      {
        path: "selections/:executionKey",
        element: page(<SelectionResultPage />),
      },
      {
        path: "backtests",
        element: <Navigate to="/backtests/history" replace />,
      },
      {
        path: "backtests/history",
        element: page(<BacktestWorkspacePage mode="history" />),
      },
      {
        path: "backtests/selection-backtest",
        element: page(<BacktestWorkspacePage mode="selection_backtest" />),
      },
      {
        path: "backtests/:executionKey",
        element: page(<BacktestReportPage />),
      },
      {
        path: "market-data",
        element: page(<MarketDataPage />),
      },
      {
        path: "console/:executionId",
        element: page(<ExecutionConsolePage />),
      },
      {
        path: "stocks/:code",
        element: page(<StockKlinePage />),
      },
    ],
  },
  {
    path: "/console/:executionId",
    element: <Navigate to="/selections" replace />,
  },
]);

export function AppRouter() {
  return <RouterProvider router={router} />;
}
