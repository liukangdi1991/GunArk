const state = {
  runs: [],
  selections: [],
  strategies: [],
};

const $ = (selector) => document.querySelector(selector);

function fmt(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return `${value.toFixed(2)}${suffix}`;
  return `${value}${suffix}`;
}

function pctClass(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "positive" : "negative";
}

function table(headers, rows, empty = "暂无数据") {
  if (!rows.length) return `<p class="empty">${empty}</p>`;
  const head = headers.map((h) => `<th>${h}</th>`).join("");
  const body = rows
    .map((row) => `<tr>${row.map((cell) => `<td>${cell ?? ""}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `请求失败: ${response.status}`);
  }
  return payload;
}

async function loadStrategies() {
  const payload = await api("/api/strategies");
  state.strategies = payload.strategies || [];
  const options = state.strategies
    .map((s) => `<option value="${s.name}">${s.name}</option>`)
    .join("");
  $("#strategy-select").innerHTML = options;
  $("#selection-strategy-select").innerHTML = options;
}

async function loadSelections() {
  const payload = await api("/api/selections");
  state.selections = payload.runs || [];
  renderSelections();
}

async function loadRuns() {
  const payload = await api("/api/backtests");
  state.runs = payload.runs || [];
  renderRuns();
}

function renderSelections() {
  const list = $("#selection-list");
  if (!state.selections.length) {
    list.innerHTML = `<p class="empty">暂无选股记录</p>`;
    return;
  }
  list.innerHTML = state.selections
    .map((run) => {
      const strategies = (run.strategies || []).join(", ");
      return `
        <button class="run-item" type="button" data-run-id="${run.run_id}">
          <strong>${run.run_id}</strong>
          <span>${run.selection_date}</span>
          <span>${strategies || "-"}</span>
        </button>
      `;
    })
    .join("");
  list.querySelectorAll(".run-item").forEach((button) => {
    button.addEventListener("click", () => loadSelection(button.dataset.runId));
  });
}

function renderRuns() {
  const list = $("#run-list");
  if (!state.runs.length) {
    list.innerHTML = `<p class="empty">暂无回测记录</p>`;
    return;
  }
  list.innerHTML = state.runs
    .map((run) => {
      const strategies = (run.strategies || []).join(", ");
      return `
        <button class="run-item" type="button" data-run-id="${run.run_id}">
          <strong>${run.run_id}</strong>
          <span>${run.start_date} ~ ${run.end_date}</span>
          <span>${strategies || "-"}</span>
        </button>
      `;
    })
    .join("");
  list.querySelectorAll(".run-item").forEach((button) => {
    button.addEventListener("click", () => loadReport(button.dataset.runId));
  });
}

async function runSelection(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const selected = Array.from($("#selection-strategy-select").selectedOptions).map((option) => option.value);
  const payload = {
    date: form.get("date") || null,
    strategies: selected.length ? selected : null,
  };

  const button = $("#select-button");
  const status = $("#selection-status-line");
  button.disabled = true;
  status.textContent = "选股执行中...";
  try {
    const result = await api("/api/selections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    status.textContent = `完成: ${result.run_id}`;
    await loadSelections();
    await loadSelection(result.run_id);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function runBacktest(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const selected = Array.from($("#strategy-select").selectedOptions).map((option) => option.value);
  const payload = {
    from: form.get("from"),
    to: form.get("to"),
    strategies: selected.length ? selected : null,
    mode: form.get("mode"),
    cash_per_trade: Number(form.get("cash_per_trade")),
  };

  const button = $("#run-button");
  const status = $("#status-line");
  button.disabled = true;
  status.textContent = "回测执行中...";
  try {
    const result = await api("/api/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    status.textContent = `完成: ${result.run_id}`;
    await loadRuns();
    await loadReport(result.run_id);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function loadReport(runId) {
  const payload = await api(`/api/backtests/${runId}/report`);
  renderReport(payload);
}

function renderReport(payload) {
  const run = payload.run;
  const summary = run.summary || [];
  const strategySnapshots = run.strategy_snapshots || [];
  const trades = payload.trades || [];
  const skips = payload.skips || [];
  const equity = payload.equity || [];

  $("#selection-results-table").innerHTML = `<p class="empty">当前为回测报告</p>`;
  $("#report-title").textContent = run.run_id;
  $("#report-meta").innerHTML = `
    <div>${run.start_date} ~ ${run.end_date}</div>
    <div>${run.capital_mode} · 每票 ${fmt(run.cash_per_trade)}</div>
    <div>${(run.strategies || []).join(", ")}</div>
  `;

  const best = summary[0] || {};
  $("#summary-cards").innerHTML = [
    ["交易数", fmt(best.trade_count)],
    ["胜率", fmt(best.win_rate_pct, "%")],
    ["总收益", fmt(best.total_return_pct, "%")],
    ["最大回撤", fmt(best.max_drawdown_pct, "%")],
    ["Sharpe", fmt(best.sharpe)],
    ["跳过数", fmt(best.skip_count)],
  ]
    .map(([label, value]) => `<div class="card"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");

  $("#summary-table").innerHTML = table(
    ["策略", "交易数", "跳过数", "胜率", "总收益", "年化", "最大回撤", "Sharpe", "最终现金"],
    summary.map((s) => [
      s.strategy,
      s.trade_count,
      s.skip_count,
      `${fmt(s.win_rate_pct)}%`,
      `<span class="${pctClass(s.total_return_pct)}">${fmt(s.total_return_pct)}%</span>`,
      `${fmt(s.annual_return_pct)}%`,
      `<span class="${pctClass(s.max_drawdown_pct)}">${fmt(s.max_drawdown_pct)}%</span>`,
      fmt(s.sharpe),
      fmt(s.final_cash),
    ])
  );

  $("#strategy-snapshots").innerHTML = strategySnapshots.length
    ? strategySnapshots
        .map(
          (item) => `
            <div class="snapshot">
              <div class="snapshot-title">${item.name || "-"}</div>
              <div class="snapshot-class">${item.class || "-"}</div>
              <p>${item.description || "未配置策略说明"}</p>
              <pre>${JSON.stringify(item.params || {}, null, 2)}</pre>
            </div>
          `
        )
        .join("")
    : `<p class="empty">暂无策略快照</p>`;

  $("#trades-table").innerHTML = table(
    ["策略", "代码", "信号日", "买入日", "买入价", "卖出日", "卖出价", "股数", "盈亏", "收益率", "延期"],
    trades.map((t) => [
      t.strategy,
      t.code,
      t.signal_date,
      t.buy_date,
      fmt(t.buy_price),
      t.sell_date,
      fmt(t.sell_price),
      t.shares,
      `<span class="${pctClass(t.profit)}">${fmt(t.profit)}</span>`,
      `<span class="${pctClass(t.return_pct)}">${fmt(t.return_pct)}%</span>`,
      t.sell_postpone_days,
    ]),
    "无成交记录"
  );

  $("#skips-table").innerHTML = table(
    ["策略", "代码", "信号日", "买入日", "阶段", "原因"],
    skips.map((s) => [s.strategy, s.code, s.signal_date, s.buy_date, s.stage, s.reason]),
    "无跳过记录"
  );

  $("#equity-table").innerHTML = table(
    ["策略", "日期", "现金", "权益", "持仓数"],
    equity.map((e) => [e.strategy, e.date, fmt(e.cash), fmt(e.equity), e.position_count]),
    "暂无资金曲线数据"
  );
}

async function loadSelection(runId) {
  const payload = await api(`/api/selections/${runId}`);
  renderSelection(payload);
}

function renderSelection(payload) {
  const run = payload.run;
  const summary = run.summary || [];
  const strategySnapshots = run.strategy_snapshots || [];
  const picks = payload.picks || [];
  const totalCount = summary.reduce((sum, item) => sum + Number(item.count || 0), 0);

  $("#report-title").textContent = `选股: ${run.run_id}`;
  $("#report-meta").innerHTML = `
    <div>${run.selection_date}</div>
    <div>${(run.strategies || []).join(", ")}</div>
    <div>${run.signal_file || ""}</div>
  `;

  $("#summary-cards").innerHTML = [
    ["选股策略", fmt((run.strategies || []).length)],
    ["选中股票", fmt(totalCount)],
    ["运行状态", run.status || "-"],
    ["选股日期", run.selection_date || "-"],
  ]
    .map(([label, value]) => `<div class="card"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");

  $("#summary-table").innerHTML = table(
    ["策略", "选中数量", "耗时"],
    summary.map((s) => [s.strategy, s.count, `${fmt(s.elapsed_seconds)}s`]),
    "暂无选股摘要"
  );

  $("#strategy-snapshots").innerHTML = strategySnapshots.length
    ? strategySnapshots
        .map(
          (item) => `
            <div class="snapshot">
              <div class="snapshot-title">${item.name || "-"}</div>
              <div class="snapshot-class">${item.class || "-"}</div>
              <p>${item.description || "未配置策略说明"}</p>
              <pre>${JSON.stringify(item.params || {}, null, 2)}</pre>
            </div>
          `
        )
        .join("")
    : `<p class="empty">暂无策略快照</p>`;

  $("#selection-results-table").innerHTML = table(
    ["策略", "日期", "代码", "名称"],
    picks.map((p) => [p.strategy, p.date, p.code, p.name]),
    "无符合条件股票"
  );
  $("#trades-table").innerHTML = `<p class="empty">当前为选股结果</p>`;
  $("#skips-table").innerHTML = `<p class="empty">当前为选股结果</p>`;
  $("#equity-table").innerHTML = `<p class="empty">当前为选股结果</p>`;
}

async function boot() {
  $("#selection-form").addEventListener("submit", runSelection);
  $("#backtest-form").addEventListener("submit", runBacktest);
  $("#refresh-selections").addEventListener("click", loadSelections);
  $("#refresh-runs").addEventListener("click", loadRuns);
  await loadStrategies();
  await loadSelections();
  await loadRuns();
  const hashRunId = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (hashRunId.startsWith("selection:")) {
    await loadSelection(hashRunId.replace(/^selection:/, ""));
    return;
  }
  const initialRun = hashRunId || (state.runs[0] && state.runs[0].run_id);
  if (initialRun && state.runs.some((run) => run.run_id === initialRun)) {
    await loadReport(initialRun);
  } else if (state.selections[0]) {
    await loadSelection(state.selections[0].run_id);
  }
}

boot().catch((error) => {
  $("#status-line").textContent = error.message;
  $("#selection-status-line").textContent = error.message;
});
