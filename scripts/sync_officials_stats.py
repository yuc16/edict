#!/usr/bin/env python3
"""同步各官员统计数据 → data/officials_stats.json"""
from __future__ import annotations

import datetime
import json
import logging
import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from file_lock import atomic_json_read, atomic_json_write

from edict_runtime.config import AGENT_META, AGENTS_HOME, DATA_DIR, load_runtime_config


log = logging.getLogger("officials")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

MODEL_PRICING = {
    "openai-codex/gpt-5.1-codex": {"in": 1.25, "out": 10.0},
    "openai-codex/gpt-5.3-codex": {"in": 2.0, "out": 12.0},
    "openai-codex/gpt-5.4": {"in": 2.5, "out": 15.0},
    "openai-codex/gpt-5-mini": {"in": 0.3, "out": 2.4},
    "openai-codex/gpt-4o": {"in": 2.5, "out": 10.0},
}


def scan_agent(agent_id: str) -> dict:
    sessions_file = AGENTS_HOME / agent_id / "sessions" / "sessions.json"
    if not sessions_file.exists():
        return {"tokens_in": 0, "tokens_out": 0, "sessions": 0, "last_active": None, "messages": 0}
    data = atomic_json_read(sessions_file, {})
    if not isinstance(data, dict):
        data = {}
    tokens_in = sum(int(v.get("inputTokens", 0) or 0) for v in data.values())
    tokens_out = sum(int(v.get("outputTokens", 0) or 0) for v in data.values())
    last_ts = 0
    total_messages = 0
    for value in data.values():
        ts = int(value.get("updatedAt", 0) or 0)
        if ts > last_ts:
            last_ts = ts
        session_file = value.get("sessionFile")
        if session_file:
            path = AGENTS_HOME / agent_id / "sessions" / Path(session_file).name
            if path.exists():
                try:
                    total_messages += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
                except Exception:
                    pass
    last_active = None
    if last_ts:
        last_active = datetime.datetime.fromtimestamp(last_ts / 1000).strftime("%Y-%m-%d %H:%M")
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "sessions": len(data),
        "last_active": last_active,
        "messages": total_messages,
    }


def calc_cost(stats: dict, model: str) -> float:
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["openai-codex/gpt-5.1-codex"])
    return round(
        stats["tokens_in"] / 1e6 * pricing["in"] + stats["tokens_out"] / 1e6 * pricing["out"],
        4,
    )


def get_task_stats(label: str, tasks: list[dict]) -> dict:
    done = [t for t in tasks if t.get("state") == "Done" and t.get("org") == label]
    active = [t for t in tasks if t.get("state") in ("Doing", "Review", "Assigned") and t.get("org") == label]
    flow_hits = 0
    participated = []
    for task in tasks:
        if not str(task.get("id", "")).startswith("JJC"):
            continue
        matched = False
        for entry in task.get("flow_log", []):
            if entry.get("from") == label or entry.get("to") == label:
                flow_hits += 1
                matched = True
        if matched:
            participated.append({"id": task.get("id", ""), "title": task.get("title", ""), "state": task.get("state", "")})
    return {
        "tasks_done": len(done),
        "tasks_active": len(active),
        "flow_participations": flow_hits,
        "participated_edicts": participated,
    }


def get_heartbeat(agent_id: str, live_tasks: list[dict]) -> dict:
    for task in live_tasks:
        meta = task.get("sourceMeta", {}) or {}
        if meta.get("agentId") == agent_id and task.get("heartbeat"):
            return task["heartbeat"]
    return {"status": "idle", "label": "⚪ 待命", "ageSec": None}


def main() -> None:
    runtime_cfg = load_runtime_config()
    tasks = atomic_json_read(DATA_DIR / "tasks_source.json", [])
    live = atomic_json_read(DATA_DIR / "live_status.json", {})
    live_tasks = live.get("tasks", []) if isinstance(live, dict) else []

    officials = []
    for agent_id, meta in AGENT_META.items():
        model = runtime_cfg.get("agents", {}).get(agent_id, {}).get("model", runtime_cfg.get("defaultModel", ""))
        stats = scan_agent(agent_id)
        task_stats = get_task_stats(meta["label"], tasks)
        heartbeat = get_heartbeat(agent_id, live_tasks)
        cost_usd = calc_cost(stats, model)
        officials.append(
            {
                "id": agent_id,
                "label": meta["label"],
                "role": meta["role"],
                "emoji": meta["emoji"],
                "rank": "正一品" if agent_id in {"zhongshu", "menxia", "shangshu"} else ("储君" if agent_id == "taizi" else "正二品"),
                "model": model,
                "model_short": model.split("/", 1)[-1] if model else "",
                "sessions": stats["sessions"],
                "tokens_in": stats["tokens_in"],
                "tokens_out": stats["tokens_out"],
                "cache_read": 0,
                "cache_write": 0,
                "tokens_total": stats["tokens_in"] + stats["tokens_out"],
                "messages": stats["messages"],
                "cost_usd": cost_usd,
                "cost_cny": round(cost_usd * 7.2, 2),
                "last_active": stats["last_active"],
                "heartbeat": heartbeat,
                "tasks_done": task_stats["tasks_done"],
                "tasks_active": task_stats["tasks_active"],
                "flow_participations": task_stats["flow_participations"],
                "participated_edicts": task_stats["participated_edicts"],
                "merit_score": task_stats["tasks_done"] * 10 + task_stats["flow_participations"] * 2 + min(stats["sessions"], 20),
            }
        )

    officials.sort(key=lambda item: item["merit_score"], reverse=True)
    for idx, item in enumerate(officials, start=1):
        item["merit_rank"] = idx

    totals = {
        "tokens_total": sum(item["tokens_total"] for item in officials),
        "cache_total": 0,
        "cost_usd": round(sum(item["cost_usd"] for item in officials), 2),
        "cost_cny": round(sum(item["cost_cny"] for item in officials), 2),
        "tasks_done": sum(item["tasks_done"] for item in officials),
    }
    top = officials[0] if officials else {}
    atomic_json_write(
        DATA_DIR / "officials_stats.json",
        {
            "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "officials": officials,
            "totals": totals,
            "top_official": top.get("label", ""),
        },
    )
    log.info("%s officials | cost=¥%s | top=%s", len(officials), totals["cost_cny"], top.get("label", ""))


if __name__ == "__main__":
    main()
