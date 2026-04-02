"""Tool execution pipeline for Agentao."""

import json
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .permissions import PermissionDecision, PermissionEngine
from .agents import TaskComplete
from .tools import ToolRegistry

# Tool outputs larger than this are saved to disk; a head+tail excerpt stays in context.
TOOL_OUTPUT_SAVE_THRESHOLD = 40_000   # chars  (~10K tokens)
# Fraction of the threshold kept from the beginning of the output (error context, args)
TOOL_OUTPUT_HEAD_RATIO = 0.2          # 20% head, 80% tail (errors/results tend to be at end)
# Directory for saved full outputs (relative to cwd)
_TOOL_OUTPUT_DIR = Path(".agentao") / "tool-outputs"

# Legacy hard cap for results that fail the file-save path (e.g. write errors)
MAX_TOOL_RESULT_CHARS = 80_000


def _save_and_truncate(content: str, tool_name: str, logger=None) -> str:
    """Save large tool output to .agentao/tool-outputs/ and return a head+tail excerpt.

    The full content is preserved on disk so the LLM can read_file it later.
    In context only the first 20% and last 80% of TOOL_OUTPUT_SAVE_THRESHOLD chars are kept.
    """
    head_chars = int(TOOL_OUTPUT_SAVE_THRESHOLD * TOOL_OUTPUT_HEAD_RATIO)
    tail_chars = TOOL_OUTPUT_SAVE_THRESHOLD - head_chars
    total = len(content)
    omitted = total - TOOL_OUTPUT_SAVE_THRESHOLD

    # Attempt to persist full output
    file_ref = ""
    try:
        _TOOL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        uid = uuid.uuid4().hex[:6]
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
        out_file = _TOOL_OUTPUT_DIR / f"{safe_name}_{ts}_{uid}.txt"
        out_file.write_text(content, encoding="utf-8")
        file_ref = f"\nFull output saved to: {out_file}  (use read_file to access)"
    except Exception as exc:
        if logger:
            logger.warning(f"Could not save tool output to file: {exc}")

    return (
        f"[Output truncated: {total:,} chars total, showing first {head_chars:,} "
        f"and last {tail_chars:,} chars.{file_ref}]\n\n"
        + content[:head_chars]
        + f"\n\n[… {omitted:,} chars omitted …]\n\n"
        + content[total - tail_chars :]
    )


class ToolRunner:
    """Encapsulates the 4-phase tool execution pipeline.

    Phase 1: Doom-loop detection + permission decisions → _plans
    Phase 2: User confirmation (sequential, interactive)
    Phase 3: Parallel execution (ThreadPoolExecutor, 8 workers)
    Phase 4: Result ordering + truncation

    Call reset() at the start of each chat() invocation to clear doom-loop state.
    Call execute() for each set of tool_calls within the loop.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        permission_engine: Optional[PermissionEngine],
        confirmation_callback: Optional[Callable[[str, str, Dict[str, Any]], bool]],
        step_callback: Optional[Callable[[Optional[str], Dict[str, Any]], None]],
        output_callback: Optional[Callable[[str, str], None]],
        tool_complete_callback: Optional[Callable[[str], None]],  # Fix #2: was Callable[[str, int], None]
        logger,
    ):
        self._tools = tools
        self._permission_engine = permission_engine
        self._confirmation_callback = confirmation_callback
        self._step_callback = step_callback
        self._output_callback = output_callback
        self._tool_complete_callback = tool_complete_callback
        self._logger = logger
        self._doom_counter: Counter = Counter()

    def reset(self) -> None:
        """Reset doom-loop counter. Call at the start of each chat() invocation."""
        self._doom_counter.clear()

    def execute(self, tool_calls, cancellation_token=None) -> Tuple[bool, List[Dict[str, Any]]]:
        """Run the 4-phase tool execution pipeline.

        Args:
            tool_calls: List of tool call objects from the LLM response.

        Returns:
            (doom_loop_triggered, tool_result_messages)
            - doom_loop_triggered: True if execution was halted by doom-loop detection.
            - tool_result_messages: List of {"role": "tool", ...} dicts to append to
              self.messages. Includes placeholder messages if doom-loop was triggered.
        """
        result_messages: List[Dict[str, Any]] = []

        # --- Phase 1: Pre-process (sequential) ---
        # Doom-loop detection + JSON parsing + permission decisions; no I/O yet.
        _plans: List[Dict[str, Any]] = []
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            function_args_raw = tool_call.function.arguments

            _doom_key = (function_name, function_args_raw)
            self._doom_counter[_doom_key] += 1
            if self._doom_counter[_doom_key] >= 3:
                self._logger.warning(
                    f"Doom-loop detected: {function_name} called 3+ times with identical args"
                )
                result_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": (
                        f"[Doom-loop detected] Tool '{function_name}' was called 3 times "
                        f"with identical arguments. Execution stopped to prevent an infinite loop. "
                        f"Please try a different approach or tool."
                    ),
                })
                # Placeholder results for already-queued plans so message state stays consistent.
                for _p in _plans:
                    result_messages.append({
                        "role": "tool",
                        "tool_call_id": _p["tool_call"].id,
                        "name": _p["function_name"],
                        "content": "Tool not executed (halted by doom-loop detection).",
                    })
                return True, result_messages

            # Fix #3: guard against malformed JSON from the LLM
            try:
                function_args = json.loads(function_args_raw)
            except json.JSONDecodeError as exc:
                self._logger.warning(
                    f"Tool '{function_name}' received invalid JSON arguments: {exc}"
                )
                result_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": (
                        f"Error: could not parse arguments for '{function_name}': {exc}. "
                        f"Please retry with valid JSON."
                    ),
                })
                continue

            # Fix #4 is in ToolRegistry.get() — KeyError now includes available tools
            try:
                tool = self._tools.get(function_name)
            except KeyError as exc:
                self._logger.warning(str(exc))
                result_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": str(exc),
                })
                continue

            if self._permission_engine:
                engine_decision = self._permission_engine.decide(function_name, function_args)
                if engine_decision == PermissionDecision.ALLOW:
                    raw_decision = PermissionDecision.ALLOW
                elif engine_decision == PermissionDecision.DENY:
                    raw_decision = PermissionDecision.DENY
                else:
                    # No matching rule — fall back to tool's own setting
                    raw_decision = (
                        PermissionDecision.ASK
                        if tool.requires_confirmation
                        else PermissionDecision.ALLOW
                    )
            else:
                raw_decision = (
                    PermissionDecision.ASK
                    if tool.requires_confirmation
                    else PermissionDecision.ALLOW
                )

            _plans.append({
                "tool_call": tool_call,
                "function_name": function_name,
                "function_args": function_args,
                "tool": tool,
                "decision": raw_decision,
            })

        if not _plans:
            return False, result_messages

        # --- Phase 2: Confirmation (sequential, interactive) ---
        # All user-facing prompts happen here before any execution starts.
        for _plan in _plans:
            if _plan["decision"] == PermissionDecision.ASK:
                _fn = _plan["function_name"]
                if self._confirmation_callback:
                    self._logger.info(f"Tool {_fn} requires confirmation")
                    _confirmed = self._confirmation_callback(
                        _fn,
                        _plan["tool"].description,
                        _plan["function_args"],
                    )
                    if not _confirmed:
                        self._logger.info(f"Tool {_fn} execution cancelled by user")
                        _plan["decision"] = "CANCELLED"
                    else:
                        self._logger.info(f"Tool {_fn} execution confirmed by user")
                        _plan["decision"] = PermissionDecision.ALLOW
                else:
                    _plan["decision"] = PermissionDecision.ALLOW

        # --- Phase 3: Parallel execution ---
        # Fix #1: per-tool-instance lock prevents concurrent calls to the same tool
        # from corrupting shared state (e.g. output_callback).  Different tool
        # instances still run in parallel.
        _tool_locks: Dict[int, threading.Lock] = {}
        for _p in _plans:
            _tid = id(_p["tool"])
            if _tid not in _tool_locks:
                _tool_locks[_tid] = threading.Lock()

        def _execute_one(_plan: Dict[str, Any]) -> tuple:
            _fn = _plan["function_name"]
            _args = _plan["function_args"]
            _tool = _plan["tool"]
            _tc = _plan["tool_call"]
            _decision = _plan["decision"]

            if self._step_callback:
                self._step_callback(_fn, _args)

            if _decision == PermissionDecision.DENY:
                self._logger.info(f"Tool {_fn} denied by permission engine")
                _result = (
                    f"Tool execution denied: '{_fn}' is not permitted "
                    f"by the current permission rules."
                )
            elif _decision == "CANCELLED":
                _result = (
                    f"Tool execution cancelled by user. "
                    f"The user declined to execute {_fn}."
                )
            else:  # ALLOW
                # Propagate cancellation token to AgentToolWrapper so it can pass
                # it down to nested sub-agent chat() calls.
                if cancellation_token and hasattr(_tool, "_cancellation_token"):
                    _tool._cancellation_token = cancellation_token

                # Pre-execution cancellation check (e.g. Ctrl+C fired while
                # other parallel tools were executing).
                if cancellation_token and cancellation_token.is_cancelled:
                    return _tc.id, _fn, f"[Operation Cancelled] {cancellation_token.reason}"

                # Acquire the per-tool lock to serialize concurrent calls to the
                # same tool instance, protecting output_callback assignment.
                with _tool_locks[id(_tool)]:
                    if self._output_callback and hasattr(_tool, "output_callback"):
                        _tool.output_callback = (
                            lambda chunk, _name=_fn: self._output_callback(_name, chunk)
                        )
                    try:
                        _result = _tool.execute(**_args)
                    except TaskComplete as _tc_exc:
                        _result = _tc_exc.result
                    except Exception as _e:
                        _result = f"Error executing {_fn}: {str(_e)}"
                    finally:
                        if hasattr(_tool, "output_callback"):
                            _tool.output_callback = None
                        # Fix #2: callback signature is (tool_name,) — no returncode
                        if self._tool_complete_callback:
                            self._tool_complete_callback(_fn)

            return _tc.id, _fn, _result

        _exec_results: Dict[str, tuple] = {}

        # Fix #6: skip ThreadPoolExecutor overhead for the common single-tool case
        if len(_plans) == 1:
            _call_id, _fn_name, _res = _execute_one(_plans[0])
            _exec_results[_call_id] = (_fn_name, _res)
        else:
            with ThreadPoolExecutor(max_workers=8) as _executor:
                _futures = {_executor.submit(_execute_one, p): p for p in _plans}
                for _future in as_completed(_futures):
                    _call_id, _fn_name, _res = _future.result()
                    _exec_results[_call_id] = (_fn_name, _res)

        # --- Phase 4: Append results in original order ---
        for _plan in _plans:
            _tc = _plan["tool_call"]
            _fn_name, _result = _exec_results[_tc.id]

            # Save large outputs to disk and put a head+tail excerpt in context.
            # This prevents context explosion while keeping full data accessible.
            if isinstance(_result, str) and len(_result) > TOOL_OUTPUT_SAVE_THRESHOLD:
                self._logger.warning(
                    f"Tool result from {_fn_name} is {len(_result):,} chars — "
                    f"saving to file and truncating context copy"
                )
                _result = _save_and_truncate(_result, _fn_name, self._logger)
            elif isinstance(_result, str) and len(_result) > MAX_TOOL_RESULT_CHARS:
                # Fallback hard cap (should rarely be reached after file-save path)
                _truncated = len(_result) - MAX_TOOL_RESULT_CHARS
                _result = (
                    _result[:MAX_TOOL_RESULT_CHARS]
                    + f"\n\n[... {_truncated:,} characters truncated ...]"
                )
                self._logger.warning(
                    f"Tool result from {_fn_name} hard-truncated: {_truncated:,} chars removed"
                )

            result_messages.append({
                "role": "tool",
                "tool_call_id": _tc.id,
                "name": _fn_name,
                "content": _result,
            })

        return False, result_messages
