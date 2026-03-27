#!/usr/bin/env python3
"""
同步本地运行时配置 → data/agent_config.json
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from file_lock import atomic_json_write

from edict_runtime.config import (
    AGENT_META,
    DATA_DIR,
    KNOWN_MODELS,
    load_runtime_config,
    now_iso,
    skills_dir,
    workspace_dir,
)


log = logging.getLogger("sync_agent_config")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")


def get_skills(agent_id: str) -> list[dict]:
    root = skills_dir(agent_id)
    result: list[dict] = []
    if not root.exists():
        return result
    for item in sorted(root.iterdir()):
        if not item.is_dir():
            continue
        md = item / "SKILL.md"
        desc = ""
        if md.exists():
            try:
                for line in md.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("---"):
                        desc = line[:100]
                        break
            except Exception:
                desc = "(读取失败)"
        result.append(
            {
                "name": item.name,
                "path": str(md),
                "exists": md.exists(),
                "description": desc,
            }
        )
    return result


def main() -> None:
    cfg = load_runtime_config()
    agents_cfg = cfg.get("agents", {})
    default_model = cfg.get("defaultModel", "")
    merged_models = cfg.get("knownModels") or KNOWN_MODELS

    result = []
    for agent_id, meta in AGENT_META.items():
        item = agents_cfg.get(agent_id, {})
        result.append(
            {
                "id": agent_id,
                "label": meta["label"],
                "role": meta["role"],
                "duty": meta["duty"],
                "emoji": meta["emoji"],
                "model": item.get("model", default_model),
                "defaultModel": default_model,
                "workspace": str(workspace_dir(agent_id)),
                "skills": get_skills(agent_id),
                "allowAgents": item.get("allowAgents", meta.get("allowAgents", [])),
            }
        )

    existing_cfg = {}
    cfg_path = DATA_DIR / "agent_config.json"
    if cfg_path.exists():
        try:
            existing_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            existing_cfg = {}

    payload = {
        "generatedAt": now_iso(),
        "defaultModel": default_model,
        "knownModels": merged_models,
        "dispatchChannel": cfg.get("dispatchChannel") or existing_cfg.get("dispatchChannel") or "tui",
        "agents": result,
    }
    DATA_DIR.mkdir(exist_ok=True)
    atomic_json_write(cfg_path, payload)
    log.info("%s agents synced", len(result))


if __name__ == "__main__":
    main()
