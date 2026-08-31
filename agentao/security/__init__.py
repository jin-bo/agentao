"""Security primitives for agentao tools.

Exposes :class:`PathPolicy` (gates filesystem writes to a project-rooted
workspace), the outbound-URL SSRF policy used by the web tools, in paired
sync / async forms (:func:`validate_outbound_url` / :func:`guarded_get` and
:func:`validate_outbound_url_async` / :func:`guarded_get_async`), and
:func:`strip_unicode_tags` (invisible-character smuggling defense; the
boundaries that apply it are enumerated in ``CLAUDE.md`` — it is a transform,
not an ambient guarantee about every string in the process).
:func:`sanitize_terminal_text` builds on it to strip terminal control and
bidi bytes from untrusted text bound for a TTY.
"""

from .path_policy import PathPolicy, PathPolicyError
from .terminal_text import sanitize_terminal_text
from .unicode_tags import (
    count_unicode_tags,
    has_unicode_tags,
    strip_unicode_tags,
)
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
    "count_unicode_tags",
    "guarded_get",
    "guarded_get_async",
    "has_unicode_tags",
    "sanitize_terminal_text",
    "strip_unicode_tags",
    "validate_outbound_url",
    "validate_outbound_url_async",
]
