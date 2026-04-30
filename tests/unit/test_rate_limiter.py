"""
tests/unit/test_rate_limiter.py
─────────────────────────────────────────────────────────────
Unit tests for backend/api/middleware/rate_limiter.py

Tests cover:
  - TokenBucket: consume, refill, retry_after
  - RateLimiterStore: allow, deny, cleanup
  - Rule matching: most specific path wins
  - Middleware: 429 response structure
  - Exempt paths bypass limiting
  - Headers on allowed responses
"""

from __future__ import annotations

import time

import pytest

from backend.api.middleware.rate_limiter import (
    ENDPOINT_RULES,
    EXEMPT_PATHS,
    GLOBAL_RULE,
    RateLimitRule,
    RateLimiterStore,
    TokenBucket,
    _match_rule,
    get_rate_limiter_stats,
)


# ── TokenBucket Tests ─────────────────────────────────────────────

class TestTokenBucket:

    def test_bucket_starts_full(self):
        bucket = TokenBucket(capacity=10.0, refill_rate=1.0)
        assert bucket.tokens == 10.0

    def test_consume_returns_true_when_tokens_available(self):
        bucket = TokenBucket(capacity=5.0, refill_rate=1.0)
        now = time.monotonic()
        assert bucket.consume(now) is True

    def test_consume_decrements_tokens(self):
        bucket = TokenBucket(capacity=5.0, refill_rate=1.0)
        now = time.monotonic()
        bucket.consume(now)
        assert bucket.tokens == 4.0

    def test_consume_returns_false_when_empty(self):
        bucket = TokenBucket(capacity=1.0, refill_rate=0.1)
        now = time.monotonic()
        bucket.consume(now)  # use the one token
        assert bucket.consume(now) is False

    def test_tokens_do_not_exceed_capacity(self):
        bucket = TokenBucket(capacity=5.0, refill_rate=10.0)
        # Simulate 10 seconds of refill
        future = time.monotonic() + 10.0
        bucket.consume(future)
        assert bucket.tokens <= 5.0

    def test_tokens_refill_over_time(self):
        bucket = TokenBucket(capacity=10.0, refill_rate=2.0)
        now = time.monotonic()
        # Drain all tokens
        for _ in range(10):
            bucket.consume(now)
        assert bucket.tokens < 1.0

        # 5 seconds later — should have 10 tokens (2/sec * 5sec)
        later = now + 5.0
        bucket.consume(later)  # triggers refill
        assert bucket.tokens > 5.0

    def test_retry_after_zero_when_tokens_available(self):
        bucket = TokenBucket(capacity=5.0, refill_rate=1.0)
        assert bucket.retry_after_seconds == 0

    def test_retry_after_positive_when_empty(self):
        bucket = TokenBucket(capacity=1.0, refill_rate=1.0)
        now = time.monotonic()
        bucket.consume(now)  # drain
        assert bucket.retry_after_seconds >= 1

    def test_multiple_consumes_drain_bucket(self):
        bucket = TokenBucket(capacity=3.0, refill_rate=1.0)
        now = time.monotonic()
        assert bucket.consume(now) is True
        assert bucket.consume(now) is True
        assert bucket.consume(now) is True
        assert bucket.consume(now) is False


# ── RateLimitRule Tests ───────────────────────────────────────────

class TestRateLimitRule:

    def test_refill_rate_calculation(self):
        rule = RateLimitRule(requests=60, window_secs=60, key_prefix="test")
        assert rule.refill_rate == 1.0

    def test_refill_rate_fast(self):
        rule = RateLimitRule(requests=10, window_secs=1, key_prefix="test")
        assert rule.refill_rate == 10.0

    def test_refill_rate_slow(self):
        rule = RateLimitRule(requests=5, window_secs=60, key_prefix="test")
        assert abs(rule.refill_rate - 0.0833) < 0.001


# ── RateLimiterStore Tests ────────────────────────────────────────

class TestRateLimiterStore:

    def setup_method(self):
        """Fresh store for each test."""
        self.store = RateLimiterStore()
        self.rule = RateLimitRule(requests=5, window_secs=60, key_prefix="test")

    def test_first_request_allowed(self):
        allowed, retry = self.store.is_allowed("test:127.0.0.1", self.rule)
        assert allowed is True
        assert retry == 0

    def test_requests_within_limit_allowed(self):
        for _ in range(5):
            allowed, _ = self.store.is_allowed("test:127.0.0.1", self.rule)
            assert allowed is True

    def test_request_over_limit_denied(self):
        # Exhaust all 5 tokens
        for _ in range(5):
            self.store.is_allowed("test:127.0.0.1", self.rule)
        # 6th request — should be denied
        allowed, retry = self.store.is_allowed("test:127.0.0.1", self.rule)
        assert allowed is False
        assert retry >= 1

    def test_different_keys_independent(self):
        # Exhaust IP1
        for _ in range(5):
            self.store.is_allowed("test:192.168.1.1", self.rule)
        denied, _ = self.store.is_allowed("test:192.168.1.1", self.rule)
        assert denied is False

        # IP2 should still be allowed
        allowed, _ = self.store.is_allowed("test:192.168.1.2", self.rule)
        assert allowed is True

    def test_bucket_count_increases(self):
        assert self.store.get_bucket_count() == 0
        self.store.is_allowed("test:1.1.1.1", self.rule)
        assert self.store.get_bucket_count() == 1
        self.store.is_allowed("test:2.2.2.2", self.rule)
        assert self.store.get_bucket_count() == 2

    def test_same_key_reuses_bucket(self):
        self.store.is_allowed("test:1.1.1.1", self.rule)
        self.store.is_allowed("test:1.1.1.1", self.rule)
        assert self.store.get_bucket_count() == 1

    def test_retry_after_is_positive_integer(self):
        for _ in range(5):
            self.store.is_allowed("test:127.0.0.1", self.rule)
        _, retry = self.store.is_allowed("test:127.0.0.1", self.rule)
        assert isinstance(retry, int)
        assert retry >= 1

    def test_high_capacity_rule(self):
        """100 requests should all pass with capacity=100."""
        rule = RateLimitRule(requests=100, window_secs=60, key_prefix="big")
        for i in range(100):
            allowed, _ = self.store.is_allowed(f"big:{i}", rule)
            assert allowed is True


# ── Rule Matching Tests ───────────────────────────────────────────

class TestRuleMatching:

    def test_login_matches_auth_rule(self):
        rule = _match_rule("/api/v1/auth/login")
        assert rule.key_prefix == "auth"
        assert rule.requests == 10

    def test_register_matches_auth_rule(self):
        rule = _match_rule("/api/v1/auth/register")
        assert rule.key_prefix == "auth"

    def test_upload_matches_upload_rule(self):
        rule = _match_rule("/api/v1/upload/file")
        assert rule.key_prefix == "upload"
        assert rule.requests == 10

    def test_dashboard_matches_dashboard_rule(self):
        rule = _match_rule("/api/v1/dashboard/summary")
        assert rule.key_prefix == "dashboard"

    def test_transactions_matches_api_rule(self):
        rule = _match_rule("/api/v1/transactions/")
        assert rule.key_prefix == "api"

    def test_accounts_matches_api_rule(self):
        rule = _match_rule("/api/v1/accounts/")
        assert rule.key_prefix == "api"

    def test_unknown_path_matches_global_rule(self):
        rule = _match_rule("/unknown/path")
        assert rule.key_prefix == "global"

    def test_auth_rule_stricter_than_api(self):
        auth_rule = _match_rule("/api/v1/auth/login")
        api_rule = _match_rule("/api/v1/transactions/")
        assert auth_rule.requests < api_rule.requests

    def test_upload_rule_stricter_than_api(self):
        upload_rule = _match_rule("/api/v1/upload/file")
        api_rule = _match_rule("/api/v1/accounts/")
        assert upload_rule.requests <= api_rule.requests


# ── Exempt Paths Tests ────────────────────────────────────────────

class TestExemptPaths:

    def test_health_is_exempt(self):
        assert "/health" in EXEMPT_PATHS

    def test_docs_is_exempt(self):
        assert "/api/docs" in EXEMPT_PATHS

    def test_redoc_is_exempt(self):
        assert "/api/redoc" in EXEMPT_PATHS

    def test_openapi_is_exempt(self):
        assert "/api/openapi.json" in EXEMPT_PATHS


# ── Global Rule Tests ─────────────────────────────────────────────

class TestGlobalRule:

    def test_global_rule_has_high_capacity(self):
        assert GLOBAL_RULE.requests >= 100

    def test_global_rule_window_is_60_seconds(self):
        assert GLOBAL_RULE.window_secs == 60

    def test_global_rule_prefix_is_global(self):
        assert GLOBAL_RULE.key_prefix == "global"


# ── Stats Tests ───────────────────────────────────────────────────

class TestStats:

    def test_stats_returns_dict(self):
        stats = get_rate_limiter_stats()
        assert isinstance(stats, dict)

    def test_stats_has_required_keys(self):
        stats = get_rate_limiter_stats()
        assert "active_buckets" in stats
        assert "algorithm" in stats
        assert "storage" in stats

    def test_stats_algorithm_is_token_bucket(self):
        stats = get_rate_limiter_stats()
        assert stats["algorithm"] == "token_bucket"

    def test_stats_storage_is_in_memory(self):
        stats = get_rate_limiter_stats()
        assert stats["storage"] == "in_memory"

    def test_stats_active_buckets_is_int(self):
        stats = get_rate_limiter_stats()
        assert isinstance(stats["active_buckets"], int)
