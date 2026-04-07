# Agentao Tests

This directory contains test files for the Agentao project.

## Running Tests

### Run all tests
```bash
python -m pytest tests/
```

### Run a specific test file
```bash
python -m pytest tests/test_imports.py
```

### With verbose output
```bash
python -m pytest tests/ -v
```

> **Note:** If running from a source checkout with `uv`, use `uv run python -m pytest tests/`.

## Test Files

### Core Functionality Tests
- `test_imports.py` - Test module imports
- `test_logging.py` - Test logging functionality
- `test_multi_turn.py` - Test multi-turn conversations

### Feature Tests
- `test_agentao_md.py` - Test AGENTAO.md auto-loading
- `test_tool_confirmation.py` - Test tool confirmation mechanism
- `test_menu_confirmation.py` - Test menu-based confirmation
- `test_readchar_confirmation.py` - Test single-key confirmation with readchar
- `test_clear_resets_confirm.py` - Test /clear command resets confirmation state
- `test_date_in_prompt.py` - Test date/time injection in system prompt
- `test_status_pause.py` - Test status and pause functionality

### Skills Tests
- `test_skills.py` - Test skills loading and management
- `test_skills_prompt.py` - Test skills prompt integration
- `test_skill_resources.py` - Test skill resource loading
- `test_skill_integration.py` - Test skill integration

### Command Tests
- `test_model_command.py` - Test /model command

## Test Requirements

Tests use the following:
- Python 3.10+
- Dependencies from `pyproject.toml`

## Notes

- Tests are designed to be run independently
- Some tests may require environment variables (see `.env.example`)
- Test files follow the naming pattern `test_*.py`
