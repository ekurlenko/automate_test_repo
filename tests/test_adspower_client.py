"""Тесты клиента Local API на замоканном HTTP."""

from __future__ import annotations

import pytest
import requests
import responses

from ozon_test.adspower.client import AdsPowerClient
from ozon_test.adspower.errors import ApiRejected, LocalApiUnavailable, ProfileStartError
from ozon_test.adspower.session import profile

BASE = "http://local.adspower.net:50325"
START = f"{BASE}/api/v1/browser/start"
STOP = f"{BASE}/api/v1/browser/stop"
ACTIVE = f"{BASE}/api/v1/browser/active"


@pytest.fixture
def client() -> AdsPowerClient:
    # min_interval=0: троттлинг проверяется отдельным тестом, остальным он
    # добавил бы по секунде на каждый запрос.
    return AdsPowerClient(BASE, api_key="key", min_interval=0)


def _ok_start(debug_port: str = "9222") -> dict:
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "ws": {"puppeteer": "ws://127.0.0.1:9222/devtools/browser/abc", "selenium": "127.0.0.1:9222"},
            "debug_port": debug_port,
            "webdriver": "/opt/adspower/chromedriver",
        },
    }


@responses.activate
def test_start_returns_cdp_endpoint(client):
    responses.add(responses.GET, START, json=_ok_start(), status=200)

    endpoint = client.start("profile-1")

    assert endpoint.cdp_url == "ws://127.0.0.1:9222/devtools/browser/abc"
    assert endpoint.debug_port == "9222"
    assert endpoint.user_id == "profile-1"


@responses.activate
def test_start_passes_api_key_and_quiet_flags(client):
    responses.add(responses.GET, START, json=_ok_start(), status=200)

    client.start("profile-1")

    query = responses.calls[0].request.params
    assert query["api_key"] == "key"
    assert query["open_tabs"] == "0"
    assert query["ip_tab"] == "0"


@responses.activate
def test_api_error_arrives_with_http_200(client):
    """Local API отдаёт отказы двухсоткой — смотреть надо на поле code."""
    responses.add(
        responses.GET, START, json={"code": -1, "msg": "user_id not exists"}, status=200
    )

    with pytest.raises(ApiRejected) as exc:
        client.start("nope")
    assert exc.value.code == -1
    assert len(responses.calls) == 1  # логический отказ не ретраим


@responses.activate
def test_start_without_debug_port_is_an_error(client):
    """Профиль «запустился», но подключаться некуда — это провал, а не успех."""
    responses.add(responses.GET, START, json={"code": 0, "data": {"ws": {}}}, status=200)

    with pytest.raises(ProfileStartError):
        client.start("profile-1")


@responses.activate
def test_unavailable_local_api_is_retried(client):
    responses.add(responses.GET, ACTIVE, body=requests.ConnectionError("нет соединения"))
    responses.add(responses.GET, ACTIVE, body=requests.ConnectionError("нет соединения"))
    responses.add(responses.GET, ACTIVE, json={"code": 0, "data": {"status": "Active"}}, status=200)

    assert client.is_active("profile-1") is True
    assert len(responses.calls) == 3


@responses.activate
def test_unavailable_local_api_gives_up_with_a_readable_message(client):
    for _ in range(3):
        responses.add(responses.GET, ACTIVE, body=requests.ConnectionError("нет соединения"))

    with pytest.raises(LocalApiUnavailable, match="AdsPower запущено"):
        client.is_active("profile-1")


@responses.activate
def test_stop_never_raises(client):
    """stop вызывается из finally — его падение затёрло бы исходную ошибку."""
    responses.add(responses.GET, STOP, json={"code": -1, "msg": "not running"}, status=200)

    client.stop("profile-1")  # не должно бросить


@responses.activate
def test_profile_context_stops_even_when_scenario_fails(client):
    responses.add(responses.GET, START, json=_ok_start(), status=200)
    responses.add(responses.GET, STOP, json={"code": 0, "msg": "success"}, status=200)

    with pytest.raises(RuntimeError):
        with profile(client, "profile-1"):
            raise RuntimeError("сценарий упал")

    assert responses.calls[-1].request.url.startswith(STOP)


def test_throttle_keeps_one_request_per_second():
    """Local API отдаёт `Too many request`, если стучаться чаще раза в секунду."""
    ticks = iter([0.0, 0.0, 0.2, 0.2])
    slept: list[float] = []
    throttled = AdsPowerClient(
        BASE, min_interval=1.0, clock=lambda: next(ticks), sleeper=slept.append
    )

    throttled._throttle()
    throttled._throttle()

    assert slept == [pytest.approx(0.8)]  # первый прошёл сразу, второй добрал 0.8 с
