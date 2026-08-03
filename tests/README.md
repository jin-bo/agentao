# Agentao Tests

~3800 tests across `tests/`. This file documents **layout and conventions**;
it deliberately does not enumerate test files — an earlier version listed 14
of them by hand and rotted into naming files that no longer exist.

## Running

```bash
uv run python -m pytest tests/          # default suite
uv run python -m pytest -m slow         # clean-install smoke tests
uv run python -m pytest tests/test_replay.py -v
```

`pyproject.toml :: tool.pytest.ini_options` sets
`addopts = "--tb=short -m 'not slow'"`, so **`slow` is excluded by default**.
It marks the three modules that build wheels or boot subprocess venvs
(`test_clean_install_smoke.py`, `test_dependency_split.py`,
`test_cli_missing_dep_message.py`) and needs `uv build` to have run first.

## Layout

| Path | Contents |
|---|---|
| `tests/*.py` | The bulk of the suite — one module per contract, named after the thing under test. |
| `tests/cli/` | Slash-command and `agentao run` argument handling. |
| `tests/support/` | Shared scaffolding — fake servers, agent doubles, param builders. See its own README. |
| `tests/data/` | Static fixtures (e.g. `full_extras_baseline.txt`, the dependency-split baseline). |
| `tests/conftest.py` | Two autouse credential fixtures plus `search_tool` / `capture_subprocess_run`. |

## Conventions

**Credentials are stubbed for every test.** `conftest.py::_stub_llm_credentials`
sets `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`, and
`_agentao_env_default_credentials` backfills them onto direct
`Agentao(working_directory=...)` construction — mirroring what
`build_from_environment` does, so production code never sees an implicit env
read from `Agentao.__init__`. Note both fixtures *defer to a real exported
value* (`os.environ.get(key, default)`); a test that reaches the network will
use a developer's real key.

**Do not reach the network by default.** The two tests that legitimately call
a live model gate themselves on an env var and default to offline in CI:

| Gate | Used by |
|---|---|
| `AGENTAO_TEST_LIVE_LLM` | `test_multi_turn.py` |
| `AGENTAO_TEST_LIVE_MODELS` | `test_model_command.py` |

Both are pinned to `0` in `.github/workflows/publish*.yml`. A new test that
talks to a provider needs the same gate *and* an assertion that still holds on
the offline path — see `test_multi_turn.py::test_multi_turn_tool_calls` for the
degradation branch.

**Write under `tmp_path`, never `Path.cwd()`.** A test rooted at the repo
working directory mutates the developer's real `.agentao/` state (memory DB,
sessions, replays).

**No side effects at import time.** Module-level `os.environ` writes land
during *collection*, before any fixture runs, and leak into every other test in
the session. Use `monkeypatch`.

**Assert, don't print.** A test whose failure path is a `print` or an early
`return` passes unconditionally and is worse than no test.

**Helpers duplicated across 2+ files belong in `tests/support/`** — that
directory's README defines what is in and out of scope.

## Requirements

Python 3.10+ and the dev dependency group (`uv sync`). Some tests skip
themselves on platform grounds (POSIX-only, macOS-only, `rg` not installed) or
when `mypy` is unavailable; those skips are expected.
