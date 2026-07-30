"""Security primitives for agentao tools.

Exposes :class:`PathPolicy` (gates filesystem writes to a project-rooted
workspace) and the outbound-URL SSRF policy used by the web tools, in paired
sync / async forms (:func:`validate_outbound_url` / :func:`guarded_get` and
:func:`validate_outbound_url_async` / :func:`guarded_get_async`).
"""

from .path_policy import PathPolicy, PathPolicyError
from .url_policy import (
    UrlPolicyError,
    guarded_get,
    guarded_get_async,
    validate_outbound_url,
    validate_outbound_url_async,
)

__all__ = [
    "PathPolicy",
    "PathPolicyError",
    "UrlPolicyError",
    "guarded_get",
    "guarded_get_async",
    "validate_outbound_url",
    "validate_outbound_url_async",
]
