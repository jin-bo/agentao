# tests/support/

Shared test scaffolding — fake servers, agent doubles, factory helpers.

**Scope:** Helpers that are (or would be) duplicated across 2+ test files.
Things that belong: configurable fake subprocess servers, reusable `FakeAgent`
base classes, common param builders (`initialize_params()`, `_prompt_params`).

**Out of scope:** Test-file-specific fixtures, mocks of business types with
only one caller, anything that requires a flag to behave differently per call
site. If an abstraction needs more than two optional knobs, it is probably
better left inline.

**Import style:** Use relative imports (`from .support.acp_client import X`)
from inside `tests/`. Helpers here are **plain functions / classes**, not
pytest fixtures — construction is explicit at the call site.

See `plans/agentao-fixture-helper-abstract-rossum.md` for the migration plan.
