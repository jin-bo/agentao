# Skills 系统提示词改进

## 问题描述

原来的系统提示词虽然提到了 skills，但存在以下问题：

1. **缺少可用 skills 列表**：LLM 不知道有哪些 skills 可用
2. **缺少触发条件**：LLM 不知道何时应该激活哪个 skill
3. **静态系统提示词**：系统提示词在初始化时构建一次后就固定了，激活 skill 后不会更新

这导致 LLM 很难正确地使用 skills 功能。

## 解决方案

### 1. 在系统提示词中列出所有可用 Skills

修改 `_build_system_prompt()` 方法，添加 "=== Available Skills ===" 部分：

```python
# Add available skills section
available_skills = self.skill_manager.list_available_skills()
if available_skills:
    prompt += "\n\n=== Available Skills ===\n"
    prompt += "You have access to specialized skills. Use the 'activate_skill' tool to activate them when needed.\n\n"

    for skill_name in sorted(available_skills):
        skill_info = self.skill_manager.get_skill_info(skill_name)
        if skill_info:
            description = skill_info.get('description', 'No description available')
            prompt += f"• {skill_name}: {description}\n"

    prompt += "\nWhen the user's request matches a skill's description, use the activate_skill tool before proceeding with the task."
```

### 2. 动态构建系统提示词

移除了在 `__init__` 中缓存的 `self.system_prompt`，改为在 `chat()` 方法中每次动态构建：

**修改前：**
```python
# __init__ 中
self.system_prompt = self._build_system_prompt()

# chat 中
messages_with_system = [
    {"role": "system", "content": self.system_prompt}
] + self.messages
```

**修改后：**
```python
# chat 中
system_prompt = self._build_system_prompt()
messages_with_system = [
    {"role": "system", "content": system_prompt}
] + self.messages
```

这样确保：
- 每次调用都使用最新的 skills 列表
- 激活 skill 后，下一次调用会包含活跃 skills 的上下文

### 3. 导出必要的类

修改 `agentao/__init__.py` 以便测试和外部使用：

```python
from .agent import Agentao
from .skills import SkillManager

__all__ = ["Agentao", "SkillManager"]
```

## 测试验证

创建了 `test_skills_prompt.py` 进行验证，测试结果：

```
✅ Skills section found in system prompt
✅ Found 17 available skills
✅ All skills found in prompt
✅ Active skills section found after activation
```

## 系统提示词示例

现在的系统提示词包含：

```
=== Available Skills ===
You have access to specialized skills. Use the 'activate_skill' tool to activate them when needed.

• algorithmic-art: Creating algorithmic art using p5.js with seeded randomness...
• brand-guidelines: Applies Anthropic's official brand colors and typography...
• canvas-design: Create beautiful visual art in .png and .pdf documents...
• doc-coauthoring: Guide users through a structured workflow for co-authoring...
• docx: Use this skill whenever the user wants to create, read, edit, or manipulate Word documents...
• pdf: Use this skill whenever the user wants to do anything with PDF files...
• pptx: Use this skill any time a .pptx file is involved in any way...
• xlsx: Use this skill any time a spreadsheet file is the primary input or output...
• skill-creator: Guide for creating effective skills...
[... 其他 skills ...]

When the user's request matches a skill's description, use the activate_skill tool before proceeding with the task.
```

激活 skill 后还会添加：

```
=== Active Skills ===

pdf - PDF Processing Guide:
  Description: Use this skill whenever the user wants to do anything with PDF files...
  Task: Merge multiple PDFs
  Documentation: /path/to/skills/pdf/SKILL.md
```

## 效果

1. **更好的 Skill 发现**：LLM 可以看到所有可用的 skills 及其描述
2. **自动激活**：当用户请求匹配某个 skill 的描述时，LLM 会自动激活它
3. **实时更新**：激活 skill 后，系统提示词会包含活跃 skills 的详细信息
4. **更准确的任务处理**：LLM 知道何时应该使用哪个 skill

## 影响范围

### 修改的文件

1. **agentao/agent.py**
   - 修改 `_build_system_prompt()` - 添加 skills 列表
   - 修改 `__init__()` - 移除 `self.system_prompt` 缓存
   - 修改 `chat()` - 动态构建系统提示词

2. **agentao/__init__.py**
   - 添加 `Agentao` 和 `SkillManager` 导出

3. **test_skills_prompt.py** (新增)
   - 验证 skills 在系统提示词中

### 向后兼容性

✅ **完全兼容**：
- 对外接口没有变化
- 只是改进了内部实现
- 已有功能保持不变

### 性能影响

⚠️ **轻微影响**：
- 每次调用 `chat()` 都会重新构建系统提示词
- 对于有 17 个 skills 的情况，增加约 2-3KB 的系统提示词
- 这个开销可以忽略不计，而且换来了更好的功能

## 使用示例

### 示例 1：自动激活 PDF Skill

**用户输入：**
```
帮我合并这两个 PDF 文件
```

**LLM 行为：**
1. 查看系统提示词中的 skills 列表
2. 发现 "pdf" skill 的描述匹配这个任务
3. 调用 `activate_skill("pdf", "合并 PDF 文件")`
4. 按照 PDF skill 的指导完成任务

### 示例 2：自动激活 Skill Creator

**用户输入：**
```
帮我写一个总结会议，写会议纪要的 Skill
```

**LLM 行为：**
1. 识别这是创建新 skill 的任务
2. 激活 "skill-creator" skill
3. 按照 skill-creator 的工作流引导用户
4. 创建新的 meeting-minutes skill

## 后续改进建议

1. **Skill 优先级**：为不同 skills 设置优先级，避免冲突
2. **Skill 标签**：添加标签系统，更好地分类和搜索 skills
3. **Skill 依赖**：支持 skill 之间的依赖关系
4. **性能优化**：对于大量 skills，考虑按需加载描述
5. **多语言支持**：支持不同语言的 skill 描述

## 测试命令

```bash
# 测试系统提示词
uv run python test_skills_prompt.py

# 实际使用
./run.sh
```

---

**修改日期**：2026-02-09
**版本**：0.1.1
**状态**：✅ 已测试并部署
**相关文档**：MULTI_TURN_FIX.md
