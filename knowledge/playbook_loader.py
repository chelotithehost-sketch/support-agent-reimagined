# knowledge/playbook_loader.py
# ───────────────────────────────────────────────
# Loads hosting_playbooks.yaml and provides lookup by intent/product_area.
# The Reasoner uses this to build resolution paths.
# Copy into: knowledge/playbook_loader.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)


@dataclass
class DiagnosticStep:
    step: str
    tool: str | None = None
    path: str | None = None
    expected: str | None = None


@dataclass
class CommonCause:
    cause: str
    fix: str
    propagation_time: str | None = None
    note: str | None = None


@dataclass
class Playbook:
    name: str
    product_area: str
    triggers: list[str]
    diagnostic_steps: list[DiagnosticStep]
    common_causes: list[CommonCause]
    escalation_criteria: list[str] = field(default_factory=list)
    empathy_statements: list[str] = field(default_factory=list)
    resolution: list[str] = field(default_factory=list)
    note: str | None = None


class PlaybookLoader:
    """Loads and indexes troubleshooting playbooks from YAML."""

    def __init__(self, playbook_path: str | Path | None = None) -> None:
        self._playbooks: dict[str, Playbook] = {}
        self._triggers: list[tuple[str, str, re.Pattern]] = []  # (playbook_name, area, pattern)

        path = playbook_path or Path(__file__).parent / "hosting_playbooks.yaml"
        self._load(path)

    def _load(self, path: Path) -> None:
        import re

        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning("playbook_loader.not_found", path=str(path))
            return

        for name, cfg in data.get("playbooks", {}).items():
            playbook = Playbook(
                name=name,
                product_area=cfg.get("product_area", "GENERAL"),
                triggers=cfg.get("triggers", []),
                diagnostic_steps=[
                    DiagnosticStep(**step) for step in cfg.get("diagnostic_steps", [])
                ],
                common_causes=[
                    CommonCause(**cause) for cause in cfg.get("common_causes", [])
                ],
                escalation_criteria=cfg.get("escalation_criteria", []),
                empathy_statements=cfg.get("empathy_statements", []),
                resolution=cfg.get("resolution", []),
                note=cfg.get("note"),
            )
            self._playbooks[name] = playbook

            # Index triggers for fast lookup
            for trigger in playbook.triggers:
                pattern = re.compile(re.escape(trigger), re.IGNORECASE)
                self._triggers.append((name, playbook.product_area, pattern))

        logger.info("playbook_loader.loaded", count=len(self._playbooks))

    def lookup(self, message: str, product_area: str | None = None) -> Playbook | None:
        """Find the best matching playbook for a customer message."""
        message_lower = message.lower()

        candidates: list[tuple[int, Playbook]] = []
        for name, area, pattern in self._triggers:
            if product_area and area != product_area:
                continue
            matches = len(pattern.findall(message_lower))
            if matches > 0:
                candidates.append((matches, self._playbooks[name]))

        if not candidates:
            return None

        # Return the playbook with the most trigger matches
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def get_by_name(self, name: str) -> Playbook | None:
        """Get a playbook by its exact name."""
        return self._playbooks.get(name)

    def get_diagnostic_steps(self, playbook_name: str) -> list[DiagnosticStep]:
        """Get diagnostic steps for a playbook."""
        pb = self._playbooks.get(playbook_name)
        return pb.diagnostic_steps if pb else []

    def get_empathy_statement(self, playbook_name: str) -> str | None:
        """Get a random empathy statement for billing/emotional situations."""
        pb = self._playbooks.get(playbook_name)
        if pb and pb.empathy_statements:
            import random
            return random.choice(pb.empathy_statements)
        return None
