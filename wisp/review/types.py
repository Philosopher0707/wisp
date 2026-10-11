"""The review's vocabulary. A finding is a claim with a location, the text it stands on, and how it was established."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"block": 0, "warn": 1, "info": 2}[self.value]


@dataclass(frozen=True)
class Finding:
    rule: str  # "secret", "syntax-error", "rule:no-print", "model:security", ...
    severity: Severity
    file: str
    line: int  # 0 when the finding is about the file, not a line
    quote: str  # the text it stands on; never a secret (secrets are redacted before they get here)
    message: str
    evidence: str  # how it was established: "diff pattern", "post-image parse", "gate:secrets", "model claim grounded in the diff", ...
    suggestion: str = ""

    @property
    def fingerprint(self) -> str:
        """Stable across line shifts: what, where (file) and the text it stands on, not the number."""
        basis = " ".join(self.quote.split()) or self.message
        return hashlib.sha1(f"{self.rule}\0{self.file}\0{basis}".encode()).hexdigest()[:12]
