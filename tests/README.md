# Agentao Tests

~4900 tests across `tests/`. This file documents **layout and conventions**;
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

CI runs them in the **build** job, on Python 3.12, right after `uv build` —
that is the only job with a `dist/*.whl` for them to install. They ran nowhere
before, which is how the dependency baseline drifted unnoticed since June.

## Layout

| Path | Contents |
|---|---|
| `tests/*.py` | The bulk of the suite — one module per contract, named after the thing under test. |
| `tests/cli/` | Slash-command and `agentao run` argument handling. |
| `tests/support/` | Shared scaffolding — fake servers, agent doubles, param builders. See its own README. |
| `tests/data/` | Static fixtures (e.g. `full_extras_baseline.txt` — the `[full]` closure as PEP 503 *names*; versions float by design and are not compared). |
| `tests/conftest.py` | Two autouse credential fixtures and an autouse `.env`-discovery guard, plus opt-in `isolated_cwd` / `isolated_skill_dirs` (keep agentao's cwd writes and skill discovery under `tmp_path`) and `search_tool` / `capture_subprocess_run`. |

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
a live model gate themselves on an env var:

| Gate | Used by | Unset means |
|---|---|---|
| `AGENTAO_TEST_LIVE_LLM` | `test_multi_turn.py` | offline everywhere; `1` opts in |
| `AGENTAO_TEST_LIVE_MODELS` | `test_model_command.py` | offline everywhere; `1` opts in |

Make a new gate opt-in like these two. Guessing from the environment (CI or
not, a key that looks fake or not) is what sent a dummy `sk-dummy` key to the
real models endpoint (#463), and a gate that picks assertions but not whether
the request is sent is no gate: `test_multi_turn.py` used to reach the
provider on every run and pass on the 401 (#468).

Both are pinned to `0` in `.github/workflows/publish*.yml`. A new test that
talks to a provider needs the same gate, and the behaviour it checks needs an
offline test too: script the provider with the wire fakes in `tests/support/`
(`test_multi_turn.py` drives two tool rounds and a second turn through
`openai_responses_wire.py`). A live test asserts success; it does not accept an
API error as a pass.

**Never call `load_dotenv()` in a test.** It writes into `os.environ` for the
rest of the session, outside `monkeypatch`, and with no path it walks up from
the test file to the first `.env` it finds — a developer's `~/.env` when the
checkout has none. Keys conftest already set are left alone, so it does not
even supply the test's credentials; it leaks the rest (`LLM_PROVIDER`,
`OPENAI_API_FORMAT`, other providers' keys), and conftest's credential
discovery in every later test then picks up that provider's real key (#468).

agentao's own `safe_load_dotenv()` does the same walk when it gets no path
(from the process cwd, in `build_from_environment`, the CLI and `agentao
doctor`), so a test that builds through the real factory loaded `~/.env` too.
`conftest.py::_no_dotenv_discovery` makes that walk find nothing for every test
(#471). An explicit path still loads: a test that needs a `.env` writes one
under `tmp_path` and passes it.

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
