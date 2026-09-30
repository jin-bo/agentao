"""A rule pattern that cannot be compiled falls back to literal equality.

``re.error`` always did. ``a{99999999999}`` raises OverflowError and deep
nesting RecursionError instead; both passed the rule validator and then
escaped ``decide()``, ending every turn that called the tool.
"""

import pytest

from agentao.permissions import PermissionDecision, PermissionEngine

_BAD = ["a{99999999999}", "(" * 5000 + ")" * 5000, "Read|Write("]


@pytest.mark.parametrize("pattern", _BAD)
def test_bad_args_pattern_is_compared_literally(tmp_path, pattern):
    rule = {"tool": "read_file", "args": {"file_path": pattern}, "action": "deny"}
    e = PermissionEngine(project_root=tmp_path, rules=[rule])
    assert e.decide("read_file", {"file_path": "x.txt"}) is None
    assert e.decide("read_file", {"file_path": pattern}) == PermissionDecision.DENY


@pytest.mark.parametrize("pattern", _BAD)
def test_bad_tool_pattern_is_compared_literally(tmp_path, pattern):
    e = PermissionEngine(project_root=tmp_path, rules=[{"tool": pattern, "action": "deny"}])
    assert e.decide("read_file", {"file_path": "x.txt"}) is None
