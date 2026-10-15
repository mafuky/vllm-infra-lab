import pytest

from scripts.cost import api_cost_per_request, breakeven_requests_per_day, self_hosted_cost_per_request


def test_self_hosted_cost():
    assert self_hosted_cost_per_request(1.92, 4.0, 1.0) == pytest.approx(1.92 / 3600 / 4.0)
    assert self_hosted_cost_per_request(1.92, 4.0, 0.3) == pytest.approx(1.92 / 3600 / 4.0 / 0.3)


def test_api_cost():
    assert api_cost_per_request(1024, 256, 0.5, 2.0) == pytest.approx((1024 * 0.5 + 256 * 2.0) / 1e6)


def test_breakeven():
    assert breakeven_requests_per_day(1.92, 0.001) == pytest.approx(1.92 * 24 / 0.001)


def test_rejects_zero_throughput():
    with pytest.raises(ValueError):
        self_hosted_cost_per_request(1.92, 0.0, 1.0)
    with pytest.raises(ValueError):
        self_hosted_cost_per_request(1.92, 4.0, 0.0)
