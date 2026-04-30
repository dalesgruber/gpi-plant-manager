from datetime import datetime, timezone

from zira_dashboard import _zira_persist
from zira_dashboard.leaderboard import StationTotal
from zira_dashboard.stations import Station


def _make_total(meter_id="m1", units=100):
    s = Station(meter_id=meter_id, name=f"WC-{meter_id}", category="Dismantler", cell="Recycling")
    dt = datetime(2026, 4, 30, 14, 0, tzinfo=timezone.utc)
    return StationTotal(
        station=s,
        units=units,
        reading_count=12,
        truncated=False,
        downtime_minutes=15,
        active_minutes=400,
        last_reading_at=dt,
        last_status="Working",
        samples=((dt, 5), (dt, 10)),
        active_intervals=((dt, dt),),
    )


def test_serialize_then_deserialize_round_trips():
    original = _make_total()
    payload = _zira_persist._serialize_total(original)
    restored = _zira_persist._deserialize_total(payload)
    assert restored.station.meter_id == original.station.meter_id
    assert restored.station.name == original.station.name
    assert restored.station.category == original.station.category
    assert restored.station.cell == original.station.cell
    assert restored.units == original.units
    assert restored.reading_count == original.reading_count
    assert restored.truncated == original.truncated
    assert restored.downtime_minutes == original.downtime_minutes
    assert restored.active_minutes == original.active_minutes
    assert restored.last_reading_at == original.last_reading_at
    assert restored.last_status == original.last_status
    assert restored.samples == original.samples
    assert restored.active_intervals == original.active_intervals


def test_serialize_handles_none_dt_fields():
    s = Station(meter_id="m2", name="WC-m2", category="Repair", cell="Recycling")
    total = StationTotal(
        station=s,
        units=0,
        reading_count=0,
        truncated=False,
        downtime_minutes=0,
        active_minutes=0,
        last_reading_at=None,
        last_status=None,
        samples=(),
        active_intervals=(),
    )
    payload = _zira_persist._serialize_total(total)
    restored = _zira_persist._deserialize_total(payload)
    assert restored.last_reading_at is None
    assert restored.last_status is None
    assert restored.samples == ()
    assert restored.active_intervals == ()
