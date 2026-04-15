#!/usr/bin/env python3
"""
日线观势 - Textual 全屏交互式 CLI

说明：
- 仅负责交互与参数组织，不改变策略逻辑。
- 底层继续调用 run.py / select_stock.py / fetch_kline.py。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Log, SelectionList, Static


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_SCRIPT = PROJECT_ROOT / "polars_version" / "run.py"
SELECT_SCRIPT = PROJECT_ROOT / "polars_version" / "select_stock.py"
FETCH_SCRIPT = PROJECT_ROOT / "polars_version" / "fetch_kline.py"
CONFIG_PATH = PROJECT_ROOT / "configs.json"


def load_active_strategies() -> List[str]:
    if not CONFIG_PATH.exists():
        return []
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

    selectors = payload.get("selectors", []) if isinstance(payload, dict) else []
    names: List[str] = []
    for selector in selectors:
        if not isinstance(selector, dict):
            continue
        if selector.get("activate", True) is False:
            continue
        alias = str(selector.get("alias", "")).strip()
        if alias:
            names.append(alias)
    return names


class RunLogScreen(Screen):
    """独立运行日志面板：执行任务时提供更大的日志视图。"""

    CSS = """
    RunLogScreen {
        background: #19162b;
        color: #f7f4ff;
    }

    #runlog-title {
        color: #efe7ff;
        text-style: bold;
        margin: 0 1;
    }

    #runlog-tip {
        color: #d8ccff;
        margin: 0 1 1 1;
    }

    #runlog {
        border: round #9f8de6;
        margin: 0 1 1 1;
        height: 1fr;
        background: #231e3a;
        color: #f2ecff;
    }

    #runlog-back {
        width: 24;
        margin: 0 1 1 1;
        background: #c4b5fd;
        color: #221a36;
        border: none;
        text-style: bold;
        content-align: center middle;
    }
    """

    BINDINGS = [("ctrl+b", "app.pop_screen", "返回主界面")]

    def __init__(self) -> None:
        super().__init__()
        self.pending_lines: List[str] = []

    def compose(self) -> ComposeResult:
        yield Static("任务运行日志", id="runlog-title")
        yield Static("按 Ctrl+B 返回主界面（任务会继续后台执行）", id="runlog-tip")
        yield Log(id="runlog", auto_scroll=True)
        yield Button("⬅ 后台挂起并返回主界面", id="runlog-back")

    def on_mount(self) -> None:
        """屏幕挂载后刷新此前缓存的日志，避免未挂载时写入报错。"""
        if not self.pending_lines:
            return
        self._flush_pending_lines()

    def write_line(self, text: str) -> None:
        try:
            self.query_one("#runlog", Log).write_line(text)
        except NoMatches:
            self.pending_lines.append(text)

    def _flush_pending_lines(self) -> None:
        try:
            log = self.query_one("#runlog", Log)
        except NoMatches:
            return
        for line in self.pending_lines:
            log.write_line(line)
        self.pending_lines.clear()


class DailyTrendApp(App):
    TITLE = "日线观势"

    CSS = """
    Screen {
        background: #0f1220;
        color: #e8eeff;
    }

    #banner {
        border: round #7aa2ff;
        background: #1a2036;
        padding: 0 2;
        margin: 0 1 1 1;
        text-align: center;
        color: #e8eeff;
    }

    #layout {
        height: 1fr;
        min-height: 0;
        padding: 0 1 1 1;
    }

    #sidebar {
        width: 34;
        min-width: 24;
        border: round #6e90e8;
        padding: 1;
        margin-right: 0;
        overflow-y: auto;
        background: #151c2f;
    }

    #sidebar-title {
        color: #d7e3ff;
        text-style: bold;
        margin: 0 1 1 1;
    }

    #main {
        border: round #6e90e8;
        padding: 0 1;
        overflow-y: auto;
        background: #131a2b;
        min-height: 0;
    }

    #config-fields {
        margin: 0 0 0 1;
        width: 1fr;
        max-width: 50;
    }

    .action-btn {
        width: 100%;
        height: 1;
        min-height: 1;
        margin-bottom: 1;
        padding: 0;
        background: #1b2740;
        color: #e7eeff;
        text-style: bold;
        content-align: center middle;
        border: none;
    }

    .active-action {
        background: #9ec1ff;
        color: #0f1a33;
        text-style: bold;
        border-left: thick #dbe8ff;
    }

    .action-btn:hover {
        background: #25385d;
    }

    .active-action:hover {
        background: #bfd7ff;
    }

    #run-button {
        width: 1fr;
        min-width: 0;
        max-width: 52;
        height: 3;
        min-height: 3;
        margin: 1 0 0 1;
        padding: 0 2;
        background: #7aa2ff;
        color: #0d1730;
        text-style: bold;
        border: none;
        content-align: center middle;
    }

    #run-button.running {
        background: #ffd08a;
        color: #3d2500;
        border: none;
    }

    Input {
        background: #0d1628;
        color: #e8eeff;
        border: tall #3d5f9c;
        width: 1fr;
        height: 3;
        min-height: 3;
        padding: 0 1;
        margin-bottom: 0;
    }

    Input:focus {
        border: tall #79a5ff;
        background: #13213a;
    }

    SelectionList {
        background: #0d1628;
        color: #e8eeff;
        border: tall #3d5f9c;
        width: 1fr;
        margin-bottom: 0;
        min-height: 3;
        max-height: 6;
    }

    Checkbox {
        color: #c8d6f2;
        background: transparent;
        width: auto;
        height: auto;
        min-height: 0;
        padding: 0;
        margin: 0;
        border: none;
    }

    #skip_fetch {
        width: auto;
        margin-left: 2;
    }

    #skip_fetch:focus {
        background: transparent;
    }

    #strategy_custom {
        width: auto;
        margin-left: 2;
        color: #9fb2d6;
        text-style: none;
    }

    #strategy_custom:focus {
        background: transparent;
    }

    #action-desc {
        color: #9fb2d6;
        margin-bottom: 0;
        text-style: none;
    }

    .inline-row {
        margin-left: 2;
        height: 1;
        min-height: 1;
        width: 1fr;
        layout: horizontal;
        align: left middle;
    }

    .inline-hint {
        margin-left: 0;
        color: #9fb2d6;
        text-style: none;
        text-wrap: nowrap;
        width: auto;
        min-width: 0;
    }

    .inline-input {
        width: 11;
        min-width: 11;
        max-width: 11;
        margin-left: 0;
        height: 1;
        min-height: 1;
        padding: 0;
        border: none;
        background: transparent;
        color: #e8eeff;
    }

    #month_input {
        width: 7;
        min-width: 7;
        max-width: 7;
        height: 1;
        min-height: 1;
        padding: 0;
    }

    #month-row {
        height: 1;
        min-height: 1;
    }

    #start_input,
    #end_input {
        width: 8;
        min-width: 8;
        max-width: 8;
    }

    #fetch-start-hint,
    #fetch-end-hint {
        min-width: 12;
    }

    #board_list {
        max-width: 28;
        margin-left: 2;
    }

    #board_list:focus {
        border: tall #79a5ff;
    }

    #month-hint,
    #day-hint,
    #fetch-start-hint,
    #fetch-end-hint {
        color: #9fb2d6;
    }

    #month-row,
    #day-row,
    #fetch-start-row,
    #fetch-end-row {
        margin-left: 2;
    }

    #month_input,
    #date_input,
    #start_input,
    #end_input {
        margin-left: 0;
    }

    #month_input,
    #date_input,
    #start_input,
    #end_input,
    #run-button {
        text-style: none;
    }

    #month_input,
    #date_input,
    #start_input,
    #end_input,
    #skip_fetch,
    #strategy_custom {
        border: none;
    }

    #month_input:focus,
    #date_input:focus,
    #start_input:focus,
    #end_input:focus {
        border: none;
        background: transparent;
    }

    .field {
        margin-bottom: 0;
    }

    #log-hint {
        color: #aec4ef;
        margin: 0 0 0 1;
    }

    #run-state-hint {
        margin: 1 0 0 1;
        color: #b9d2ff;
        text-style: bold;
    }

    #run-state-hint.running {
        color: #ffd08a;
    }

    .main-title {
        color: #d7e3ff;
        text-style: bold;
        margin-bottom: 0;
    }
    """

    BINDINGS = [
        ("ctrl+r", "run_task", "执行任务"),
        ("ctrl+l", "show_logs", "查看日志"),
        ("ctrl+q", "quit", "退出"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.current_action = "latest"
        self.strategy_names = load_active_strategies()
        self.run_logs: List[str] = []
        self.log_screen: RunLogScreen | None = None
        self.task_running = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(
            "▲  日线观势  ▲\n"
            "量化策略  ·  A股日线分析  ·  自动筛选\n"
            "A股日线级别高性能量化选股程序\n"
            "author by kangdi.liu",
            id="banner",
        )
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static("功能菜单", id="sidebar-title")
                yield Button("最新交易日一键选股", id="action_latest", classes="action-btn")
                yield Button("月份批量选股", id="action_month", classes="action-btn")
                yield Button("指定日期单日选股", id="action_day", classes="action-btn")
                yield Button("仅拉取行情数据", id="action_fetch", classes="action-btn")
            with Vertical(id="main"):
                yield Static("参数配置", classes="main-title")
                yield Static("", id="action-desc")
                with Vertical(id="config-fields"):
                    with Horizontal(id="month-row", classes="field inline-row"):
                        yield Static("请填写具体月份：", id="month-hint", classes="inline-hint")
                        yield Input("202603", id="month_input", classes="inline-input")
                    with Horizontal(id="day-row", classes="field inline-row"):
                        yield Static("请填写具体日期：", id="day-hint", classes="inline-hint")
                        yield Input("2026-03-12", id="date_input", classes="inline-input")
                    with Horizontal(id="fetch-start-row", classes="field inline-row"):
                        yield Static("开始日期：", id="fetch-start-hint", classes="inline-hint")
                        yield Input("20190101", id="start_input", classes="inline-input")
                    with Horizontal(id="fetch-end-row", classes="field inline-row"):
                        yield Static("结束日期：", id="fetch-end-hint", classes="inline-hint")
                        yield Input("today", id="end_input", classes="inline-input")
                    yield Checkbox("跳过数据拉取（仅选股流程）", value=True, id="skip_fetch", classes="field")
                    yield Checkbox("指定策略多选", value=False, id="strategy_custom", classes="field")
                    selections = [(name, name, False) for name in self.strategy_names]
                    yield SelectionList[str](*selections, id="strategy_list")
                    board_options = [
                        ("gem 创业板", "gem", False),
                        ("star 科创板", "star", False),
                        ("bj 北交所", "bj", False),
                    ]
                    yield SelectionList[str](*board_options, id="board_list")
                yield Button("🚀 执行当前任务", id="run-button", variant="success")
                yield Static("状态：空闲", id="run-state-hint")
                yield Static("日志在独立面板展示（Ctrl+L 打开）", id="log-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_responsive_layout(self.size.width)
        self.update_action_buttons()
        self.update_run_button_state()
        self.update_view()
        self.query_one("#action_latest", Button).focus()
        self.write_log("欢迎使用日线观势（Textual 版）")

    def on_resize(self, event) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        """根据终端宽度自动切换布局，避免小窗口内容被遮挡。"""
        layout = self.query_one("#layout", Horizontal)
        sidebar = self.query_one("#sidebar", Vertical)
        main = self.query_one("#main", Vertical)
        banner = self.query_one("#banner", Static)
        if width < 96:
            layout.styles.layout = "vertical"
            sidebar.styles.width = "1fr"
            sidebar.styles.margin = (0, 0, 1, 0)
            sidebar.styles.height = "auto"
            main.styles.height = "1fr"
            banner.update(
                "▲  日线观势  ▲\n"
                "A股日线分析 · 自动筛选\n"
                "author by kangdi.liu"
            )
        else:
            layout.styles.layout = "horizontal"
            sidebar.styles.width = max(24, min(34, int(width * 0.27)))
            sidebar.styles.margin = (0, 0, 0, 0)
            sidebar.styles.height = "1fr"
            main.styles.height = "1fr"
            banner.update(
                "▲  日线观势  ▲\n"
                "量化策略 · A股日线分析 · 自动筛选\n"
                "author by kangdi.liu"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("action_"):
            self.current_action = button_id.replace("action_", "")
            self.update_action_buttons()
            self.update_view()
            return
        if button_id == "runlog-back":
            self.pop_screen()
            return
        if button_id == "run-button":
            self.action_run_task()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "strategy_custom":
            strategy_list = self.query_one("#strategy_list", SelectionList)
            strategy_list.display = bool(event.value) and bool(self.strategy_names)

    def update_action_buttons(self) -> None:
        for action in ("latest", "month", "day", "fetch"):
            btn = self.query_one(f"#action_{action}", Button)
            btn.remove_class("active-action")
            if action == self.current_action:
                btn.add_class("active-action")

    def update_view(self) -> None:
        desc = self.query_one("#action-desc", Static)
        config_fields = self.query_one("#config-fields", Vertical)
        run_button = self.query_one("#run-button", Button)
        month_input = self.query_one("#month_input", Input)
        month_row = self.query_one("#month-row", Horizontal)
        day_row = self.query_one("#day-row", Horizontal)
        fetch_start_row = self.query_one("#fetch-start-row", Horizontal)
        fetch_end_row = self.query_one("#fetch-end-row", Horizontal)
        date_input = self.query_one("#date_input", Input)
        start_input = self.query_one("#start_input", Input)
        end_input = self.query_one("#end_input", Input)
        skip_fetch = self.query_one("#skip_fetch", Checkbox)
        strategy_custom = self.query_one("#strategy_custom", Checkbox)
        strategy_list = self.query_one("#strategy_list", SelectionList)
        board_list = self.query_one("#board_list", SelectionList)

        month_row.display = self.current_action == "month"
        day_row.display = self.current_action == "day"
        fetch_start_row.display = self.current_action == "fetch"
        fetch_end_row.display = self.current_action == "fetch"
        board_list.display = self.current_action == "fetch"

        if self.current_action == "fetch":
            # 拉取行情模式信息更多，单独放宽，避免局促
            config_fields.styles.max_width = 62
            run_button.styles.max_width = 62
            fetch_start_row.styles.margin = (0, 0, 0, 1)
            fetch_end_row.styles.margin = (0, 0, 0, 1)
            board_list.styles.max_width = 36
            board_list.styles.margin = (1, 0, 0, 1)
        else:
            config_fields.styles.max_width = 50
            run_button.styles.max_width = 52
            fetch_start_row.styles.margin = (0, 0, 0, 2)
            fetch_end_row.styles.margin = (0, 0, 0, 2)
            board_list.styles.max_width = 28
            board_list.styles.margin = (0, 0, 0, 2)

        is_select_flow = self.current_action in {"latest", "month", "day"}
        skip_fetch.display = self.current_action in {"latest", "month"}
        strategy_custom.display = is_select_flow
        if not is_select_flow:
            strategy_custom.value = False
        strategy_list.display = is_select_flow and strategy_custom.value and bool(self.strategy_names)

        if self.current_action == "latest":
            desc.update("当前功能：最新交易日一键选股")
            month_input.placeholder = "不使用"
            date_input.placeholder = "不使用"
        elif self.current_action == "month":
            desc.update("当前功能：月份批量选股（YYYYMM 或 YYYYMM-YYYYMM）")
            month_input.placeholder = "例如：202603 或 202601-202603"
        elif self.current_action == "day":
            desc.update("当前功能：指定日期单日选股（YYYY-MM-DD）")
            date_input.placeholder = "例如：2026-03-12"
        else:
            desc.update("当前功能：仅拉取行情数据（不执行选股）")

    def get_selected_strategies(self) -> List[str]:
        strategy_custom = self.query_one("#strategy_custom", Checkbox)
        if not strategy_custom.value:
            return []
        strategy_list = self.query_one("#strategy_list", SelectionList)
        selected = list(strategy_list.selected)
        return selected

    def build_command(self) -> List[str] | None:
        if self.current_action == "latest":
            args = [str(RUN_SCRIPT)]
            if self.query_one("#skip_fetch", Checkbox).value:
                args.append("--skip-fetch")
            selected = self.get_selected_strategies()
            if selected:
                args.extend(["--strategies", *selected])
            return args

        if self.current_action == "month":
            month = self.query_one("#month_input", Input).value.strip()
            if not re.fullmatch(r"\d{6}(-\d{6})?", month):
                self.notify("月份格式错误，请使用 YYYYMM 或 YYYYMM-YYYYMM", severity="error")
                return None
            args = [str(RUN_SCRIPT), month]
            if self.query_one("#skip_fetch", Checkbox).value:
                args.append("--skip-fetch")
            selected = self.get_selected_strategies()
            if selected:
                args.extend(["--strategies", *selected])
            return args

        if self.current_action == "day":
            day = self.query_one("#date_input", Input).value.strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                self.notify("日期格式错误，请使用 YYYY-MM-DD", severity="error")
                return None
            args = [str(SELECT_SCRIPT), "--date", day]
            selected = self.get_selected_strategies()
            if selected:
                args.extend(["--strategies", *selected])
            return args

        start = self.query_one("#start_input", Input).value.strip() or "20190101"
        end = self.query_one("#end_input", Input).value.strip() or "today"
        board_list = self.query_one("#board_list", SelectionList)
        boards = list(board_list.selected)
        args = [str(FETCH_SCRIPT), "--start", start, "--end", end]
        if boards:
            args.extend(["--exclude-boards", *boards])
        return args

    def write_log(self, message: str) -> None:
        self.run_logs.append(message)
        if self.log_screen is not None and self.screen is self.log_screen:
            self.log_screen.write_line(message)

    def action_show_logs(self) -> None:
        self.open_log_screen()

    def open_log_screen(self) -> None:
        if self.log_screen is not None and self.screen is self.log_screen:
            return
        self.log_screen = RunLogScreen()
        self.push_screen(self.log_screen)
        for line in self.run_logs:
            self.log_screen.write_line(line)

    def action_run_task(self) -> None:
        if self.task_running:
            self.open_log_screen()
            return
        args = self.build_command()
        if not args:
            return
        self.task_running = True
        self.update_run_button_state()
        self.open_log_screen()
        self.write_log(f"$ {sys.executable} {' '.join(args)}")
        self.run_command_worker(args)

    @work(thread=True)
    def run_command_worker(self, args: List[str]) -> None:
        process = subprocess.Popen(
            [sys.executable, *args],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.call_from_thread(self.write_log, line.rstrip())
        code = process.wait()
        self.call_from_thread(self.write_log, f"[exit_code={code}]")
        self.call_from_thread(self._after_run, code)

    def _after_run(self, code: int) -> None:
        self.task_running = False
        self.update_run_button_state()
        if code == 0:
            self.notify("任务执行完成", severity="information")
        else:
            self.notify(f"任务执行失败 (exit={code})", severity="error")

    def update_run_button_state(self) -> None:
        run_btn = self.query_one("#run-button", Button)
        run_hint = self.query_one("#run-state-hint", Static)
        run_btn.remove_class("running")
        run_hint.remove_class("running")
        if self.task_running:
            run_btn.label = "⏳ 正在执行中（点击查看日志）"
            run_btn.add_class("running")
            run_hint.update("状态：运行中，点击查看日志（Ctrl+L）")
            run_hint.add_class("running")
        else:
            run_btn.label = "🚀 执行当前任务"
            run_hint.update("状态：空闲")


if __name__ == "__main__":
    DailyTrendApp().run()
