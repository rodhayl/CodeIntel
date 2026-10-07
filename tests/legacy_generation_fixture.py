"""Construct historical IDs only for compatibility/collision regression fixtures."""


def legacy_generation_id(sequence, snapshot_hash):
    return f'gen_{sequence:06d}_{snapshot_hash[:12]}'
