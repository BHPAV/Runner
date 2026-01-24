"""
APOC Trigger Management

Provides utilities to configure Neo4j APOC triggers for:
- Dependency resolution (unblock requests when dependencies complete)
- Cascade rules (automatically create new requests based on graph events)
- Sync detection (mark new data for synchronization)

Usage:
    python -m runner.triggers.setup --install
    python -m runner.triggers.setup --status
    python -m runner.triggers.setup --remove
"""

from .setup import install_triggers, remove_triggers, get_trigger_status
from .cascade_rules import CascadeRuleManager

__all__ = [
    "install_triggers",
    "remove_triggers",
    "get_trigger_status",
    "CascadeRuleManager",
]
