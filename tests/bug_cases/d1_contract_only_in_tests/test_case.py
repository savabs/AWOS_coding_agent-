from buggy import format_duration

def test_seconds_only():
    assert format_duration(45) == "45s"

def test_minutes_and_seconds():
    assert format_duration(90) == "1m 30s"

def test_hours_minutes_seconds():
    assert format_duration(3661) == "1h 1m 1s"

def test_zero():
    assert format_duration(0) == "0s"

def test_exact_minute_omits_seconds():
    assert format_duration(120) == "2m"

def test_exact_hour_omits_the_rest():
    assert format_duration(7200) == "2h"
