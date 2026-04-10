"""
Polars 数据管理器
- 负责使用 Polars 读取和写入股票数据
- 将 CSV 数据转换为高效的 Parquet 格式
- 提供快速的数据加载接口
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import polars as pl
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel
from rich import box

console = Console()


class PolarsDataManager:
    """使用 Polars 管理股票数据"""
    
    def __init__(self, data_dir: Path = Path("./data"), parquet_dir: Path = Path("./data_parquet")):
        """
        Parameters
        ----------
        data_dir : Path
            CSV 文件目录
        parquet_dir : Path
            Parquet 文件输出目录
        """
        self.data_dir = Path(data_dir)
        self.parquet_dir = Path(parquet_dir)
        self.parquet_dir.mkdir(parents=True, exist_ok=True)
    
    def csv_to_parquet_single(self, code: str) -> bool:
        """将单个 CSV 文件转换为 Parquet 格式"""
        csv_path = self.data_dir / f"{code}.csv"
        parquet_path = self.parquet_dir / f"{code}.parquet"
        
        if not csv_path.exists():
            return False
        
        try:
            # 使用 Polars 读取 CSV
            df = pl.read_csv(
                csv_path,
                try_parse_dates=True,
                schema_overrides={
                    "date": pl.Utf8,
                    "open": pl.Float64,
                    "close": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "volume": pl.Float64,
                }
            )
            
            # 确保日期列正确解析
            if df["date"].dtype == pl.Utf8:
                df = df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
            
            # 按日期排序
            df = df.sort("date")
            
            # 写入 Parquet（压缩格式，读取更快）
            df.write_parquet(parquet_path, compression="zstd")
            
            return True
        except Exception as e:
            console.print(f"[red]❌ {code} 转换失败: {e}[/red]")
            return False
    
    def batch_convert_csv_to_parquet(self, codes: Optional[List[str]] = None) -> Dict[str, int]:
        """批量将 CSV 转换为 Parquet"""
        if codes is None:
            codes = [f.stem for f in self.data_dir.glob("*.csv")]
        
        success_count = 0
        fail_count = 0
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]转换 CSV → Parquet...", total=len(codes))
            
            for code in codes:
                if self.csv_to_parquet_single(code):
                    success_count += 1
                else:
                    fail_count += 1
                progress.advance(task)
        
        return {"success": success_count, "fail": fail_count}
    
    def load_single_stock(self, code: str, use_parquet: bool = True) -> Optional[pl.DataFrame]:
        """加载单只股票数据"""
        if use_parquet:
            parquet_path = self.parquet_dir / f"{code}.parquet"
            if parquet_path.exists():
                return pl.read_parquet(parquet_path)
        
        # 回退到 CSV
        csv_path = self.data_dir / f"{code}.csv"
        if csv_path.exists():
            df = pl.read_csv(csv_path, try_parse_dates=True)
            if df["date"].dtype == pl.Utf8:
                df = df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
            return df.sort("date")
        
        return None
    
    def load_multiple_stocks(
        self, 
        codes: List[str], 
        use_parquet: bool = True
    ) -> Dict[str, pl.DataFrame]:
        """批量加载多只股票数据"""
        frames: Dict[str, pl.DataFrame] = {}
        
        for code in codes:
            df = self.load_single_stock(code, use_parquet=use_parquet)
            if df is not None and not df.is_empty():
                frames[code] = df
        
        return frames
    
    def load_all_stocks(self, use_parquet: bool = True) -> Dict[str, pl.DataFrame]:
        """加载所有股票数据"""
        if use_parquet:
            parquet_files = list(self.parquet_dir.glob("*.parquet"))
            if parquet_files:
                codes = [f.stem for f in parquet_files]
            else:
                codes = [f.stem for f in self.data_dir.glob("*.csv")]
        else:
            codes = [f.stem for f in self.data_dir.glob("*.csv")]
        
        return self.load_multiple_stocks(codes, use_parquet=use_parquet)
    
    def get_stock_codes(self, use_parquet: bool = True) -> List[str]:
        """获取所有股票代码"""
        if use_parquet:
            parquet_files = list(self.parquet_dir.glob("*.parquet"))
            if parquet_files:
                return [f.stem for f in parquet_files]
        
        return [f.stem for f in self.data_dir.glob("*.csv")]
    
    def get_data_stats(self) -> Dict:
        """获取数据统计信息"""
        csv_count = len(list(self.data_dir.glob("*.csv")))
        parquet_count = len(list(self.parquet_dir.glob("*.parquet")))
        
        return {
            "csv_count": csv_count,
            "parquet_count": parquet_count,
            "csv_dir": str(self.data_dir.resolve()),
            "parquet_dir": str(self.parquet_dir.resolve()),
        }


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="Polars 数据管理器 - CSV 转 Parquet")
    parser.add_argument("--data-dir", default="./data", help="CSV 数据目录")
    parser.add_argument("--parquet-dir", default="./data_parquet", help="Parquet 输出目录")
    parser.add_argument("--codes", nargs="*", help="指定股票代码（空格分隔），不指定则处理全部")
    parser.add_argument("--stats", action="store_true", help="显示数据统计信息")
    args = parser.parse_args()
    
    manager = PolarsDataManager(
        data_dir=Path(args.data_dir),
        parquet_dir=Path(args.parquet_dir),
    )
    
    if args.stats:
        stats = manager.get_data_stats()
        panel = Panel(
            f"[bold cyan]📊 数据统计[/bold cyan]\n\n"
            f"  CSV 文件数: [bold green]{stats['csv_count']}[/bold green]\n"
            f"  Parquet 文件数: [bold green]{stats['parquet_count']}[/bold green]\n"
            f"  CSV 目录: [dim]{stats['csv_dir']}[/dim]\n"
            f"  Parquet 目录: [dim]{stats['parquet_dir']}[/dim]",
            title="[bold blue]数据概览[/bold blue]",
            border_style="blue",
            box=box.ROUNDED
        )
        console.print(panel)
        return
    
    # 显示转换信息
    codes = args.codes
    if codes is None:
        codes = manager.get_stock_codes(use_parquet=False)
    
    info_panel = Panel(
        f"[bold cyan]🔄 开始转换[/bold cyan]\n\n"
        f"  待转换文件数: [bold green]{len(codes)}[/bold green]\n"
        f"  源目录: [dim]{manager.data_dir.resolve()}[/dim]\n"
        f"  目标目录: [dim]{manager.parquet_dir.resolve()}[/dim]\n"
        f"  压缩格式: [bold]zstd[/bold]",
        title="[bold blue]CSV → Parquet[/bold blue]",
        border_style="blue",
        box=box.ROUNDED
    )
    console.print(info_panel)
    console.print()
    
    # 执行转换
    start_time = time.time()
    result = manager.batch_convert_csv_to_parquet(codes)
    elapsed = time.time() - start_time
    
    # 显示结果
    console.print()
    result_panel = Panel(
        f"[bold cyan]✅ 转换完成[/bold cyan]\n\n"
        f"  成功: [bold green]{result['success']}[/bold green] 个文件\n"
        f"  失败: [bold red]{result['fail']}[/bold red] 个文件\n"
        f"  耗时: [bold yellow]{elapsed:.2f}[/bold yellow] 秒\n"
        f"  速度: [bold]{result['success']/elapsed:.1f}[/bold] 文件/秒",
        title="[bold blue]转换结果[/bold blue]",
        border_style="green" if result['fail'] == 0 else "yellow",
        box=box.ROUNDED
    )
    console.print(result_panel)


if __name__ == "__main__":
    main()