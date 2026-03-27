from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RUNTIME_HOME = BASE_DIR / ".edict_runtime"
AGENTS_HOME = RUNTIME_HOME / "agents"
OUTPUTS_DIR = DATA_DIR / "outputs"
RUNTIME_CONFIG_PATH = DATA_DIR / "runtime_config.json"
RUNTIME_STATE_PATH = DATA_DIR / "runtime_state.json"

DEFAULT_MODEL = "openai-codex/gpt-5.1-codex"

KNOWN_MODELS = [
    {"id": "openai-codex/gpt-5.1-codex", "label": "GPT-5.1 Codex", "provider": "OpenAI Codex"},
    {"id": "openai-codex/gpt-5.3-codex", "label": "GPT-5.3 Codex", "provider": "OpenAI Codex"},
    {"id": "openai-codex/gpt-5.4", "label": "GPT-5.4", "provider": "OpenAI Codex"},
    {"id": "openai-codex/gpt-5-mini", "label": "GPT-5 Mini", "provider": "OpenAI Codex"},
    {"id": "openai-codex/gpt-4o", "label": "GPT-4o", "provider": "OpenAI Codex"},
]

AGENT_META: dict[str, dict[str, Any]] = {
    "taizi": {
        "label": "太子",
        "role": "太子",
        "duty": "消息分拣与任务转呈",
        "emoji": "🤴",
        "allowAgents": ["zhongshu"],
    },
    "zhongshu": {
        "label": "中书省",
        "role": "中书令",
        "duty": "方案规划与任务拆解",
        "emoji": "📜",
        "allowAgents": ["menxia", "shangshu"],
    },
    "menxia": {
        "label": "门下省",
        "role": "侍中",
        "duty": "审议与封驳",
        "emoji": "🔍",
        "allowAgents": ["zhongshu", "shangshu"],
    },
    "shangshu": {
        "label": "尚书省",
        "role": "尚书令",
        "duty": "执行派发与汇总",
        "emoji": "📮",
        "allowAgents": ["hubu", "libu", "bingbu", "xingbu", "gongbu", "libu_hr"],
    },
    "hubu": {
        "label": "户部",
        "role": "户部尚书",
        "duty": "数据分析与成本测算",
        "emoji": "💰",
        "allowAgents": ["shangshu"],
    },
    "libu": {
        "label": "礼部",
        "role": "礼部尚书",
        "duty": "文档撰写与表达整理",
        "emoji": "📝",
        "allowAgents": ["shangshu"],
    },
    "bingbu": {
        "label": "兵部",
        "role": "兵部尚书",
        "duty": "工程实现与编码执行",
        "emoji": "⚔️",
        "allowAgents": ["shangshu"],
    },
    "xingbu": {
        "label": "刑部",
        "role": "刑部尚书",
        "duty": "测试审查与风险识别",
        "emoji": "⚖️",
        "allowAgents": ["shangshu"],
    },
    "gongbu": {
        "label": "工部",
        "role": "工部尚书",
        "duty": "基础设施与部署实现",
        "emoji": "🔧",
        "allowAgents": ["shangshu"],
    },
    "libu_hr": {
        "label": "吏部",
        "role": "吏部尚书",
        "duty": "组织协调与流程治理",
        "emoji": "👔",
        "allowAgents": ["shangshu"],
    },
    "zaochao": {
        "label": "钦天监",
        "role": "朝报官",
        "duty": "新闻简报与朝会信息整理",
        "emoji": "📰",
        "allowAgents": [],
    },
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def today_str(fmt: str = "%Y%m%d") -> str:
    return dt.datetime.now().strftime(fmt)


def workspace_dir(agent_id: str) -> Path:
    return RUNTIME_HOME / f"workspace-{agent_id}"


def skills_dir(agent_id: str) -> Path:
    return workspace_dir(agent_id) / "skills"


def sessions_dir(agent_id: str) -> Path:
    return AGENTS_HOME / agent_id / "sessions"


def soul_source(agent_id: str) -> Path:
    return BASE_DIR / "agents" / agent_id / "SOUL.md"


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_runtime_config() -> dict[str, Any]:
    agents: dict[str, Any] = {}
    for agent_id, meta in AGENT_META.items():
        agents[agent_id] = {
            "id": agent_id,
            "label": meta["label"],
            "role": meta["role"],
            "duty": meta["duty"],
            "emoji": meta["emoji"],
            "model": DEFAULT_MODEL,
            "workspace": str(workspace_dir(agent_id)),
            "allowAgents": meta.get("allowAgents", []),
        }
    return {
        "generatedAt": now_iso(),
        "defaultModel": DEFAULT_MODEL,
        "dispatchChannel": "tui",
        "knownModels": KNOWN_MODELS,
        "agents": agents,
    }


def ensure_runtime_layout() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_HOME.mkdir(parents=True, exist_ok=True)
    for agent_id in AGENT_META:
        ws = workspace_dir(agent_id)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "skills").mkdir(parents=True, exist_ok=True)
        sessions = sessions_dir(agent_id)
        sessions.mkdir(parents=True, exist_ok=True)
        write_json(sessions / "sessions.json", load_json(sessions / "sessions.json", {}))
        src = soul_source(agent_id)
        if src.exists():
            dst = ws / "soul.md"
            content = src.read_text(encoding="utf-8", errors="ignore")
            if not dst.exists() or dst.read_text(encoding="utf-8", errors="ignore") != content:
                dst.write_text(content, encoding="utf-8")
    if not RUNTIME_CONFIG_PATH.exists():
        write_json(RUNTIME_CONFIG_PATH, default_runtime_config())
    if not RUNTIME_STATE_PATH.exists():
        write_json(
            RUNTIME_STATE_PATH,
            {"engine": {"alive": True, "status": "idle", "checkedAt": now_iso()}, "agents": {}},
        )
    for name, default in {
        "tasks_source.json": [],
        "live_status.json": {"tasks": [], "syncStatus": {"ok": True}},
        "agent_config.json": {},
        "model_change_log.json": [],
        "last_model_change_result.json": {},
        "officials_stats.json": {"officials": [], "totals": {}},
        "pending_model_changes.json": [],
    }.items():
        path = DATA_DIR / name
        if not path.exists():
            write_json(path, default)


def load_runtime_config() -> dict[str, Any]:
    ensure_runtime_layout()
    cfg = load_json(RUNTIME_CONFIG_PATH, {})
    if not cfg:
        cfg = default_runtime_config()
        write_json(RUNTIME_CONFIG_PATH, cfg)
    return cfg


def save_runtime_config(cfg: dict[str, Any]) -> None:
    cfg["generatedAt"] = now_iso()
    write_json(RUNTIME_CONFIG_PATH, cfg)


def load_runtime_state() -> dict[str, Any]:
    ensure_runtime_layout()
    return load_json(
        RUNTIME_STATE_PATH,
        {"engine": {"alive": True, "status": "idle", "checkedAt": now_iso()}, "agents": {}},
    )


def save_runtime_state(state: dict[str, Any]) -> None:
    state.setdefault("engine", {})
    state["engine"]["alive"] = True
    state["engine"]["checkedAt"] = now_iso()
    write_json(RUNTIME_STATE_PATH, state)


ensure_runtime_layout()

