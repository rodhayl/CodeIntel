from __future__ import annotations

import pytest

from codeintel.safe_artifacts import strict_json_loads


@pytest.mark.parametrize(
    "raw",
    [r'{"value":"\ud800"}', r'{"\udfff":1}', r'{"nested":["ok","\ud800"]}'],
)
def test_strict_release_json_rejects_decoded_unpaired_surrogates(raw):
    with pytest.raises(ValueError, match="non-UTF-8 text"):
        strict_json_loads(raw)


def test_strict_release_json_accepts_valid_supplementary_pair():
    assert strict_json_loads(r'{"emoji":"\ud83d\ude00"}') == {"emoji": "😀"}


def test_strict_release_json_keeps_normal_values():
    assert strict_json_loads('{"ok":[1,true,null,"text"]}') == {
        "ok": [1, True, None, "text"]
    }
