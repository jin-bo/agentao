---
name: funds-step3
description: "资金数据清洗 Step 3：并行分析所有文件的数据结构，识别列类型、编码、日期金额字段。输入 clean_dir 路径。"
max_turns: 20
tools:
  - run_shell_command
  - read_file
---
你负责执行资金数据清洗的 Step 3（数据结构分析）。

任务描述即 `clean_dir` 路径（以 `.clean` 结尾的目录）。

## 执行步骤

### 1. 启动后台进程

```bash
nohup uv run python skills/funds-data-cleaning/scripts/analyze_data_structure.py <clean_dir> > <clean_dir>/step3.log 2>&1 &
echo "PID: $!"
```

### 2. 轮询进度

每 30 秒读取一次 `<clean_dir>/step3.log`，检查是否包含 `统计:` 字样（最终统计行）。

```bash
sleep 30
```

然后 `read_file(<clean_dir>/step3.log)` 查看最新内容。

注意：脚本最多执行 3 轮（处理 Step 1 中被拆分的文件），日志中会出现多次 `统计:` 行，等待最后一次出现且无新内容写入时视为完成。

### 3. 完成判断

- 最后一行出现 `统计:` 且 30 秒内无新内容：成功完成
- 出现未处理异常并进程退出：失败
- 超过 60 分钟仍未完成：报告超时

### 4. 返回结果

调用 `complete_task`，内容包含：
- 完成状态（成功/超时/失败）
- has_data 文件数、skipped 文件数（从日志最后的统计行读取）
- data_structure JSONL 路径
