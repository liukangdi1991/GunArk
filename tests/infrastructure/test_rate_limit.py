import time
import pytest
from trendradar.infrastructure.tushare.rate_limit import TokenBucket


def test_acquire_immediately_when_burst_available():
    bucket = TokenBucket(rate_per_min=450, burst=450)
    assert bucket.acquire() is True


def test_acquire_blocks_until_refill():
    bucket = TokenBucket(rate_per_min=60, burst=2)  # 1 token/sec
    assert bucket.acquire() is True
    assert bucket.acquire() is True
    t0 = time.monotonic()  # 墙钟可能回跳（WSL2 时钟跳变）→ 用单调钟
    assert bucket.acquire(timeout=2.0) is True  # waits ~1s for refill
    assert 0.8 <= time.monotonic() - t0 < 2.0


def test_acquire_returns_false_on_cancel():
    bucket = TokenBucket(rate_per_min=60, burst=1)
    assert bucket.acquire() is True
    assert bucket.acquire(timeout=1.0, cancel_check=lambda: True) is False


def test_rate_does_not_exceed_ceiling():
    bucket = TokenBucket(rate_per_min=60, burst=1)  # 1/sec
    acquired = 0
    for _ in range(5):
        if bucket.acquire(timeout=0.2):
            acquired += 1
        time.sleep(0.3)
    assert acquired <= 3  # 5 * 0.3s window, 1/sec ceiling
