"""Shared finding / report records for the diagnostics commands."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional


FindingLevel = Literal["info", "warning", "error"]
FileStatus = Literal["absent", "ok", "unreadable", "malformed"]


@dataclass
class Finding:
    """A single diagnostic finding.

    ``source`` carries the file path or env-derived label when known so the
    user can act on the finding without re-deriving where it came from.
    """

    level: FindingLevel
    area: str
    message: str
    source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class DiagnosticReport:
    """Aggregated doctor / config-validate output."""

    ok: bool = True
    sections: Dict[str, Any] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.level == "error":
            self.ok = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "sections": self.sections,
            "findings": [f.to_dict() for f in self.findings],
        }
