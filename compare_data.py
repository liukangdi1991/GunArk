"""
对比 db (parquet) 和 data (csv) 数据是否一致
"""
import polars as pl
import pandas as pd
import numpy as np
from pathlib import Path

# 选择几只股票进行对比
test_codes = ["000001", "000002", "300432", "600753", "688218"]

for code in test_codes:
    csv_path = Path(f"data/{code}.csv")
    parquet_path = Path(f"db/{code}.parquet")
    
    if not csv_path.exists() or not parquet_path.exists():
        print(f"⚠️  {code}: 文件不存在")
        continue
    
    # 读取 CSV (pandas)
    df_csv = pd.read_csv(csv_path)
    df_csv.columns = [c.lower() for c in df_csv.columns]
    df_csv['date'] = pd.to_datetime(df_csv['date']).dt.date
    
    # 读取 Parquet (polars)
    df_parquet = pl.read_parquet(parquet_path)
    df_parquet = df_parquet.rename({col: col.lower() for col in df_parquet.columns})
    if df_parquet["date"].dtype == pl.Utf8:
        df_parquet = df_parquet.with_columns(pl.col("date").str.to_date())
    
    # 转换 polars 为 pandas 进行比较
    df_parquet_pd = df_parquet.to_pandas()
    df_parquet_pd['date'] = pd.to_datetime(df_parquet_pd['date']).dt.date
    
    # 对比
    print(f"\n{'='*60}")
    print(f"股票: {code}")
    print(f"CSV 行数: {len(df_csv)}, Parquet 行数: {len(df_parquet_pd)}")
    print(f"CSV 列: {list(df_csv.columns)}")
    print(f"Parquet 列: {list(df_parquet_pd.columns)}")
    
    # 检查列是否一致
    if set(df_csv.columns) != set(df_parquet_pd.columns):
        print(f"❌ 列名不一致!")
        print(f"  CSV 独有: {set(df_csv.columns) - set(df_parquet_pd.columns)}")
        print(f"  Parquet 独有: {set(df_parquet_pd.columns) - set(df_csv.columns)}")
        continue
    
    # 按日期排序后比较
    df_csv_sorted = df_csv.sort_values('date').reset_index(drop=True)
    df_parquet_sorted = df_parquet_pd.sort_values('date').reset_index(drop=True)
    
    # 检查日期范围
    print(f"CSV 日期范围: {df_csv_sorted['date'].min()} ~ {df_csv_sorted['date'].max()}")
    print(f"Parquet 日期范围: {df_parquet_sorted['date'].min()} ~ {df_parquet_sorted['date'].max()}")
    
    # 检查行数
    if len(df_csv_sorted) != len(df_parquet_sorted):
        print(f"❌ 行数不一致!")
        continue
    
    # 比较数值列
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount']
    all_match = True
    for col in numeric_cols:
        if col in df_csv_sorted.columns:
            # 处理可能的浮点数精度问题
            csv_vals = df_csv_sorted[col].astype(float).values
            parquet_vals = df_parquet_sorted[col].astype(float).values
            
            if not np.allclose(csv_vals, parquet_vals, rtol=1e-5, atol=1e-8, equal_nan=True):
                diff = np.abs(csv_vals - parquet_vals)
                max_diff = np.nanmax(diff)
                print(f"❌ {col} 列不一致! 最大差异: {max_diff}")
                all_match = False
    
    if all_match:
        print(f"✅ {code}: 数据完全一致!")
    else:
        print(f"❌ {code}: 数据不一致!")

print(f"\n{'='*60}")
print("对比完成")