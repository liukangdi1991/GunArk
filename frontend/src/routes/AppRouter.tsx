import { lazy, Suspense } from "react";
import { Spin } from "antd";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AppShell } from "../layouts/AppShell";
import { BacktestReportPage } from "../pages/Backtests/BacktestReportPage";
import { BacktestWorkspacePage } from "../pages/Backtests/BacktestWorkspacePage";
import { ExecutionConsolePage } from "../pages/ExecutionConsole/ExecutionConsolePage";
import { MarketDataPage } from "../pages/MarketData/MarketDataPage";
import { SelectionResultPage } from "../pages/Selections/SelectionResultPage";
import { SelectionWorkspacePage } from "../pages/Selections/SelectionWorkspacePage";

const StockKlinePage = lazy(() => import("../pages/Stocks/StockKlinePage"));

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
        element: <SelectionWorkspacePage />,
      },
      {
        path: "selections/:executionKey",
        element: <SelectionResultPage />,
      },
      {
        path: "backtests",
        element: <Navigate to="/backtests/history" replace />,
      },
      {
        path: "backtests/history",
        element: <BacktestWorkspacePage mode="history" />,
      },
      {
        path: "backtests/selection-backtest",
        element: <BacktestWorkspacePage mode="selection_backtest" />,
      },
      {
        path: "backtests/:executionKey",
        element: <BacktestReportPage />,
      },
      {
        path: "market-data",
        element: <MarketDataPage />,
      },
      {
        path: "stocks/:code",
        element: (
          <Suspense fallback={<Spin style={{ display: "block", margin: "80px auto" }} />}>
            <StockKlinePage />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: "/console/:executionId",
    element: <ExecutionConsolePage />,
  },
]);

export function AppRouter() {
  return <RouterProvider router={router} />;
}
