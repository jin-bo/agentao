"""Security primitives for agentao tools.

Exposes :class:`PathPolicy` (gates filesystem writes to a project-rooted
workspace) and the outbound-URL SSRF policy (:func:`validate_outbound_url`,
:func:`guarded_get`) used by the web tools.
"""

from .path_policy import PathPolicy, PathPolicyError
from .url_policy import UrlPolicyError, guarded_get, validate_outbound_url

__all__ = [
    "PathPolicy",
    "PathPolicyError",
    "UrlPolicyError",
    "guarded_get",
    "validate_outbound_url",
]
