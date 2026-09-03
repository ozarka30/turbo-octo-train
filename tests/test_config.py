import pytest

from jptutor.config import Settings, parse_region


def test_parse_region():
    assert parse_region("0, 800, 1920,280") == (0, 800, 1920, 280)
    with pytest.raises(ValueError):
        parse_region("1,2,3")
    with pytest.raises(ValueError):
        parse_region("0,0,0,10")


def test_settings_from_env():
    s = Settings.from_env({"JPTUTOR_REGION": "1,2,3,4", "JPTUTOR_LEVEL": "advanced", "JPTUTOR_POLL_INTERVAL": "1.5"})
    assert s.region == (1, 2, 3, 4) and s.level == "advanced" and s.poll_interval == 1.5
    assert s.tutor_model == "claude-opus-5"
