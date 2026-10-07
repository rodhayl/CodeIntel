"""One packet cannot claim two file versions for the same physical path."""
import pytest

from codeintel.lab import retrieval as lab


FIRST = b'def first(): return "needle_common FIRST_OLD"\ndef second(): return "needle_common SECOND_OLD"\n'
SECOND = FIRST.replace(b"FIRST_OLD", b"FIRST_NEW").replace(b"SECOND_OLD", b"SECOND_NEW")


def _packet(tmp_path, repeated=False):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    source = (b"def repeated(): return 1\n\ndef repeated(): return 1\n" if repeated else FIRST)
    (repo / "a.py").write_bytes(source)
    lab.index_repository(repo, state)
    packet, _ = lab.query_repository(repo, state, "repeated" if repeated else "needle_common",
                                     max_bytes=8192, limit=5)
    return repo, packet


@pytest.mark.parametrize("legacy", [False, True])
def test_conflicting_primary_file_hashes_reject_before_source_io(tmp_path, monkeypatch, legacy):
    repo, packet = _packet(tmp_path)
    assert len(packet["selected"]) == 2
    packet["selected"][1]["file_sha256"] = lab.digest(SECOND)
    if legacy:
        packet["schema"] = lab.LEGACY_SCHEMA
        del packet["scope"]
        for row in packet["selected"]:
            del row["aliases"], row["coverage"]

    def forbidden(*args):
        raise AssertionError("self-contradictory packet reached source IO")

    monkeypatch.setattr(lab, "source_bytes", forbidden)
    with pytest.raises(lab.LabError, match="INVALID_PACKET"):
        lab.verify_packet(repo, packet)


def test_alias_cannot_claim_a_second_version_of_its_owner_path(tmp_path, monkeypatch):
    repo, packet = _packet(tmp_path, repeated=True)
    alias = packet["selected"][0]["aliases"][0]
    assert alias["path"] == packet["selected"][0]["path"]
    alias["file_sha256"] = "f" * 64
    monkeypatch.setattr(lab, "source_bytes", lambda *args: pytest.fail("alias conflict reached source IO"))
    with pytest.raises(lab.LabError, match="INVALID_PACKET"):
        lab.verify_packet(repo, packet)


def test_mutation_between_reads_cannot_validate_a_mixed_version_packet(tmp_path, monkeypatch):
    repo, packet = _packet(tmp_path)
    row = packet["selected"][1]
    literal = lab.literal_span(SECOND, row["span"])
    row.update(source=literal.decode(), source_sha256=lab.digest(literal), file_sha256=lab.digest(SECOND))
    original = lab.source_bytes
    reads = []

    def changing_source(root, path):
        raw = original(root, path)
        reads.append(path)
        (repo / "a.py").write_bytes(SECOND)
        return raw

    monkeypatch.setattr(lab, "source_bytes", changing_source)
    with pytest.raises(lab.LabError, match="INVALID_PACKET"):
        lab.verify_packet(repo, packet)
    assert reads == []


def test_each_verify_reads_one_file_once_and_keeps_no_persistent_cache(tmp_path, monkeypatch):
    repo, packet = _packet(tmp_path)
    original = lab.source_bytes
    reads = []

    def observed(root, path):
        reads.append(path)
        return original(root, path)

    monkeypatch.setattr(lab, "source_bytes", observed)
    lab.verify_packet(repo, packet)
    assert reads == ["a.py"]
    (repo / "a.py").write_bytes(SECOND)
    with pytest.raises(lab.LabError, match="STALE_SOURCE"):
        lab.verify_packet(repo, packet)
    assert reads == ["a.py", "a.py"]
