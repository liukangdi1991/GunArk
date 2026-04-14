"""
A股交易回测系统
功能：读取选股结果，计算持有N天后的收益
规则：
  - T+1 开盘价买入
  - T+8 收盘价卖出
  - 涨停不能买入（作废）
  - 跌停不能卖出（顺延）
  - 节假日顺延
"""

import json
import pandas as pd
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

console = Console()


class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, initial_capital=100000, buy_delay=1, sell_delay=8):
        self.initial_capital = initial_capital
        self.buy_delay = buy_delay  # T+N 买入
        self.sell_delay = sell_delay  # T+M 卖出
        self.stock_names = self._load_stock_names()
    
    def _load_stock_names(self) -> dict:
        """加载股票名称"""
        names = {}
        try:
            df = pd.read_csv('stocklist.csv')
            for _, row in df.iterrows():
                names[str(row['symbol']).zfill(6)] = row['name']
        except:
            pass
        return names
    
    def get_stock_name(self, code: str) -> str:
        """获取股票名称"""
        return self.stock_names.get(code, '')
    
    def parse_selection_file(self, file_path: str) -> Dict[str, List[str]]:
        """解析选股结果文件（JSON 格式）"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        strategies = {}
        for strategy_name, strategy_data in data.items():
            if isinstance(strategy_data, dict) and 'stocks' in strategy_data:
                strategies[strategy_name] = strategy_data['stocks']
        
        return strategies
    
    def load_stock_data(self, code: str) -> pd.DataFrame:
        """加载股票数据"""
        data_file = f'data/{code}.csv'
        
        if not os.path.exists(data_file):
            return None
        
        try:
            df = pd.read_csv(data_file)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            return df
        except Exception as e:
            return None
    
    def is_limit_up(self, row) -> bool:
        """判断是否涨停（收盘价等于最高价）"""
        return abs(row['close'] - row['high']) < 0.001
    
    def is_limit_down(self, row) -> bool:
        """判断是否跌停（收盘价等于最低价）"""
        return abs(row['close'] - row['low']) < 0.001
    
    def calculate_return(self, code: str, select_date: datetime) -> dict:
        """计算单只股票持有收益"""
        df = self.load_stock_data(code)
        if df is None:
            return None
        
        # 找到选股日期及之后的数据
        df_after = df[df['date'] >= select_date].reset_index(drop=True)
        
        if len(df_after) < 1:
            return None
        
        # T+1 日开盘价买入
        buy_idx = self.buy_delay
        if buy_idx >= len(df_after):
            return None
        
        buy_row = df_after.iloc[buy_idx]
        buy_date = buy_row['date']
        buy_price = buy_row['open']
        
        # 检查是否涨停（不能买入）
        if self.is_limit_up(buy_row):
            return {
                'code': code,
                'name': self.get_stock_name(code),
                'status': 'skip',
                'reason': '涨停无法买入',
                'skip_date': buy_date.strftime('%m-%d'),
            }
        
        # T+8 日收盘价卖出（可能顺延）
        sell_idx = self.sell_delay
        max_attempts = 10  # 最多尝试10天
        attempts = 0
        
        while sell_idx < len(df_after) and attempts < max_attempts:
            sell_row = df_after.iloc[sell_idx]
            
            # 检查是否跌停（不能卖出，顺延）
            if self.is_limit_down(sell_row):
                sell_idx += 1
                attempts += 1
                continue
            
            # 可以卖出
            sell_date = sell_row['date']
            sell_price = sell_row['close']
            
            # 计算收益
            shares = int(self.initial_capital / buy_price / 100) * 100  # 整手
            if shares <= 0:
                return None
            
            cost = shares * buy_price
            revenue = shares * sell_price
            profit = revenue - cost
            return_pct = (sell_price / buy_price - 1) * 100
            
            return {
                'code': code,
                'name': self.get_stock_name(code),
                'status': 'success',
                'buy_date': buy_date.strftime('%m-%d'),
                'buy_price': buy_price,
                'sell_date': sell_date.strftime('%m-%d'),
                'sell_price': sell_price,
                'shares': shares,
                'cost': cost,
                'revenue': revenue,
                'profit': profit,
                'return_pct': return_pct,
                'delay_days': sell_idx - self.sell_delay,  # 延迟天数
            }
        
        # 数据不足，无法卖出
        return {
            'code': code,
            'name': self.get_stock_name(code),
            'status': 'skip',
            'reason': '数据不足无法卖出',
            'skip_date': buy_date.strftime('%m-%d'),
        }
    
    def run(self, selection_file: str) -> dict:
        """运行回测"""
        # 解析选股文件
        strategies = self.parse_selection_file(selection_file)
        
        if not strategies:
            console.print('[red]未找到选股结果[/red]')
            return {}
        
        # 获取选股日期
        select_date = self._extract_date(selection_file)
        
        console.print(Panel(
            f'[bold]选股文件:[/bold] {selection_file}\n'
            f'[bold]选股日期:[/bold] {select_date.strftime("%Y-%m-%d")}\n'
            f'[bold]初始资金:[/bold] {self.initial_capital:,.2f} 元\n'
            f'[bold]买入规则:[/bold] T+{self.buy_delay} 开盘价\n'
            f'[bold]卖出规则:[/bold] T+{self.sell_delay} 收盘价（跌停顺延）\n'
            f'[bold]限制规则:[/bold] 涨停不能买入，跌停不能卖出',
            title='📊 回测参数',
            border_style='blue'
        ))
        
        all_results = {}
        
        # 遍历每个策略
        for strategy_name, codes in strategies.items():
            console.print(f'\n[bold cyan]🎯 {strategy_name}[/bold cyan]')
            
            strategy_results = []
            skip_results = []
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task(f"回测 {strategy_name}...", total=len(codes))
                
                for code in codes:
                    result = self.calculate_return(code, select_date)
                    if result:
                        if result['status'] == 'success':
                            strategy_results.append(result)
                        else:
                            skip_results.append(result)
                    progress.advance(task)
            
            all_results[strategy_name] = {
                'results': strategy_results,
                'skips': skip_results,
                'select_date': select_date,
            }
        
        return all_results
    
    def _extract_date(self, file_path: str) -> datetime:
        """从文件名提取日期"""
        match = re.search(r'(\d{8})', file_path)
        if match:
            return datetime.strptime(match.group(1), '%Y%m%d')
        return datetime.now()
    
    def print_results(self, all_results: dict):
        """打印回测结果"""
        console.print('\n')
        console.rule('[bold green]📈 回测结果报告[/bold green]')
        console.print('\n')
        
        total_profit = 0
        total_cost = 0
        total_count = 0
        win_count = 0
        
        for strategy_name, data in all_results.items():
            results = data['results']
            skips = data['skips']
            
            if not results and not skips:
                console.print(f'[yellow]{strategy_name}: 无有效数据[/yellow]')
                continue
            
            # 创建表格
            table = Table(
                title=f'🎯 {strategy_name}',
                box=box.ROUNDED,
                show_header=True,
                header_style='bold magenta'
            )
            table.add_column('股票代码', style='cyan', width=10)
            table.add_column('股票名称', width=8)
            table.add_column('买入日', width=8)
            table.add_column('买入价', justify='right', width=10)
            table.add_column('卖出日', width=8)
            table.add_column('卖出价', justify='right', width=10)
            table.add_column('收益金额', justify='right', width=12)
            table.add_column('收益率', justify='right', width=10)
            table.add_column('备注', width=10)
            
            strategy_profit = 0
            strategy_cost = 0
            strategy_win = 0
            
            for r in results:
                profit = r['profit']
                return_pct = r['return_pct']
                
                # A股红涨绿跌
                if return_pct > 0:
                    style = 'red'  # A股红色表示涨
                    symbol = '📈'
                    strategy_win += 1
                    win_count += 1
                else:
                    style = 'green'  # A股绿色表示跌
                    symbol = '📉'
                
                delay_note = f'延迟{r["delay_days"]}天' if r['delay_days'] > 0 else ''
                
                table.add_row(
                    r['code'],
                    r['name'][:4] if r['name'] else '',
                    r['buy_date'],
                    f'{r["buy_price"]:.2f}',
                    r['sell_date'],
                    f'{r["sell_price"]:.2f}',
                    f'{symbol} {profit:+,.2f}',
                    f'[{style}]{return_pct:+.2f}%[/{style}]',
                    delay_note
                )
                
                strategy_profit += profit
                strategy_cost += r['cost']
                total_profit += profit
                total_cost += r['cost']
                total_count += 1
            
            # 计算平均收益
            avg_return_pct = (strategy_profit / strategy_cost * 100) if strategy_cost > 0 else 0
            win_rate = (strategy_win / len(results) * 100) if results else 0
            
            table.add_section()
            table.add_row(
                '[bold]统计[/bold]',
                '',
                '',
                '',
                '',
                '',
                f'[bold]{strategy_profit:+,.2f}[/bold]',
                f'[bold]{avg_return_pct:+.2f}%[/bold]',
                ''
            )
            
            console.print(table)
            console.print(f'  📊 有效交易: {len(results)} | 盈利: {strategy_win} | 亏损: {len(results)-strategy_win} | 胜率: {win_rate:.1f}%')
            
            # 显示跳过的股票（带日期）
            if skips:
                skip_info = [f"{s['code']}({s['name'][:2] if s['name'] else ''})-{s['skip_date']}-{s['reason']}" for s in skips]
                console.print(f'  ⚠️ 跳过: {", ".join(skip_info)}')
        
        # 总体统计
        console.rule('[bold blue]📊 总体统计[/bold blue]')
        
        summary_table = Table(box=box.SIMPLE, show_header=False)
        summary_table.add_column('指标', style='bold')
        summary_table.add_column('数值')
        
        avg_total_return = (total_profit / total_cost * 100) if total_cost > 0 else 0
        total_win_rate = (win_count / total_count * 100) if total_count > 0 else 0
        
        summary_table.add_row('总交易数', str(total_count))
        summary_table.add_row('盈利次数', f'[red]{win_count}[/red]')  # A股红色
        summary_table.add_row('亏损次数', f'[green]{total_count - win_count}[/green]')  # A股绿色
        summary_table.add_row('胜率', f'{total_win_rate:.1f}%')
        summary_table.add_row('总投入', f'{total_cost:,.2f} 元')
        summary_table.add_row('总盈亏', f'[bold {"red" if total_profit >= 0 else "green"}]{total_profit:+,.2f} 元[/bold {"red" if total_profit >= 0 else "green"}]')
        summary_table.add_row('总收益率', f'[bold {"red" if avg_total_return >= 0 else "green"}]{avg_total_return:+.2f}%[/bold {"red" if avg_total_return >= 0 else "green"}]')
        
        console.print(summary_table)
    
    def save_results_to_file(self, all_results: dict, output_dir: str):
        """保存回测结果到文件"""
        os.makedirs(output_dir, exist_ok=True)
        
        # 生成日期
        date_str = None
        for data in all_results.values():
            if 'select_date' in data:
                date_str = data['select_date'].strftime('%Y%m%d')
                break
        
        if not date_str:
            date_str = datetime.now().strftime('%Y%m%d')
        
        # 保存详细结果
        output_file = os.path.join(output_dir, f'backtest_{date_str}.json')
        
        # 转换为可序列化的格式
        serializable_results = {}
        for strategy_name, data in all_results.items():
            serializable_results[strategy_name] = {
                'select_date': data['select_date'].strftime('%Y-%m-%d'),
                'results': data['results'],
                'skips': data['skips'],
                'summary': {
                    'total_trades': len(data['results']),
                    'winning_trades': sum(1 for r in data['results'] if r['return_pct'] > 0),
                    'losing_trades': sum(1 for r in data['results'] if r['return_pct'] <= 0),
                    'total_profit': sum(r['profit'] for r in data['results']),
                    'avg_return': (sum(r['profit'] for r in data['results']) / sum(r['cost'] for r in data['results']) * 100) if data['results'] else 0,
                }
            }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(serializable_results, f, ensure_ascii=False, indent=2)
        
        console.print(f'\n[green]✅ 详细结果已保存到: {output_file}[/green]')
        
        # 生成可读的文本报告
        report_file = os.path.join(output_dir, f'backtest_{date_str}_report.txt')
        self._generate_text_report(all_results, report_file, date_str)
        
        return output_file, report_file
    
    def _generate_text_report(self, all_results: dict, report_file: str, date_str: str):
        """生成文本报告"""
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(f"{'='*60}\n")
            f.write(f"A股回测报告\n")
            f.write(f"回测日期: {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}\n")
            f.write(f"{'='*60}\n\n")
            
            total_profit = 0
            total_cost = 0
            total_count = 0
            win_count = 0
            
            for strategy_name, data in all_results.items():
                results = data['results']
                skips = data['skips']
                
                f.write(f"\n{'─'*60}\n")
                f.write(f"策略: {strategy_name}\n")
                f.write(f"{'─'*60}\n\n")
                
                if results:
                    f.write(f"{'股票代码':<10}{'股票名称':<8}{'买入日':<10}{'买入价':<10}{'卖出日':<10}{'卖出价':<10}{'收益率':<10}{'收益金额':<12}\n")
                    f.write(f"{'-'*80}\n")
                    
                    strategy_profit = 0
                    strategy_cost = 0
                    strategy_win = 0
                    
                    for r in results:
                        name = r['name'][:4] if r['name'] else ''
                        f.write(f"{r['code']:<10}{name:<8}{r['buy_date']:<10}{r['buy_price']:<10.2f}{r['sell_date']:<10}{r['sell_price']:<10.2f}{r['return_pct']:<+10.2f}{r['profit']:<+12.2f}\n")
                        
                        strategy_profit += r['profit']
                        strategy_cost += r['cost']
                        if r['return_pct'] > 0:
                            strategy_win += 1
                            win_count += 1
                        total_profit += r['profit']
                        total_cost += r['cost']
                        total_count += 1
                    
                    avg_return = (strategy_profit / strategy_cost * 100) if strategy_cost > 0 else 0
                    win_rate = (strategy_win / len(results) * 100) if results else 0
                    
                    f.write(f"{'-'*80}\n")
                    f.write(f"有效交易: {len(results)} | 盈利: {strategy_win} | 亏损: {len(results)-strategy_win} | 胜率: {win_rate:.1f}%\n")
                    f.write(f"策略收益: {strategy_profit:+,.2f} 元 | 平均收益率: {avg_return:+.2f}%\n")
                else:
                    f.write("无有效交易\n")
                
                if skips:
                    f.write(f"\n跳过的股票:\n")
                    for s in skips:
                        f.write(f"  {s['code']} {s['name'][:4] if s['name'] else ''} ({s['skip_date']}) - {s['reason']}\n")
            
            # 总体统计
            f.write(f"\n{'='*60}\n")
            f.write(f"总体统计\n")
            f.write(f"{'='*60}\n\n")
            
            avg_total_return = (total_profit / total_cost * 100) if total_cost > 0 else 0
            total_win_rate = (win_count / total_count * 100) if total_count > 0 else 0
            
            f.write(f"总交易数: {total_count}\n")
            f.write(f"盈利次数: {win_count}\n")
            f.write(f"亏损次数: {total_count - win_count}\n")
            f.write(f"胜率: {total_win_rate:.1f}%\n")
            f.write(f"总投入: {total_cost:,.2f} 元\n")
            f.write(f"总盈亏: {total_profit:+,.2f} 元\n")
            f.write(f"总收益率: {avg_total_return:+.2f}%\n")
        
        console.print(f'[green]✅ 文本报告已保存到: {report_file}[/green]')


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='A股交易回测系统')
    parser.add_argument('--file', '-f', help='选股结果文件路径')
    parser.add_argument('--date', '-d', help='选股日期 (YYYYMMDD)')
    parser.add_argument('--latest', '-l', action='store_true', help='使用最新的选股结果')
    parser.add_argument('--capital', '-c', type=float, default=100000, help='初始资金 (默认: 100000)')
    parser.add_argument('--buy-delay', type=int, default=1, help='买入延迟天数 (默认: 1)')
    parser.add_argument('--sell-delay', type=int, default=8, help='卖出延迟天数 (默认: 8)')
    parser.add_argument('--month', '-m', help='批量回测整月 (YYYYMM)')
    parser.add_argument('--output', '-o', default='backtest_results', help='输出目录 (默认: backtest_results)')
    
    args = parser.parse_args()
    
    # 显示标题
    console.print(Panel(
        '[bold]A股交易回测系统 v2.0[/bold]\n'
        '支持涨停跌停限制 | 节假日顺延 | 批量回测',
        title='📊 Stock Backtest',
        border_style='green'
    ))
    
    # 批量回测整月
    if args.month:
        results_dir = 'backtest_results'
        if not os.path.exists(results_dir):
            console.print('[red]未找到选股结果目录[/red]')
            return
        
        # 查找该月的所有选股文件
        files = [f for f in os.listdir(results_dir) if f.startswith(args.month) and f.endswith('.json')]
        files.sort()
        
        if not files:
            console.print(f'[red]未找到 {args.month} 月的选股结果[/red]')
            return
        
        console.print(f'[cyan]找到 {len(files)} 个选股文件[/cyan]')
        
        engine = BacktestEngine(
            initial_capital=args.capital,
            buy_delay=args.buy_delay,
            sell_delay=args.sell_delay
        )
        
        # 按月汇总
        monthly_results = {}
        
        for file in files:
            file_path = os.path.join(results_dir, file)
            console.print(f'\n[bold]回测: {file}[/bold]')
            
            results = engine.run(file_path)
            
            # 合并结果
            for strategy_name, data in results.items():
                if strategy_name not in monthly_results:
                    monthly_results[strategy_name] = {
                        'results': [],
                        'skips': [],
                        'select_date': data['select_date']
                    }
                monthly_results[strategy_name]['results'].extend(data['results'])
                monthly_results[strategy_name]['skips'].extend(data['skips'])
        
        # 打印月度汇总
        console.print('\n')
        console.rule(f'[bold green]📈 {args.month} 月度回测汇总[/bold green]')
        engine.print_results(monthly_results)
        
        # 保存结果
        output_dir = os.path.join(args.output, args.month)
        engine.save_results_to_file(monthly_results, output_dir)
        
        return
    
    # 单日回测
    selection_file = None
    
    if args.file:
        selection_file = args.file
    elif args.date:
        selection_file = f'backtest_results/{args.date}.json'
    elif args.latest:
        results_dir = 'backtest_results'
        if os.path.exists(results_dir):
            files = [f for f in os.listdir(results_dir) if f.endswith('.json')]
            if files:
                files.sort(reverse=True)
                selection_file = os.path.join(results_dir, files[0])
    
    if not selection_file or not os.path.exists(selection_file):
        console.print('[red]未找到选股结果文件[/red]')
        console.print('请使用以下参数指定:')
        console.print('  --file FILE     指定文件路径')
        console.print('  --date DATE     指定日期 (YYYYMMDD)')
        console.print('  --latest        使用最新结果')
        console.print('  --month MONTH   批量回测整月 (YYYYMM)')
        console.print('')
        console.print('[yellow]提示: 请先运行选股程序生成结果[/yellow]')
        console.print('  python select_stock.py --date 2026-03-25')
        return
    
    # 运行回测
    engine = BacktestEngine(
        initial_capital=args.capital,
        buy_delay=args.buy_delay,
        sell_delay=args.sell_delay
    )
    
    results = engine.run(selection_file)
    engine.print_results(results)
    
    # 保存结果
    engine.save_results_to_file(results, args.output)


if __name__ == '__main__':
    main()