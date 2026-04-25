"""Phase 4 of the tool execution pipeline: result formatting.

Converts a batch of ``ToolExecutionResult`` instances into:

* one ``TOOL_RESULT`` replay event per call (with content hash and
  optional disk-spill path), and
* one OpenAI tool message per call (with the in-context excerpt).

Owns the large-output disk-spill policy and the hard truncation fallback
so the runner does not need to know either threshold.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..transport import AgentEvent, EventType
from .tool_executor import ToolExecutionResult
from .tool_planning import ToolCallPlan


# Tool outputs larger than this are saved to disk; a head+tail excerpt stays in context.
TOOL_OUTPUT_SAVE_THRESHOLD = 40_000   # chars  (~10K tokens)
# Fraction of the threshold kept from the beginning of the output (error context, args)
TOOL_OUTPUT_HEAD_RATIO = 0.2          # 20% head, 80% tail (errors/results tend to be at end)
# Directory for saved full outputs (relative to cwd)
_TOOL_OUTPUT_DIR = Path(".agentao") / "tool-outputs"

# Legacy hard cap for results that fail the file-save path (e.g. write errors)
MAX_TOOL_RESULT_CHARS = 80_000


def _save_and_truncate(
    content: str, tool_name: str, logger=None,
) -> Tuple[str, Optional[str]]:
    """Save large tool output to ``.agentao/tool-outputs/`` and return
    ``(excerpt, disk_path_or_None)``.

    The full content is preserved on disk so the LLM can ``read_file`` it
    later. In context only the first 20% and last 80% of
    ``TOOL_OUTPUT_SAVE_THRESHOLD`` chars are kept. The ``disk_path`` is
    surfaced so the replay's ``tool_result`` event can record where the
    full output lives (``None`` when the save attempt failed).
    """
    head_chars = int(TOOL_OUTPUT_SAVE_THRESHOLD * TOOL_OUTPUT_HEAD_RATIO)
    tail_chars = TOOL_OUTPUT_SAVE_THRESHOLD - head_chars
    total = len(content)
    omitted = total - TOOL_OUTPUT_SAVE_THRESHOLD

    file_ref = ""
    disk_path: Optional[str] = None
    try:
        _TOOL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        uid = uuid.uuid4().hex[:6]
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
        out_file = _TOOL_OUTPUT_DIR / f"{safe_name}_{ts}_{uid}.txt"
        out_file.write_text(content, encoding="utf-8")
        file_ref = f"\nFull output saved to: {out_file}  (use read_file to access)"
        disk_path = str(out_file)
    except Exception as exc:
        if logger:
            logger.warning(f"Could not save tool output to file: {exc}")

    excerpt = (
        f"[Output truncated: {total:,} chars total, showing first {head_chars:,} "
        f"and last {tail_chars:,} chars.{file_ref}]\n\n"
        + content[:head_chars]
        + f"\n\n[… {omitted:,} chars omitted …]\n\n"
        + content[total - tail_chars :]
    )
    return excerpt, disk_path


class ToolResultFormatter:
    """Phase 4: emit TOOL_RESULT events and build OpenAI tool messages."""

    def __init__(self, transport, logger):
        self._transport = transport
        self._logger = logger

    def format_batch(
        self,
        plans: List[ToolCallPlan],
        exec_results: Dict[str, ToolExecutionResult],
    ) -> List[Dict[str, Any]]:
        """Format every plan's result in the original plan order.

        Side effect: emits one ``TOOL_RESULT`` replay event per plan with
        the *untruncated* content (the recorder applies its own redaction
        + 8 KB head/tail policy). Returns the list of OpenAI tool messages
        the runner should append to the conversation.
        """
        messages: List[Dict[str, Any]] = []
        for plan in plans:
            messages.append(self._format_one(plan, exec_results[plan.tool_call.id]))
        return messages

    # ------------------------------------------------------------------
    # Per-call formatting
    # ------------------------------------------------------------------

    def _format_one(
        self,
        plan: ToolCallPlan,
        info: ToolExecutionResult,
    ) -> Dict[str, Any]:
        fn_name = info.fn_name
        result = info.result
        call_id = plan.tool_call.id

        # Capture the ORIGINAL result for the replay's tool_result event —
        # before truncation rewrites ``result`` into a compact conversation
        # message. ``content_hash`` is over the full untruncated bytes so a
        # reader can later verify the exact result a file on disk belongs to.
        original_for_replay = result if isinstance(result, str) else str(result)
        original_chars = len(original_for_replay)
        content_hash = hashlib.sha256(
            original_for_replay.encode("utf-8", errors="replace"),
        ).hexdigest()
        saved_to_disk = False
        disk_path: Optional[str] = None

        # Save large outputs to disk and keep a head+tail excerpt in context.
        # Prevents context explosion while keeping the full data accessible.
        if isinstance(result, str) and len(result) > TOOL_OUTPUT_SAVE_THRESHOLD:
            self._logger.warning(
                f"Tool result from {fn_name} is {len(result):,} chars — "
                f"saving to file and truncating context copy"
            )
            result, disk_path = _save_and_truncate(result, fn_name, self._logger)
            saved_to_disk = disk_path is not None
        elif isinstance(result, str) and len(result) > MAX_TOOL_RESULT_CHARS:
            # Fallback hard cap (should rarely be reached after file-save path)
            truncated = len(result) - MAX_TOOL_RESULT_CHARS
            result = (
                result[:MAX_TOOL_RESULT_CHARS]
                + f"\n\n[... {truncated:,} characters truncated ...]"
            )
            self._logger.warning(
                f"Tool result from {fn_name} hard-truncated: {truncated:,} chars removed"
            )

        # Fire the replay-side ``tool_result`` event with the raw content.
        # The ReplayAdapter forwards this into recorder.record() which runs
        # sanitize_event — that's where the 8000-char head/tail truncation
        # and secret scanning happen.
        self._transport.emit(AgentEvent(EventType.TOOL_RESULT, {
            "tool": fn_name,
            "call_id": call_id,
            "content": original_for_replay,
            "content_hash": content_hash,
            "original_chars": original_chars,
            "saved_to_disk": saved_to_disk,
            "disk_path": disk_path,
            "status": info.status,
            "duration_ms": info.duration_ms,
            "error": info.error,
        }))

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": fn_name,
            "content": result,
        }
