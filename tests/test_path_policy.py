"""Unit tests for PathPolicy containment.

Verifies that PathPolicy correctly accepts paths inside the project root and
rejects every flavor of escape: ``..`` traversal, absolute path outside the
root, leaf-symlink escape, and parent-symlink escape. Also pins the legacy
behavior where a tool with no bound ``working_directory`` snapshots
``Path.cwd()`` per call.

Each test gets its own ``tmp_path`` (pytest fixture). To represent
"outside" the project root, we use ``tmp_path/project`` as the root and
``tmp_path/outside`` as a sibling — both inside the test's isolated
``tmp_path`` so concurrent or sequential tests cannot interfere with each
other's fixtures.
"""

from __future__ import annotations

import os

import pytest

from agentao.security import PathPolicy, PathPolicyError


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def outside(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    return out


# ---------------------------------------------------------------------------
# Stub tool for ``for_tool`` testing
# ---------------------------------------------------------------------------


class _StubTool:
    def __init__(self, working_directory):
        self.working_directory = working_directory


# ---------------------------------------------------------------------------
# contain_file
# ---------------------------------------------------------------------------


def test_contain_file_accepts_relative_inside(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    resolved = policy.contain_file("subdir/note.txt")
    assert resolved == (project_root / "subdir/note.txt").resolve()


def test_contain_file_accepts_absolute_inside(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    target = project_root / "deep" / "note.txt"
    assert policy.contain_file(str(target)) == target.resolve()


def test_contain_file_rejects_dotdot_escape(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    with pytest.raises(PathPolicyError):
        policy.contain_file("../outside.txt")


def test_contain_file_rejects_absolute_outside(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    with pytest.raises(PathPolicyError):
        policy.contain_file("/etc/passwd")


def test_contain_file_rejects_leaf_symlink_escape(project_root, outside):
    """A symlink whose *target* is outside the root must be rejected even
    when the symlink itself sits inside the root."""
    target_outside = outside / "attacker.txt"
    target_outside.write_text("attacker")
    link = project_root / "link.txt"
    os.symlink(target_outside, link)

    policy = PathPolicy(project_root=project_root.resolve())
    with pytest.raises(PathPolicyError):
        policy.contain_file("link.txt")


def test_contain_file_rejects_parent_symlink_escape(project_root, outside):
    """When the parent dir itself is a symlink to outside, the resolved
    target naturally lands outside the root."""
    bad_parent = project_root / "badparent"
    os.symlink(outside, bad_parent)

    policy = PathPolicy(project_root=project_root.resolve())
    with pytest.raises(PathPolicyError):
        policy.contain_file("badparent/note.txt")


def test_contain_file_accepts_nonexistent_target_with_existing_parent(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    target = project_root / "fresh.txt"
    assert not target.exists()
    assert policy.contain_file("fresh.txt") == target.resolve()


def test_contain_file_accepts_nested_nonexistent_parent(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    resolved = policy.contain_file("a/b/c/note.txt")
    assert resolved == (project_root / "a/b/c/note.txt").resolve()


def test_contain_file_error_message_mentions_paths(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    with pytest.raises(PathPolicyError, match="../outside.txt"):
        policy.contain_file("../outside.txt")


# ---------------------------------------------------------------------------
# contain_directory
# ---------------------------------------------------------------------------


def test_contain_directory_accepts_root_itself(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    assert policy.contain_directory(str(project_root)) == project_root.resolve()


def test_contain_directory_accepts_relative_inside(project_root):
    (project_root / "sub").mkdir()
    policy = PathPolicy(project_root=project_root.resolve())
    assert policy.contain_directory("sub") == (project_root / "sub").resolve()


def test_contain_directory_rejects_dotdot_escape(project_root):
    policy = PathPolicy(project_root=project_root.resolve())
    with pytest.raises(PathPolicyError):
        policy.contain_directory("../outside")


def test_contain_directory_rejects_absolute_outside(project_root, outside):
    policy = PathPolicy(project_root=project_root.resolve())
    with pytest.raises(PathPolicyError):
        policy.contain_directory(str(outside))


def test_contain_directory_rejects_symlinked_dir_to_outside(project_root, outside):
    link = project_root / "linkdir"
    os.symlink(outside, link)

    policy = PathPolicy(project_root=project_root.resolve())
    with pytest.raises(PathPolicyError):
        policy.contain_directory("linkdir")


# ---------------------------------------------------------------------------
# for_tool
# ---------------------------------------------------------------------------


def test_for_tool_uses_bound_working_directory(project_root):
    tool = _StubTool(working_directory=project_root)
    policy = PathPolicy.for_tool(tool)
    assert policy.project_root == project_root.resolve()


def test_for_tool_with_none_snapshots_process_cwd(project_root, monkeypatch):
    """Legacy CLI: tool.working_directory is None → snapshot Path.cwd()."""
    monkeypatch.chdir(project_root)
    tool = _StubTool(working_directory=None)
    policy = PathPolicy.for_tool(tool)
    assert policy.project_root == project_root.resolve()


def test_for_tool_with_none_resnapshots_per_call(project_root, monkeypatch):
    """Each ``for_tool`` call snapshots fresh, so chdir between calls is
    honored."""
    other = project_root / "other"
    other.mkdir()
    tool = _StubTool(working_directory=None)

    monkeypatch.chdir(project_root)
    p1 = PathPolicy.for_tool(tool)
    monkeypatch.chdir(other)
    p2 = PathPolicy.for_tool(tool)

    assert p1.project_root != p2.project_root
    assert p2.project_root == other.resolve()


# ---------------------------------------------------------------------------
# contain_any: several writable roots, read-only carve-outs
# ---------------------------------------------------------------------------


@pytest.fixture
def carved(project_root):
    """``project_root`` writable, with ``raw/`` and one file carved read-only."""
    raw = project_root / "raw"
    raw.mkdir()
    (raw / "secret.txt").write_text("original")
    (project_root / "AGENTAO.md").write_text("rules")
    return {
        "writable": [project_root],
        "immutable": [raw, project_root / "AGENTAO.md"],
    }


def test_contain_any_accepts_write_inside_writable_root(project_root, carved):
    target = project_root / "notes" / "a.txt"
    assert PathPolicy.contain_any(str(target), **carved) == target.resolve()


def test_contain_any_accepts_second_writable_root(project_root, outside):
    target = outside / "share" / "x.txt"
    resolved = PathPolicy.contain_any(target, writable=[project_root, outside])
    assert resolved == target.resolve()


def test_contain_any_rejects_outside_every_root(project_root, outside):
    with pytest.raises(PathPolicyError, match="outside every writable root"):
        PathPolicy.contain_any(outside / "x.txt", writable=[project_root])


def test_contain_any_rejects_empty_writable(project_root):
    with pytest.raises(PathPolicyError):
        PathPolicy.contain_any(project_root / "x.txt", writable=[])


def test_contain_any_rejects_immutable_subtree_and_file(project_root, carved):
    with pytest.raises(PathPolicyError, match="read-only"):
        PathPolicy.contain_any(project_root / "raw" / "new.txt", **carved)
    with pytest.raises(PathPolicyError, match="read-only"):
        PathPolicy.contain_any(project_root / "AGENTAO.md", **carved)


def test_contain_any_rejects_leaf_symlink_into_immutable(project_root, carved):
    """The case per-root ``contain_file`` gets wrong: the link sits in a
    writable directory and points into the read-only one."""
    scratch = project_root / "scratch"
    scratch.mkdir()
    link = scratch / "link"
    os.symlink(project_root / "raw" / "secret.txt", link)

    with pytest.raises(PathPolicyError, match="read-only"):
        PathPolicy.contain_any(link, **carved)


def test_contain_any_rejects_parent_symlink_into_immutable(project_root, carved):
    alias = project_root / "alias"
    os.symlink(project_root / "raw", alias, target_is_directory=True)

    with pytest.raises(PathPolicyError, match="read-only"):
        PathPolicy.contain_any(alias / "new.txt", **carved)


def test_contain_any_rejects_leaf_symlink_out_of_writable(project_root, outside):
    link = project_root / "link"
    os.symlink(outside / "target.txt", link)

    with pytest.raises(PathPolicyError, match="outside every writable root"):
        PathPolicy.contain_any(link, writable=[project_root])


def test_contain_any_rejects_dotdot_escape(project_root, carved):
    with pytest.raises(PathPolicyError, match="read-only"):
        PathPolicy.contain_any(project_root / "notes" / ".." / "raw" / "x", **carved)


def test_contain_any_trailing_dotdot_is_resolved(project_root, outside):
    """``root/sub/..`` is ``root``'s parent's child, not inside ``root/sub``."""
    sub = outside / "sub"
    sub.mkdir()
    with pytest.raises(PathPolicyError, match="outside every writable root"):
        PathPolicy.contain_any(sub / "..", writable=[sub])


def test_contain_any_matches_roots_given_through_a_symlink(tmp_path, project_root):
    """A root named by a symlinked path still contains the real target."""
    alias = tmp_path / "alias"
    os.symlink(project_root, alias, target_is_directory=True)

    target = project_root / "a.txt"
    assert PathPolicy.contain_any(target, writable=[alias]) == target.resolve()
    with pytest.raises(PathPolicyError, match="read-only"):
        PathPolicy.contain_any(target, writable=[project_root], immutable=[alias])


def test_contain_any_rejects_relative_path(project_root, monkeypatch):
    """No root to resolve against; the process cwd would be the wrong one."""
    monkeypatch.chdir(project_root)
    with pytest.raises(PathPolicyError, match="not an absolute path"):
        PathPolicy.contain_any("a.txt", writable=[project_root])
