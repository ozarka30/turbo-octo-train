from jptutor.usage import UsageMeter, estimate_cost


def test_estimate_cost_opus5_with_cache():
    # 1000 uncached input, 9000 cache reads, 1000 cache writes (1h), 500 output
    cost = estimate_cost("claude-opus-5", input_tokens=1000, cache_read=9000, cache_write=1000, output_tokens=500, ttl="1h")
    expected = (1000 * 5 + 9000 * 0.5 + 1000 * 10 + 500 * 25) / 1e6
    assert abs(cost - expected) < 1e-12
    assert estimate_cost("some-unknown-model", input_tokens=1, cache_read=0, cache_write=0, output_tokens=0) is None


def test_meter_summary_and_listeners():
    m = UsageMeter()
    seen = []
    m.listeners.append(seen.append)

    class U:
        input_tokens, cache_read_input_tokens, cache_creation_input_tokens, output_tokens = 100, 900, 0, 50

    m.record_api("api", "claude-opus-5", "lesson", U(), "1h")
    assert len(seen) == 1
    t = m.totals()
    assert t["calls"] == 1 and round(t["cached_pct"]) == 90
    assert "90% served from cache" in m.summary() and "$" in m.summary()
    assert "no calls" in UsageMeter().summary()
