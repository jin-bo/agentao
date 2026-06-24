"""Tests for the ``/goal`` duration parser (agentao/cli/duration.py)."""

import pytest

from agentao.cli.duration import DurationParseError, parse_duration


@pytest.mark.parametrize(
    "text,expected",
    [
        ("90s", 90),
        ("30m", 1800),
        ("2h", 7200),
        ("1h30m", 5400),
        ("2h30m15s", 9015),
        ("1h 30m", 5400),   # internal whitespace tolerated
        ("  45s  ", 45),    # surrounding whitespace stripped
        ("2H", 7200),       # case-insensitive
        ("90M", 5400),
    ],
)
def test_parse_valid(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "30",       # unit-less — the headline rejection
        "100",
        "",
        "   ",
        "2x",       # unknown unit
        "abc",
        "1h30",     # trailing segment without a unit
        "30m45",
        "0s",       # zero total
        "0h0m0s",
        "-5m",      # leading sign is junk to the tokenizer
    ],
)
def test_parse_rejects(text):
    with pytest.raises(DurationParseError):
        parse_duration(text)


def test_none_rejected():
    with pytest.raises(DurationParseError):
        parse_duration(None)  # type: ignore[arg-type]
