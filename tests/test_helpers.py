"""Tests for pkg.helpers — the atomic JSON write and small time helpers."""

import json
from datetime import datetime

import pytest

from pkg import helpers


class Unserializable:
    """Fails partway through json.dump, after the encoder has begun writing."""


class TestDump:
    def test_round_trips(self, tmp_path):
        target = tmp_path / "out.json"
        helpers.dump(target, {"a": [1, 2, 3]})
        assert json.loads(target.read_text()) == {"a": [1, 2, 3]}

    def test_creates_no_leftover_temp_file(self, tmp_path):
        target = tmp_path / "out.json"
        helpers.dump(target, {"a": 1})
        assert list(tmp_path.glob("*.tmp")) == []

    def test_failed_write_preserves_the_previous_file(self, tmp_path):
        """A serialization failure must leave the old contents readable —
        this is why today.json is written via a temp file and os.replace."""
        target = tmp_path / "out.json"
        helpers.dump(target, {"good": True})
        before = target.read_text()

        # A large prefix ensures the encoder has already written bytes to the
        # temp file before it hits the value it cannot serialize.
        payload = {str(i): i for i in range(500)}
        payload["boom"] = Unserializable()
        with pytest.raises(TypeError):
            helpers.dump(target, payload)

        assert target.read_text() == before
        assert json.loads(target.read_text()) == {"good": True}

    def test_failed_write_cleans_up_the_temp_file(self, tmp_path):
        target = tmp_path / "out.json"
        with pytest.raises(TypeError):
            helpers.dump(target, {"boom": Unserializable()})
        assert list(tmp_path.glob("*.tmp")) == []

    def test_overwrites_in_place(self, tmp_path):
        target = tmp_path / "out.json"
        helpers.dump(target, {"v": 1})
        helpers.dump(target, {"v": 2})
        assert json.loads(target.read_text()) == {"v": 2}


class TestLoad:
    def test_missing_file_returns_default(self, tmp_path):
        assert helpers.load(tmp_path / "nope.json") == {}
        assert helpers.load(tmp_path / "nope.json", default=[]) == []

    def test_corrupt_file_returns_default_rather_than_raising(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert helpers.load(bad) == {}


class TestTimeHelpers:
    def test_date_str_formats_a_timestamp(self):
        t = datetime(2026, 3, 17, 14, 30).timestamp() * 1000
        assert helpers.date_str(int(t)) == "2026-03-17"

    def test_date_str_defaults_to_today(self):
        assert helpers.date_str() == datetime.now().strftime("%Y-%m-%d")

    @pytest.mark.parametrize("h,m,s,expected", [
        (0, 0, 0, 0.0),
        (9, 30, 0, 570.0),
        (23, 59, 0, 1439.0),
        (12, 0, 30, 720.5),
    ])
    def test_minutes_since_midnight(self, h, m, s, expected):
        t = datetime.now().replace(hour=h, minute=m, second=s, microsecond=0)
        assert helpers.minutes_since_midnight(int(t.timestamp() * 1000)) == expected
