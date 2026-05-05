"""Contract tests for ``agentao.redact.mask_secret``.

The default arguments matter — they're load-bearing for any future audit-log
display. A regression that, say, lowered ``floor`` to 4 would expose a short
JWT in full to whoever has read access to the log. Pin the contract here.
"""

import pytest

from agentao.redact import mask_secret


def test_long_value_keeps_head_and_tail():
    """A typical OpenAI-style key shows ``sk-A`` + ``...`` + last 4 chars."""
    assert mask_secret("sk-Abcd1234567890XYZK") == "sk-A...XYZK"


def test_below_floor_is_fully_masked():
    """Short tokens never reveal head/tail — the whole value becomes asterisks."""
    short = "abc12345"  # len 8, default floor=12
    masked = mask_secret(short)
    assert masked == "********"
    assert len(masked) == len(short)


def test_at_floor_starts_revealing():
    """Exactly at floor=12, head+tail are exposed."""
    val = "abcd56789xyz"  # len 12
    assert mask_secret(val) == "abcd...9xyz"


def test_none_renders_placeholder():
    assert mask_secret(None) == "(not set)"


def test_empty_string_renders_placeholder():
    assert mask_secret("") == "(not set)"


def test_custom_placeholder():
    assert mask_secret(None, placeholder="<unconfigured>") == "<unconfigured>"


def test_head_tail_floor_can_be_overridden():
    val = "abcdefghijklmnop"  # 16 chars
    assert mask_secret(val, head=2, tail=2) == "ab...op"


def test_head_only_drops_trailing_dots():
    """tail=0 produces a clean ``head...`` shape."""
    assert mask_secret("abcdefghijklmnop", head=4, tail=0) == "abcd..."


def test_head_plus_tail_overlapping_falls_back_to_full_mask():
    """Misconfig: head+tail >= length must NOT echo the value."""
    val = "abcdefgh"  # len 8
    assert mask_secret(val, head=4, tail=4, floor=0) == "********"


def test_negative_args_rejected():
    with pytest.raises(ValueError):
        mask_secret("abcdefghijklmn", head=-1)
    with pytest.raises(ValueError):
        mask_secret("abcdefghijklmn", tail=-1)
    with pytest.raises(ValueError):
        mask_secret("abcdefghijklmn", floor=-1)
