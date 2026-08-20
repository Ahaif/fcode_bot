"""Gunner role: fire at the first legal target on the facing line."""

from __future__ import annotations

from fcode import Controller

import map_knowledge


def run_gunner(ct: Controller, state: object) -> None:
    """One gunner turn — auto-fire if ammo + cooldown allow."""
    del state  # no per-gunner memory needed yet
    map_knowledge.update(ct)
    target = ct.get_gunner_target()
    if target is not None and ct.can_fire(target):
        ct.fire(target)
