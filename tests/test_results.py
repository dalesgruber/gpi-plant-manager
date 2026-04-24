import json
from pathlib import Path

from zira_probe.results import ProbeResult, redact_api_key, write_raw_log


def test_probe_result_defaults():
    result = ProbeResult(
        name="demo",
        category="reads",
        endpoint="GET /reading",
        status="success",
    )
    assert result.observations == []
    assert result.request_summary == {}
    assert result.response_summary == {}
    assert result.raw_log_path == ""


def test_redact_api_key_keeps_last_four():
    assert redact_api_key("abcd-efgh-ijkl-mnop-qrst-1234") == "****1234"


def test_redact_api_key_short_input_returns_asterisks():
    assert redact_api_key("xyz") == "****"


def test_write_raw_log_writes_file_and_returns_path(tmp_path):
    path = write_raw_log(
        results_dir=tmp_path,
        name="my_probe",
        request={"method": "GET", "url": "u", "headers": {"X-API-Key": "****1234"}},
        response={"status_code": 200, "body": {"ok": True}},
    )

    assert path.parent == tmp_path
    assert path.name.startswith("my_probe_")
    assert path.suffix == ".json"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["request"]["method"] == "GET"
    assert data["response"]["status_code"] == 200
