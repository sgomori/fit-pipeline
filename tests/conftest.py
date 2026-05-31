"""Shared pytest fixtures for fit-pipeline tests."""

from pathlib import Path

import pytest

from fit_pipeline.config import Config

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_FIT = FIXTURES_DIR / "sample_run.fit"
SAMPLE_EXPECTED = FIXTURES_DIR / "sample_run_expected.json"


@pytest.fixture
def base_config() -> Config:
    """Minimal config for tests that don't need delivery."""
    return Config(
        dry_run=True,
        include_streams=True,
        stream_sample_rate=1,
        exclude_gps=True,
        exclude_device_info=True,
    )


@pytest.fixture
def analytics_config(base_config: Config) -> Config:
    """Config with analytics parameters for StandardAnalyticsProcessor tests."""
    base_config.threshold_hr = 162
    base_config.resting_hr = 48
    base_config.max_hr = 185
    base_config.trimp_gender = "male"
    base_config.pace_zone_easy = 360        # 6:00/km
    base_config.pace_zone_moderate = 330    # 5:30/km
    base_config.pace_zone_threshold = 300   # 5:00/km
    base_config.threshold_pace = 300        # 5:00/km — for rTSS / NGP
    return base_config


@pytest.fixture
def sample_fit_path() -> Path:
    """Path to the anonymized sample FIT fixture.

    Skip the test if the fixture file doesn't exist yet.
    """
    if not SAMPLE_FIT.exists():
        pytest.skip(f"Sample FIT fixture not yet available: {SAMPLE_FIT}")
    return SAMPLE_FIT
