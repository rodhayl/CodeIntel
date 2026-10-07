import json
import os
import subprocess
import sys

import pytest

from codeintel import __version__
from codeintel.lab.retrieval import SCHEMA, SELECTIONS, SCOPE, serialize, verify_packet


def _empty_packet():
    return {
        "schema": SCHEMA, "scope": SCOPE, "version": __version__, "status": "EMPTY",
        "snapshot_sha256": "0" * 64, "refreshed": False,
        "limits": {"packet_bytes": 4096, "fragments": 5, "candidates": 32, "unit": "UTF-8 bytes"},
        "selection": SELECTIONS["ranked"], "selected": [],
        "omitted": {"duplicate": 0, "budget": 0, "limit": 0},
    }


def _verify(repo, packet):
    return subprocess.run(
        [sys.executable, "-m", "codeintel.lab.cli", "verify", "--repo", str(repo), "--packet", str(packet)],
        capture_output=True, timeout=2, check=False,
    )


def test_ranking_descriptor_does_not_claim_verified_call_binding():
    assert "heuristic syntax neighbors" in SELECTIONS["ranked"]
    assert "verified" not in SELECTIONS["ranked"]


def test_legacy_descriptor_remains_verifiable_without_being_emitted(tmp_path):
    packet = _empty_packet()
    packet["selection"] = "existing lexical/exact-symbol/verified syntax graph ranking; whole fragments"
    verify_packet(tmp_path, packet)


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO boundary")
def test_packet_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    packet = tmp_path / "packet.json"
    os.mkfifo(packet)
    result = _verify(tmp_path, packet)
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == "INVALID_PACKET"
    assert result.stdout == b""


def test_packet_symlink_is_rejected_even_when_target_is_a_valid_packet(tmp_path):
    regular = tmp_path / "valid.json"
    regular.write_bytes(serialize(_empty_packet()))
    linked = tmp_path / "linked.json"
    linked.symlink_to(regular)
    result = _verify(tmp_path, linked)
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == "INVALID_PACKET"
    assert result.stdout == b""


def test_oversized_packet_is_rejected_before_json_read(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_bytes(b" " * 8193)
    result = _verify(tmp_path, packet)
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == "INVALID_PACKET"


def test_regular_packet_still_verifies(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_bytes(serialize(_empty_packet()))
    result = _verify(tmp_path, packet)
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "VERIFIED"


@pytest.mark.parametrize('kind', ['selected', 'nested-limit'])
def test_verify_cli_rejects_duplicate_json_keys(tmp_path, kind):
    payload = serialize(_empty_packet()).decode()
    if kind == 'selected':
        payload = payload.replace('"selected":[]', '"selected":[{"source":"UNVERIFIED CONTENT"}],"selected":[]')
    else:
        payload = payload.replace('"packet_bytes":4096', '"packet_bytes":999999,"packet_bytes":4096')
    packet = tmp_path / 'duplicate-keys.json'
    packet.write_text(payload)
    result = _verify(tmp_path, packet)
    assert result.returncode == 2
    assert result.stdout == b''
    assert json.loads(result.stderr)['code'] == 'INVALID_PACKET'


@pytest.mark.parametrize('number', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_verify_cli_rejects_nonfinite_json_numbers(tmp_path, number):
    packet = tmp_path / 'nonfinite.json'
    packet.write_text(serialize(_empty_packet()).decode().replace('"packet_bytes":4096', '"packet_bytes":' + number))
    result = _verify(tmp_path, packet)
    assert result.returncode == 2 and result.stdout == b''
    assert json.loads(result.stderr)['code'] == 'INVALID_PACKET'
