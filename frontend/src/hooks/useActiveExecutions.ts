import { useEffect, useRef, useState } from "react";
import { listActiveExecutions } from "../services/executions";
import type { ActiveExecution } from "../types/execution";

const POLL_INTERVAL_MS = 10_000;

/**
 * 运行中任务轮询（侧边栏徽标）：
 * - 页面隐藏时暂停（切到别的标签页不空转），重新可见立即刷新一次；
 * - 失败静默保留上一次结果（徽标不值得弹错误）；
 * - 卸载中止在途请求。
 */
export function useActiveExecutions(): ActiveExecution[] {
  const [executions, setExecutions] = useState<ActiveExecution[]>([]);
  const seqRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const controller = new AbortController();

    async function poll() {
      const seq = ++seqRef.current;
      try {
        const payload = await listActiveExecutions(controller.signal);
        if (cancelled || seq !== seqRef.current) return;
        setExecutions(payload.executions);
      } catch {
        // 静默：保留上一次结果
      }
      if (!cancelled) {
        timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      }
    }

    function handleVisibility() {
      if (document.hidden) {
        if (timer) window.clearTimeout(timer);
        timer = undefined;
      } else if (timer === undefined) {
        void poll();
      }
    }

    void poll();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      controller.abort();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  return executions;
}
