"""Value objects for the optional Jev recommendation service."""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Mapping


@dataclass(frozen=True)
class JevConfig:
    enabled: bool = False
    model: str = "jev-1.13.0"
    timeout_ms: int = 10_000
    min_confidence: float = 0.7
    skill_recommendation: str = "suggest"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("jev.enabled must be a boolean")
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 100:
            raise ValueError("jev.model must be a nonempty model id (up to 100 characters)")
        if type(self.timeout_ms) is not int or not 1 <= self.timeout_ms <= 30_000:
            raise ValueError("jev.timeout_ms must be an integer between 1 and 30000")
        if (type(self.min_confidence) not in (int, float)
                or not math.isfinite(self.min_confidence)
                or not 0 <= self.min_confidence <= 1):
            raise ValueError("jev.min_confidence must be between 0 and 1")
        if self.skill_recommendation != "suggest":
            raise ValueError("jev.skill_recommendation supports only suggest")

    @classmethod
    def from_dict(cls, data: Mapping) -> JevConfig:
        if not isinstance(data, Mapping):
            raise ValueError("jev settings must be an object")
        return cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data})


@dataclass(frozen=True)
class SkillCandidate:
    name: str
    description: str
    content: str = ""


@dataclass(frozen=True)
class SkillSuggestion:
    skill_name: str
    confidence: float
    model: str
