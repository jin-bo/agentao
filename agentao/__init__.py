"""Agentao - A CLI agent harness with tools, skills, and MCP support."""

import warnings
warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match")

__version__ = "0.2.5"

from .agent import Agentao
from .skills import SkillManager

__all__ = ["Agentao", "SkillManager"]
