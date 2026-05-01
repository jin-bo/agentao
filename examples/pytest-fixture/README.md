# pytest · per-test Agentao fixture

Drop-in fixtures for downstream test suites that want one `Agentao`
per test, fake LLM, no API key. Copy
[`src/agentao_fixtures.py`](./src/agentao_fixtures.py) into your
project's `conftest.py` (or `tests/conftest.py`) and the three
fixtures — `agent`, `agent_with_reply`, `fake_llm_client` — become
available everywhere.

## Try it

```bash
cd examples/pytest-fixture
uv sync --extra dev
uv run pytest tests/ -v
```

## Use in your project

```python
# your_project/tests/test_foo.py
def test_my_thing(agent):
    reply = agent.chat("hello")
    assert reply == "fixture reply"
```

```python
# different replies per test
def test_my_other_thing(agent_with_reply):
    a1 = agent_with_reply("one")
    a2 = agent_with_reply("two")
    assert a1.chat("hi") == "one"
    assert a2.chat("hi") == "two"
```

## What's mocked

- `LLMClient` is a `MagicMock` shaped to the attributes Agentao reads
  at construction (model, api_key, base_url, …) and at metric rollup
  (`total_prompt_tokens`).
- `Agentao._llm_call` is patched class-wide for the test's duration —
  the chat loop returns the scripted reply without touching the
  network or the OpenAI SDK.
- Each test gets a fresh `tmp_path` as `working_directory`, so message
  history, tool registries, and replay files never leak across tests.

## Not included

- Tool-call simulation. If you need to exercise a tool path, build a
  `MagicMock` with `tool_calls=[...]` on the response and tighten the
  fixture in your project. See `tests/test_async_tool.py` in the
  agentao repo for the pattern.
