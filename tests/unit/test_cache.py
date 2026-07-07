from data_collect_dag.cache import SessionInputCache
from data_collect_dag.models import CachePolicy, ImageFrame
from tests.unit.conftest import make_meta

import numpy as np


def test_cache_append_and_nearest_query():
    cache = SessionInputCache({"cam": CachePolicy(5, 1.0)})
    for ts in (1000, 1200, 1400):
        frame = ImageFrame(meta=make_meta("cam", "image", ts), image_bgr=np.zeros((1, 1, 3), dtype=np.uint8), encoding="bgr8", width=1, height=1)
        cache.append("cam", frame)
    matched = cache.query_nearest("cam", 1190, 1.0)
    assert matched.meta.source_timestamp_ns == 1200


def test_cache_drops_oldest_when_full():
    cache = SessionInputCache({"cam": CachePolicy(2, 10.0)})
    for ts in (1000, 2000, 3000):
        frame = ImageFrame(meta=make_meta("cam", "image", ts), image_bgr=np.zeros((1, 1, 3), dtype=np.uint8), encoding="bgr8", width=1, height=1)
        dropped = cache.append("cam", frame)
    assert "cache_max_frames" in dropped
    assert cache.query_nearest("cam", 1000, 0.0) is None
