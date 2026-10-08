"""PolicyProvider: reading the policy file, with candidates and failure modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from claimpilot_mcp_corp.policy import MAX_POLICY_BYTES, PolicyProvider, PolicyUnavailableError


def test_reads_the_file_as_utf_8(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    path.write_text("limit: ₹5,000\n", encoding="utf-8")
    provider = PolicyProvider(path)
    assert provider.available()
    assert provider.locate() == path
    assert provider.text() == "limit: ₹5,000\n"


def test_the_first_existing_candidate_wins(tmp_path: Path):
    missing = tmp_path / "missing.yaml"
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    assert PolicyProvider(missing, first, second).text() == "first"
    assert PolicyProvider(missing, second).text() == "second"


def test_edits_are_picked_up_without_a_restart(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    path.write_text("v1", encoding="utf-8")
    provider = PolicyProvider(path)
    assert provider.text() == "v1"
    path.write_text("v2", encoding="utf-8")
    assert provider.text() == "v2"


def test_no_candidates_means_not_available(tmp_path: Path):
    provider = PolicyProvider(tmp_path / "nope.yaml", tmp_path)  # a directory is not a file
    assert not provider.available()
    assert provider.locate() is None
    with pytest.raises(PolicyUnavailableError, match="policy not available"):
        provider.text()
    with pytest.raises(PolicyUnavailableError, match="policy not available"):
        PolicyProvider().text()


def test_the_error_does_not_leak_server_paths(tmp_path: Path):
    with pytest.raises(PolicyUnavailableError) as caught:
        PolicyProvider(tmp_path / "secret-location" / "policy.yaml").text()
    assert "secret-location" not in str(caught.value)


def test_oversized_files_are_refused(tmp_path: Path):
    path = tmp_path / "big.yaml"
    path.write_bytes(b"x" * (MAX_POLICY_BYTES + 1))
    with pytest.raises(PolicyUnavailableError, match="too large"):
        PolicyProvider(path).text()


def test_undecodable_files_are_reported_as_unreadable(tmp_path: Path):
    path = tmp_path / "binary.yaml"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(PolicyUnavailableError, match="unreadable"):
        PolicyProvider(path).text()
