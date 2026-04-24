import pytest
import responses

from zira_probe.client import ZiraClient


@pytest.fixture
def client():
    return ZiraClient(api_key="test-key-1234", base_url="https://api.zira.us/public/")


@responses.activate
def test_get_readings_sends_api_key_header_and_params(client):
    responses.add(
        method=responses.GET,
        url="https://api.zira.us/public/reading",
        json=[{"id": "r1"}, {"id": "r2"}],
        status=200,
    )

    result = client.get_readings(meter_id="999", end_time="2026-04-24T00:00:00Z")

    assert result == [{"id": "r1"}, {"id": "r2"}]
    call = responses.calls[0]
    assert call.request.headers["X-API-Key"] == "test-key-1234"
    assert "meterId=999" in call.request.url
    assert "endTime=2026-04-24T00%3A00%3A00Z" in call.request.url


@responses.activate
def test_get_readings_passes_optional_params(client):
    responses.add(
        method=responses.GET,
        url="https://api.zira.us/public/reading",
        json=[],
        status=200,
    )

    client.get_readings(
        meter_id="999",
        end_time="2026-04-24T00:00:00Z",
        start_time="2026-04-17T00:00:00Z",
        limit=50,
        last_value="abc",
    )

    url = responses.calls[0].request.url
    assert "startTime=2026-04-17T00%3A00%3A00Z" in url
    assert "limit=50" in url
    assert "lastValue=abc" in url
