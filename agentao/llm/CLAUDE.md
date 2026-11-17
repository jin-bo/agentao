# LLM wire protocols and logging — rules and invariants

This file loads when Claude works under this folder. It was moved verbatim from the root `CLAUDE.md`.

## LLM wire protocols

Design: `docs/design/llm-api-adapters.md`.

`LLMClient` (`llm/client.py`) is a retry and logging shell over one adapter. `api_format` (`{PROVIDER}_API_FORMAT`) selects it: `openai-completions` (default, `_openai_completions.py`), `anthropic-messages` (`_anthropic_messages.py`), `openai-responses` (`_openai_responses.py`).

- **`api_format` is not the provider.** Never infer it from a URL, a provider name or a model name. An unimplemented format fails closed.
- After construction, only a provider switch changes the format (`reconfigure(api_format=)`). A wire change is a switch, as `runtime/model.py::set_provider` defines it.
- **History never changes shape.** `agent.messages` stays OpenAI dicts on every wire. Adapters translate an outbound copy and build responses through `_StreamAccumulator` (`_stream_response.py`). A new wire adds an adapter, never a new message model.
- **The Chat Completions adapter stays byte-identical** to a capture made before the extraction (`tests/test_llm_api_extraction_noop.py`, golden file in `tests/data/`).
- **Never regenerate that golden file from the current build.** `LLMClient.client` and its two latches stay on `LLMClient`.

**Signed thinking:**
- Anthropic blocks ride `anthropic_thinking_blocks`. Responses reasoning rides `openai_reasoning_items`.
- The runtime attaches them at the two sites that record the model's own output (`chat_loop/_serialize.py::_attach_thinking_blocks`).
- **A new carrier key goes in `WIRE_CARRIER_KEYS` and nowhere else.** That tuple drives both the recording and the purge on a switch.

**The Responses wire:**
- It is stateless (`store: false`, no `previous_response_id`). History stores a function call's id as `call_id|fc_…` (`llm/_tool_ids.py`). Split on the last `|`, and only when the tail starts with `fc_`.
- `_with_wire_tool_ids` rewrites composite ids for Chat Completions, which allows 64 characters.
- **When there are no composite ids, `_with_wire_tool_ids` returns the same list.** This keeps the golden file true.
- Send a call's `fc_` item id back only with the reasoning it came from.
- The `openai` SDK *yields* `error` and `response.failed`. The adapter must raise them.

**SDK facts:**
- `openai` has no upper pin. A fresh install gets 3.x, but `uv.lock` has 2.x. To check 3.x, run `uv run --with 'openai==3.16.2' python -m pytest tests/ -n logical`.
- Anthropic SDK facts (measured): it has no `temperature` parameter. It refuses a non-streaming request above about 21k `max_tokens`, so `chat()` consumes a stream. An in-stream `error` arrives as `APIStatusError(status_code=200)`.
- **openai 3.38+ leaves the error JSON out of `str(exc)`.** A classifier reads `llm/_retry.py::api_error_text(exc)`, which adds `exc.body`. Show the user `str(exc)`.
- **Ask the Models API in `prepare()`**, which runs before `_build_request_kwargs`. Never ask it at construction or in `build_request`.
- **`usage.prompt_tokens` is the whole prompt.** On Anthropic it is `input_tokens + cache_creation + cache_read`. Do not copy this mapping to other adapters.
- **Test a wire adapter against the real SDK.** Replace only the socket (`tests/support/anthropic_wire.py`, `tests/support/openai_responses_wire.py`). Keep fixtures on `stream_of`.

## Logging

`agentao.log` records every LLM request and response, tool call, tool result and token usage, without truncation.
- The logger is in `agentao/llm/client.py`. Read it first when you debug tool execution or LLM behaviour.
- **Request logging compares, it does not count** (#635, `llm/_logging.py`). The turn's channel (`chat_stream`, and `chat` when the Gemini bypass or the streaming fallback sends the turn's request) keeps one fingerprint per logged history message. A one-off `chat` call keeps none and is logged whole. A request logs new and changed messages, a changed one marked `(rewritten)`. Never go back to `messages[count:]`: a compaction rewrites messages in place. Pass the trailing request-only count (`request_only=`), so the tail is logged each time and never remembered.
- On `anthropic-messages`, the log has the canonical OpenAI-shaped list, not the translated body.
- **The log is redacted.** `_RedactingFormatter` rewrites credential-shaped strings with the patterns in `security/secret_scan.py`. It is a `Formatter`, not a `Filter`, so the redaction never leaks into an embedding host's handlers. To get raw bytes, bypass this formatter (the only place to do so).
