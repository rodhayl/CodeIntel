from __future__ import annotations

import math

import pytest

from codeintel.bounded_process import run_bounded_process


@pytest.mark.parametrize(
    "timeout_seconds",
    [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, True],
)
def test_bounded_process_rejects_non_finite_or_non_positive_timeout_before_spawn(
    tmp_path, timeout_seconds
):
    with pytest.raises(ValueError, match="finite positive"):
        run_bounded_process(
            ["definitely-not-a-real-executable"],
            cwd=str(tmp_path),
            timeout_seconds=timeout_seconds,
            stdout_limit=1,
            stderr_limit=1,
        )


def test_bounded_process_accepts_finite_timeout_shape(tmp_path):
    # Shape validation must not reject a normal finite float. The executable error
    # proves argument validation completed and subprocess creation was attempted.
    with pytest.raises(FileNotFoundError):
        run_bounded_process(
            ["definitely-not-a-real-executable"],
            cwd=str(tmp_path),
            timeout_seconds=0.01,
            stdout_limit=1,
            stderr_limit=1,
        )
