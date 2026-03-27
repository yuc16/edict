#!/usr/bin/env python3
"""应用 data/pending_model_changes.json → data/runtime_config.json"""
from __future__ import annotations

import datetime
import logging
import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from file_lock import atomic_json_read, atomic_json_write

from edict_runtime.config import DATA_DIR, load_runtime_config, save_runtime_config


log = logging.getLogger("model_change")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

PENDING = DATA_DIR / "pending_model_changes.json"
CHANGE_LOG = DATA_DIR / "model_change_log.json"


def main() -> None:
    pending = atomic_json_read(PENDING, [])
    if not pending:
        return

    cfg = load_runtime_config()
    agents = cfg.setdefault("agents", {})
    applied: list[dict] = []
    errors: list[dict] = []

    for change in pending:
        agent_id = str(change.get("agentId", "")).strip()
        new_model = str(change.get("model", "")).strip()
        if not agent_id or not new_model:
            errors.append({"change": change, "error": "missing fields"})
            continue
        current = agents.get(agent_id)
        if not current:
            errors.append({"change": change, "error": f"agent {agent_id} not found"})
            continue
        old_model = current.get("model", cfg.get("defaultModel", ""))
        current["model"] = new_model
        applied.append(
            {
                "at": datetime.datetime.now().isoformat(),
                "agentId": agent_id,
                "oldModel": old_model,
                "newModel": new_model,
            }
        )

    if applied:
        save_runtime_config(cfg)
        log_data = atomic_json_read(CHANGE_LOG, [])
        if not isinstance(log_data, list):
            log_data = []
        log_data.extend(applied)
        if len(log_data) > 200:
            log_data = log_data[-200:]
        atomic_json_write(CHANGE_LOG, log_data)
        for item in applied:
            log.info("%s: %s → %s", item["agentId"], item["oldModel"], item["newModel"])

    atomic_json_write(PENDING, [])
    atomic_json_write(
        DATA_DIR / "last_model_change_result.json",
        {
            "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "applied": applied,
            "errors": errors,
            "gatewayRestarted": False,
            "rolledBack": False,
        },
    )


if __name__ == "__main__":
    main()
