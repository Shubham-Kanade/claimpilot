"""get_llm picks the backend from LLM_MODE / LLM_RECORD."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from claimpilot.config import Settings
from claimpilot.llm.anthropic_llm import AnthropicLLM
from claimpilot.llm.client import get_llm
from claimpilot.llm.errors import LLMAuthError
from claimpilot.llm.fake import FakeLLM
from claimpilot.llm.replay import RecordReplayLLM


def settings(tmp_path: Path, **kwargs) -> Settings:
    return Settings(replay_dir=tmp_path, anthropic_api_key=SecretStr("sk-test"), **kwargs)


def test_fake_mode(tmp_path):
    llm = get_llm(settings(tmp_path, llm_mode="fake"))
    assert isinstance(llm, FakeLLM)
    assert llm.mode == "fake"


def test_replay_mode_reads_the_configured_dir(tmp_path, models_registry):
    llm = get_llm(settings(tmp_path, llm_mode="replay"), registry=models_registry)
    assert isinstance(llm, RecordReplayLLM)
    assert llm.mode == "replay"
    assert llm.replay_dir == tmp_path


def test_live_mode(tmp_path):
    llm = get_llm(settings(tmp_path, llm_mode="live"))
    assert isinstance(llm, AnthropicLLM)
    assert llm.mode == "live"


def test_live_mode_with_record_wraps_the_live_client(tmp_path):
    llm = get_llm(settings(tmp_path, llm_mode="live", llm_record=True))
    assert isinstance(llm, RecordReplayLLM)
    assert llm.mode == "live"


def test_live_mode_with_record_replays_at_the_configured_pace(tmp_path):
    """The hybrid demo profile: recordings still replay at the paced speed, not instantly."""
    llm = get_llm(settings(tmp_path, llm_mode="live", llm_record=True, replay_latency_scale=0.6))
    assert isinstance(llm, RecordReplayLLM)
    assert llm._latency_scale == 0.6


def test_live_mode_without_key_fails_fast(tmp_path):
    with pytest.raises(LLMAuthError):
        get_llm(Settings(llm_mode="live", replay_dir=tmp_path, anthropic_api_key=None))


def test_replay_is_the_default_mode():
    assert Settings.model_fields["llm_mode"].default == "replay"
    assert Settings.model_fields["llm_record"].default is False
