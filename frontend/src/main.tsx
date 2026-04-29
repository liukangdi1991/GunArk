import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import "antd/dist/reset.css";
import "./styles/global.css";
import { AppRouter } from "./routes/AppRouter";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#0969da",
          colorSuccess: "#1a7f37",
          colorWarning: "#9a6700",
          colorError: "#cf222e",
          colorText: "#24292f",
          colorTextSecondary: "#57606a",
          colorBgBase: "#f6f8fa",
          colorBgContainer: "#ffffff",
          colorBorder: "#d0d7de",
          borderRadius: 6,
          fontFamily:
            "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Noto Sans CJK SC\", \"Microsoft YaHei\", sans-serif",
        },
      }}
    >
      <AppRouter />
    </ConfigProvider>
  </React.StrictMode>,
);
