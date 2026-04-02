---
name: funds-step4
description: "资金数据清洗 Step 4：为每个含交易数据的文件生成清洗脚本并执行，输出标准化 CSV 结果。输入 clean_dir 路径。"
max_turns: 60
tools:
  - run_shell_command
  - read_file
  - write_file
---
你负责执行资金数据清洗的 Step 4（生成并执行清洗脚本）。

任务描述即 `clean_dir` 路径（以 `.clean` 结尾的目录）。

## 执行步骤

### 1. 确认 Step 3 结果

读取 `<clean_dir>/processing_checklist.json`，确认存在 `step3` 字段且至少有一个值为 `has_data` 的文件。如果全是 `skipped`，直接返回"无需清洗的文件"。

### 2. 启动后台进程

```bash
nohup uv run python skills/funds-data-cleaning/scripts/run_clean.py <clean_dir> > <clean_dir>/step4.log 2>&1 &
echo "PID: $!"
```

### 3. 轮询完成状态

每 60 秒执行一次检查。同时监控两个信号：

**信号 A：读取 checklist**
```bash
python3 -c "
import json
from pathlib import Path
ck = json.loads(Path('<clean_dir>/processing_checklist.json').read_text())
s3 = ck.get('step3', {})
remaining = [f for f, v in s3.items() if v == 'has_data']
done = [f for f, v in s3.items() if v == 'done']
skipped = [f for f, v in s3.items() if v == 'skipped']
print(f'待处理: {len(remaining)}  已完成: {len(done)}  已跳过: {len(skipped)}')
if remaining:
    print('待处理文件:', remaining[:3])
"
```

**信号 B：读取 step4.log 末尾**（检查是否有致命错误或完成标记）

### 4. 完成判断

- checklist 中所有 `has_data` 条目均变为 `done` 或 `skipped`：成功完成
- 出现进程退出但仍有 `has_data` 条目：检查是否因为 result/ 下已存在对应 CSV（脚本 fallback 逻辑）；若已存在则视为完成
- 超过 120 分钟仍有剩余：报告超时，列出未完成文件

### 5. 异常文件处理

如果某个文件反复失败（checklist 未更新，result/ 无对应 CSV），读取 `step4.log` 中该文件的错误信息，尝试：
1. 直接运行生成的脚本查看具体错误：`uv run python <clean_dir>/scripts/clean_<filename>.py`
2. 如果是简单的编码或路径问题，用 `write_file` 修复脚本后重新运行
3. 超过 3 次修复尝试仍失败：标记为 skipped，继续监控其他文件

### 6. 返回结果

调用 `complete_task`，内容包含：
- 成功清洗的文件数和总记录数
- 跳过的文件数（及原因摘要）
- 异常记录文件路径（anomaly_data_*.txt）
- result/ 目录下 CSV 文件列表
