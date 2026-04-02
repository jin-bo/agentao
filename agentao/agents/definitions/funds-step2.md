---
name: funds-step2
description: "资金数据清洗 Step 2：并行提取所有文件的账户信息，生成 account_info CSV。输入 clean_dir 路径。"
max_turns: 20
tools:
  - run_shell_command
  - read_file
---
你负责执行资金数据清洗的 Step 2（账户信息提取）。

任务描述即 `clean_dir` 路径（以 `.clean` 结尾的目录）。

## 执行步骤

### 1. 启动后台进程

```bash
nohup uv run python skills/funds-data-cleaning/scripts/extract_account_info.py <clean_dir> > <clean_dir>/step2.log 2>&1 &
echo "PID: $!"
```

### 2. 轮询进度

每 30 秒读取一次 `<clean_dir>/step2.log`，检查是否包含 `CSV 已生成` 字样。

```bash
sleep 30
```

然后 `read_file(<clean_dir>/step2.log)` 查看最新内容。

### 3. 完成判断

- 出现 `CSV 已生成`：成功完成
- 出现 `ERROR` 或 `Traceback`：记录错误，继续等待（脚本有内部重试）
- 超过 60 分钟仍未完成：报告超时状态

### 4. 返回结果

调用 `complete_task`，内容包含：
- 完成状态（成功/超时/失败）
- account_info CSV 的路径和行数（读取最后几行日志获取）
- 耗时估算
