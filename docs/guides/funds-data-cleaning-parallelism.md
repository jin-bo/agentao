# funds-data-cleaning: Parallelism Architecture Analysis

## Summary

The `funds-data-cleaning` SKILL uses `ThreadPoolExecutor(max_workers=3)` with `agentao -p` subprocesses for Steps 2/3/4. The generalist sub-agent (`agent_generalist`) **cannot improve efficiency** in this context.

---

## Current Architecture

```
Python script (extract/analyze/run_clean)
  └── ThreadPoolExecutor(workers=3)
        ├── subprocess: zsh -lc "agentao -p"  ← file 1
        ├── subprocess: zsh -lc "agentao -p"  ← file 2
        └── subprocess: zsh -lc "agentao -p"  ← file 3
```

- Subprocess startup overhead: ~5–15s (zsh login shell + Python interpreter + agentao init)
- Concurrency: 3, achieving ~3x speedup over sequential execution

---

## Why generalist Cannot Help

### Scenario 1: Python script calling generalist tool (Not feasible)

Python scripts are independent processes with no access to the parent agentao session's tool registry. `agent_generalist` can only be called from within a agentao session.

### Scenario 2: Main agentao using generalist sequentially (3x slower)

```
main agentao (SKILL execution)
  └── agent_generalist → process file 1 (wait)
  └── agent_generalist → process file 2 (wait)
  └── agent_generalist → process file 3 (wait)
```

Sub-agents execute sequentially. 10 files × ~120s = ~20 minutes per step. This reverts to pre-parallelization performance.

### Scenario 3: Hybrid — generalist for orchestration, subprocess for LLM work (Negligible gain)

Steps 1 and 5 (pre/post-processing) already complete in <30s. Steps 2/3/4 still require subprocess + ThreadPoolExecutor. Net benefit is negligible.

### Scenario 4: Extend agentao with parallel sub-agents (Framework change needed)

Modifying `AgentToolWrapper` to support concurrent generalist execution would save ~5–15s × N subprocess startup overhead. However:
- Task duration is 90–360s/file; saving 5–15s is only 4–10% improvement
- Requires framework changes to `agents/tools.py` + concurrency safety work
- Cost-benefit ratio is poor

---

## Conclusion

| Approach | vs. Current | Feasibility | Recommended |
|----------|-------------|-------------|-------------|
| Scenario 2: sequential generalist | 3x slower | ✅ | ❌ |
| Scenario 3: hybrid | no gain | ✅ | ❌ |
| Scenario 4: parallel sub-agents | +4–10% | needs framework changes | ⚠️ |
| **Current (ThreadPoolExecutor + subprocess)** | baseline | — | ✅ |

**Root cause**: LLM inference time accounts for 95%+ of total time. Subprocess startup overhead (<5%) is not the bottleneck. The existing solution is already optimal for the current architecture.

---

## Future Optimization Directions

1. **Faster model**: Switch to `claude-haiku-4-5` (~3–5x speed increase, quality tradeoff)
2. **Batch prompts**: Single agentao call processes multiple files (requires long context + structured output support)
3. **Cache analysis results**: Step 3 (analyze) skips LLM call for previously analyzed files, reusing cached structure
