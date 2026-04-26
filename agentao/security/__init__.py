"""Security primitives for agentao tools.

Currently exposes :class:`PathPolicy` which gates filesystem writes to a
project-rooted workspace.
"""

from .path_policy import PathPolicy, PathPolicyError

__all__ = ["PathPolicy", "PathPolicyError"]
