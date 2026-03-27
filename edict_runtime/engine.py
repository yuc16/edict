from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from file_lock import atomic_json_read, atomic_json_update  # type: ignore

from .codex import CodexClient, extract_json_object
from .config import (
    AGENT_META,
    DATA_DIR,
    OUTPUTS_DIR,
    RUNTIME_STATE_PATH,
    load_runtime_config,
    load_runtime_state,
    now_iso,
    save_runtime_state,
    sessions_dir,
    skills_dir,
    soul_source,
    workspace_dir,
)


TASKS_FILE = DATA_DIR / "tasks_source.json"
STATE_AGENT_MAP = {
    "Taizi": "taizi",
    "Zhongshu": "zhongshu",
    "Menxia": "menxia",
    "Assigned": "shangshu",
    "Review": "shangshu",
    "Pending": "taizi",
}
ORG_AGENT_MAP = {
    "礼部": "libu",
    "户部": "hubu",
    "兵部": "bingbu",
    "刑部": "xingbu",
    "工部": "gongbu",
    "吏部": "libu_hr",
    "中书省": "zhongshu",
    "门下省": "menxia",
    "尚书省": "shangshu",
}
AGENT_LABEL_TO_ID = {meta["label"]: agent_id for agent_id, meta in AGENT_META.items()}
TERMINAL_STATES = {"Done", "Cancelled"}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _load_tasks() -> list[dict[str, Any]]:
    return atomic_json_read(TASKS_FILE, [])


def _find_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    return next((task for task in tasks if task.get("id") == task_id), None)


def _guess_dept(title: str) -> str:
    text = (title or "").lower()
    if any(key in text for key in ["文档", "博客", "邮件", "总结", "公告", "写作", "翻译"]):
        return "礼部"
    if any(key in text for key in ["数据", "报表", "统计", "分析", "excel", "csv", "指标"]):
        return "户部"
    if any(key in text for key in ["测试", "审查", "review", "漏洞", "安全", "合规", "bug"]):
        return "刑部"
    if any(key in text for key in ["部署", "docker", "k8s", "运维", "监控", "infra", "基础设施"]):
        return "工部"
    if any(key in text for key in ["招聘", "组织", "流程", "agent", "技能", "培训"]):
        return "吏部"
    return "兵部"


def _default_todos(title: str) -> list[dict[str, Any]]:
    return [
        {"id": 1, "title": f"明确任务目标：{title[:32]}", "status": "completed", "detail": "需求边界已整理"},
        {"id": 2, "title": "形成执行方案与验收标准", "status": "in-progress", "detail": "等待门下省审议"},
        {"id": 3, "title": "交由对应部门落地", "status": "not-started", "detail": "尚未派发"},
    ]


def _safe_json(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        data = extract_json_object(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return fallback


def _skill_descriptions(agent_id: str) -> list[str]:
    results: list[str] = []
    base = skills_dir(agent_id)
    if not base.exists():
        return results
    for item in sorted(base.iterdir()):
        md = item / "SKILL.md"
        if not md.exists():
            continue
        try:
            desc = ""
            for line in md.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("---"):
                    desc = line[:120]
                    break
            if desc:
                results.append(f"{item.name}: {desc}")
        except Exception:
            continue
    return results[:8]


def _soul_excerpt(agent_id: str) -> str:
    src = soul_source(agent_id)
    if not src.exists():
        return ""
    return src.read_text(encoding="utf-8", errors="ignore")[:4000]


def _task_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "state": task.get("state"),
        "org": task.get("org"),
        "now": task.get("now"),
        "block": task.get("block"),
        "targetDept": task.get("targetDept", ""),
        "review_round": task.get("review_round", 0),
        "todos": task.get("todos", []),
        "flow_log": (task.get("flow_log") or [])[-6:],
        "templateId": task.get("templateId", ""),
        "templateParams": task.get("templateParams", {}),
        "ac": task.get("ac", ""),
    }


def _runtime_session_update(
    agent_id: str,
    *,
    session_id: str,
    model: str,
    session_file: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    index_path = sessions_dir(agent_id) / "sessions.json"

    def modifier(data: dict[str, Any]) -> dict[str, Any]:
        current = data or {}
        existing = current.get(session_id, {})
        current[session_id] = {
            "sessionFile": session_file,
            "updatedAt": int(time.time() * 1000),
            "inputTokens": int(existing.get("inputTokens", 0)) + input_tokens,
            "outputTokens": int(existing.get("outputTokens", 0)) + output_tokens,
            "cacheRead": 0,
            "cacheWrite": 0,
            "model": model,
        }
        return current

    atomic_json_update(index_path, modifier, {})


def _session_log(agent_id: str, session_id: str, role: str, text: str) -> None:
    session_dir = sessions_dir(agent_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / f"{session_id}.jsonl"
    payload = {
        "timestamp": now_iso(),
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    }
    with session_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _set_agent_state(agent_id: str, status: str, **extra: Any) -> None:
    state = load_runtime_state()
    agents = state.setdefault("agents", {})
    item = agents.setdefault(agent_id, {})
    item.update(extra)
    item["status"] = status
    item["updatedAt"] = now_iso()
    any_running = any(info.get("status") == "running" for info in agents.values())
    state["engine"] = {
        "alive": True,
        "status": "running" if any_running or status == "running" else "idle",
        "checkedAt": now_iso(),
    }
    save_runtime_state(state)


def _refresh_dashboard() -> None:
    for script in ("refresh_live_data.py", "sync_officials_stats.py"):
        path = SCRIPTS_DIR / script
        try:
            subprocess.run([sys.executable, str(path)], cwd=str(ROOT), timeout=30)
        except Exception:
            continue


class EdictRuntime:
    def __init__(self) -> None:
        self._client = CodexClient()
        self._active_tasks: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def agent_for_state(self, state: str, task: dict[str, Any]) -> str | None:
        agent_id = STATE_AGENT_MAP.get(state)
        if agent_id is None and state in {"Doing", "Next"}:
            agent_id = ORG_AGENT_MAP.get(task.get("org", ""))
        return agent_id

    def dispatch_for_state(
        self,
        task_id: str,
        task: dict[str, Any],
        state: str,
        trigger: str = "state-transition",
    ) -> None:
        agent_id = self.agent_for_state(state, task)
        if not agent_id:
            return

        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            current = _find_task(tasks, task_id)
            if not current:
                return tasks
            sched = current.setdefault("_scheduler", {})
            sched.update(
                {
                    "lastDispatchAt": now_iso(),
                    "lastDispatchStatus": "queued",
                    "lastDispatchAgent": agent_id,
                    "lastDispatchTrigger": trigger,
                }
            )
            current.setdefault("flow_log", []).append(
                {
                    "at": now_iso(),
                    "from": "本地编排器",
                    "to": current.get("org", ""),
                    "remark": f"🧭 已入队派发：{state} → {agent_id}（{trigger}）",
                }
            )
            current["updatedAt"] = now_iso()
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])
        self.run_agent(agent_id, task_id, self._build_dispatch_message(agent_id, task), trigger)

    def run_agent(self, agent_id: str, task_id: str, message: str, trigger: str) -> None:
        with self._lock:
            running = self._active_tasks.get(task_id)
            if running and running.is_alive():
                return
            thread = threading.Thread(
                target=self._agent_thread,
                args=(agent_id, task_id, message, trigger),
                daemon=True,
            )
            self._active_tasks[task_id] = thread
            thread.start()

    def wake_agent(self, agent_id: str, message: str = "") -> None:
        thread = threading.Thread(
            target=self._wake_thread,
            args=(agent_id, message or f"系统心跳检测，请确认你可以继续处理事务。时间：{now_iso()}"),
            daemon=True,
        )
        thread.start()

    def _wake_thread(self, agent_id: str, message: str) -> None:
        session_id = f"wake-{uuid.uuid4().hex[:8]}"
        cfg = load_runtime_config()
        model = cfg.get("agents", {}).get(agent_id, {}).get("model", cfg.get("defaultModel"))
        _set_agent_state(agent_id, "running", taskId=None, model=model)
        _session_log(agent_id, session_id, "user", message)
        try:
            system = self._agent_system(agent_id)
            user = (
                "你当前被系统唤醒，不需要创建新任务。"
                "请用 1-2 句中文确认当前职责、说明你下一步会关注什么。\n\n"
                f"唤醒消息：{message}"
            )
            response = self._client.complete_text(model=model, system=system, user=user)
            reply = response.text.strip() or f"{AGENT_META[agent_id]['label']}已就位。"
            _session_log(agent_id, session_id, "assistant", reply)
            _runtime_session_update(
                agent_id,
                session_id=session_id,
                model=model,
                session_file=f"{session_id}.jsonl",
                input_tokens=response.usage.get("input_tokens") or _estimate_tokens(user),
                output_tokens=response.usage.get("output_tokens") or _estimate_tokens(reply),
            )
            task_id = self._extract_task_id(message)
            if task_id:
                self._append_progress(task_id, agent_id, reply)
        except Exception as exc:
            _session_log(agent_id, session_id, "assistant", f"系统唤醒失败：{exc}")
            _set_agent_state(agent_id, "error", lastError=str(exc)[:200], taskId=None)
            return
        _set_agent_state(agent_id, "idle", lastError="", taskId=None)
        _refresh_dashboard()

    def _agent_thread(self, agent_id: str, task_id: str, message: str, trigger: str) -> None:
        cfg = load_runtime_config()
        model = cfg.get("agents", {}).get(agent_id, {}).get("model", cfg.get("defaultModel"))
        session_id = f"{task_id}-{uuid.uuid4().hex[:8]}"
        next_dispatch: tuple[dict[str, Any], str] | None = None
        _set_agent_state(agent_id, "running", taskId=task_id, model=model, trigger=trigger)
        _session_log(agent_id, session_id, "user", message)
        try:
            task = self._load_task(task_id)
            if not task:
                raise RuntimeError(f"任务不存在: {task_id}")
            system = self._agent_system(agent_id)
            prompt = self._stage_prompt(agent_id, task, message)
            response = self._client.complete_text(model=model, system=system, user=prompt)
            reply = response.text.strip()
            _session_log(agent_id, session_id, "assistant", reply)
            _runtime_session_update(
                agent_id,
                session_id=session_id,
                model=model,
                session_file=f"{session_id}.jsonl",
                input_tokens=response.usage.get("input_tokens") or _estimate_tokens(prompt),
                output_tokens=response.usage.get("output_tokens") or _estimate_tokens(reply),
            )
            result = self._apply_agent_result(task_id, agent_id, reply)
            _set_agent_state(agent_id, "idle", lastError="", taskId=task_id)
            _refresh_dashboard()
            if result and result.get("next_state") and result["next_state"] not in TERMINAL_STATES:
                refreshed = self._load_task(task_id)
                if refreshed:
                    next_dispatch = (
                        refreshed,
                        refreshed.get("state", result["next_state"]),
                    )
        except Exception as exc:
            self._mark_dispatch_failed(task_id, agent_id, str(exc))
            _session_log(agent_id, session_id, "assistant", f"执行失败：{exc}")
            _set_agent_state(agent_id, "error", lastError=str(exc)[:200], taskId=task_id)
            _refresh_dashboard()
        finally:
            with self._lock:
                current = self._active_tasks.get(task_id)
                if current is threading.current_thread():
                    self._active_tasks.pop(task_id, None)
        if next_dispatch:
            refreshed, next_state = next_dispatch
            self.dispatch_for_state(task_id, refreshed, next_state, trigger=f"agent:{agent_id}")

    def _mark_dispatch_failed(self, task_id: str, agent_id: str, error: str) -> None:
        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task = _find_task(tasks, task_id)
            if not task:
                return tasks
            sched = task.setdefault("_scheduler", {})
            sched.update(
                {
                    "lastDispatchAt": now_iso(),
                    "lastDispatchStatus": "failed",
                    "lastDispatchAgent": agent_id,
                    "lastDispatchError": error[:200],
                }
            )
            task.setdefault("progress_log", []).append(
                {"at": now_iso(), "agent": agent_id, "text": f"执行失败：{error[:200]}", "todos": task.get("todos", [])}
            )
            task["now"] = f"❌ {agent_id} 执行失败：{error[:80]}"
            task["updatedAt"] = now_iso()
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])

    def _append_progress(self, task_id: str, agent_id: str, text: str, todos: list[dict[str, Any]] | None = None) -> None:
        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task = _find_task(tasks, task_id)
            if not task:
                return tasks
            task.setdefault("progress_log", []).append(
                {
                    "at": now_iso(),
                    "agent": agent_id,
                    "text": text,
                    "todos": todos if todos is not None else task.get("todos", []),
                }
            )
            task["updatedAt"] = now_iso()
            task["sourceMeta"] = {"agentId": agent_id, "updatedAt": now_iso()}
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])

    def _load_task(self, task_id: str) -> dict[str, Any] | None:
        return _find_task(_load_tasks(), task_id)

    def _build_dispatch_message(self, agent_id: str, task: dict[str, Any]) -> str:
        title = task.get("title", "(无标题)")
        target_dept = task.get("targetDept", "")
        messages = {
            "taizi": f"任务ID: {task['id']}\n旨意: {title}\n请完成分拣并转交中书省。",
            "zhongshu": f"任务ID: {task['id']}\n旨意: {title}\n请起草方案、拆解 TODO，并决定拟派发部门。",
            "menxia": f"任务ID: {task['id']}\n旨意: {title}\n请审议中书省方案，决定准奏或封驳。",
            "shangshu": (
                f"任务ID: {task['id']}\n旨意: {title}\n"
                f"{'建议执行部门: ' + target_dept if target_dept else '请自行决定执行部门。'}"
            ),
        }
        return messages.get(
            agent_id,
            f"任务ID: {task['id']}\n旨意: {title}\n你负责当前执行阶段，请输出你的专业处理结论。",
        )

    def _agent_system(self, agent_id: str) -> str:
        meta = AGENT_META[agent_id]
        soul = _soul_excerpt(agent_id)
        skills = _skill_descriptions(agent_id)
        skill_text = "\n".join(f"- {item}" for item in skills) if skills else "- 当前未安装额外技能"
        return (
            f"你是三省六部中的 {meta['label']}（{meta['role']}）。"
            f"你的职责是：{meta['duty']}。\n"
            "你在一个多智能体编排系统里工作，必须严格沿着制度流转，不得越权。\n"
            "回答必须基于当前阶段职责，给出可执行的结构化结论。\n"
            "除非用户明确要求，否则不要写解释性长文。\n"
            "如果要求返回 JSON，就只返回 JSON 对象。\n\n"
            f"SOUL 摘要：\n{soul}\n\n"
            f"已安装技能：\n{skill_text}"
        )

    def _stage_prompt(self, agent_id: str, task: dict[str, Any], message: str) -> str:
        snapshot = json.dumps(_task_snapshot(task), ensure_ascii=False, indent=2)
        dept_guess = task.get("targetDept") or _guess_dept(task.get("title", ""))
        if agent_id == "taizi":
            return (
                "请处理一条新旨意。判断它是否需要进入正式流程，并给出提炼后的标题与转呈说明。\n"
                "返回 JSON："
                '{"clean_title":"", "summary":"", "next_state":"Zhongshu", "remark":""}\n\n'
                f"调度消息：\n{message}\n\n任务快照：\n{snapshot}"
            )
        if agent_id == "zhongshu":
            return (
                "你负责起草方案。请拆解任务、补充验收标准，并决定建议执行部门。\n"
                "返回 JSON："
                '{"summary":"","todos":[{"id":1,"title":"","status":"not-started","detail":""}],'
                '"acceptance_criteria":"","target_dept":"","next_state":"Menxia","remark":""}\n\n'
                f"建议执行部门可以参考：{dept_guess}\n\n任务快照：\n{snapshot}"
            )
        if agent_id == "menxia":
            return (
                "你负责审议方案。请判断是否通过；如不通过，要指出明确的修订方向。\n"
                "返回 JSON："
                '{"approved":true,"summary":"","next_state":"Assigned","remark":"","revision_notes":""}\n\n'
                f"任务快照：\n{snapshot}"
            )
        if agent_id == "shangshu" and task.get("state") == "Assigned":
            return (
                "你负责把任务派发到执行部门。请选择一个主责部门，可附带协作部门。\n"
                "返回 JSON："
                '{"primary_dept":"","collaborators":[],"summary":"","next_state":"Doing","remark":""}\n\n'
                f"默认建议部门：{dept_guess}\n\n任务快照：\n{snapshot}"
            )
        if agent_id == "shangshu" and task.get("state") == "Review":
            return (
                "你负责汇总执行成果并形成回奏。请产出最终总结。\n"
                "返回 JSON："
                '{"summary":"","memorial":"","next_state":"Done","remark":""}\n\n'
                f"任务快照：\n{snapshot}"
            )
        return (
            "你处于执行部门。请基于当前任务生成阶段性成果，给出摘要与 markdown 产出。\n"
            "返回 JSON："
            '{"summary":"","deliverable_markdown":"","next_state":"Review","remark":""}\n\n'
            f"任务快照：\n{snapshot}"
        )

    def _apply_agent_result(self, task_id: str, agent_id: str, raw_text: str) -> dict[str, Any]:
        task = self._load_task(task_id)
        if not task:
            raise RuntimeError(f"任务不存在: {task_id}")

        if agent_id == "taizi":
            fallback = {
                "clean_title": task.get("title", ""),
                "summary": "太子已完成分拣，转交中书省起草。",
                "next_state": "Zhongshu",
                "remark": "太子分拣完毕，转中书省起草",
            }
            data = _safe_json(raw_text, fallback)
            return self._apply_taizi(task_id, agent_id, data)
        if agent_id == "zhongshu":
            fallback = {
                "summary": "已形成初步方案并拆解任务。",
                "todos": _default_todos(task.get("title", "")),
                "acceptance_criteria": "输出完整方案、执行结果与关键风险说明。",
                "target_dept": task.get("targetDept") or _guess_dept(task.get("title", "")),
                "next_state": "Menxia",
                "remark": "中书省方案提交门下省审议",
            }
            data = _safe_json(raw_text, fallback)
            return self._apply_zhongshu(task_id, agent_id, data)
        if agent_id == "menxia":
            approved_default = (task.get("review_round") or 0) >= 1
            fallback = {
                "approved": True if approved_default else True,
                "summary": "门下省已完成审议。",
                "next_state": "Assigned",
                "remark": "门下省准奏，通过执行",
                "revision_notes": "",
            }
            data = _safe_json(raw_text, fallback)
            return self._apply_menxia(task_id, agent_id, data)
        if agent_id == "shangshu" and task.get("state") == "Assigned":
            fallback = {
                "primary_dept": task.get("targetDept") or _guess_dept(task.get("title", "")),
                "collaborators": [],
                "summary": "尚书省已完成派发。",
                "next_state": "Doing",
                "remark": "尚书省开始派发执行",
            }
            data = _safe_json(raw_text, fallback)
            return self._apply_shangshu_dispatch(task_id, agent_id, data)
        if agent_id == "shangshu" and task.get("state") == "Review":
            fallback = {
                "summary": "已汇总执行成果并完成回奏。",
                "memorial": raw_text.strip() or "任务完成。",
                "next_state": "Done",
                "remark": "尚书省汇总完毕，回奏完成",
            }
            data = _safe_json(raw_text, fallback)
            return self._apply_shangshu_review(task_id, agent_id, data)

        fallback = {
            "summary": f"{AGENT_META[agent_id]['label']}已完成阶段执行。",
            "deliverable_markdown": raw_text.strip() or f"# {task.get('title', '')}\n\n执行完成。",
            "next_state": "Review",
            "remark": "执行完成，提交尚书省汇总",
        }
        data = _safe_json(raw_text, fallback)
        return self._apply_department(task_id, agent_id, data)

    def _apply_taizi(self, task_id: str, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task = _find_task(tasks, task_id)
            if not task:
                return tasks
            task["title"] = (data.get("clean_title") or task.get("title") or "").strip() or task.get("title")
            task["state"] = "Zhongshu"
            task["org"] = "中书省"
            task["now"] = data.get("summary") or "太子已接旨，转交中书省。"
            task.setdefault("flow_log", []).append(
                {"at": now_iso(), "from": "太子", "to": "中书省", "remark": data.get("remark") or "太子转呈中书省"}
            )
            task.setdefault("progress_log", []).append(
                {"at": now_iso(), "agent": agent_id, "text": task["now"], "todos": task.get("todos", [])}
            )
            task["updatedAt"] = now_iso()
            task["sourceMeta"] = {"agentId": agent_id, "updatedAt": now_iso()}
            sched = task.setdefault("_scheduler", {})
            sched["lastDispatchStatus"] = "success"
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])
        return {"next_state": "Zhongshu"}

    def _apply_zhongshu(self, task_id: str, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        todos = data.get("todos")
        if not isinstance(todos, list) or not todos:
            todos = _default_todos(data.get("summary") or "")

        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task = _find_task(tasks, task_id)
            if not task:
                return tasks
            task["todos"] = todos
            task["ac"] = data.get("acceptance_criteria") or task.get("ac") or "形成完整结果与关键结论。"
            task["targetDept"] = data.get("target_dept") or task.get("targetDept") or _guess_dept(task.get("title", ""))
            task["state"] = "Menxia"
            task["org"] = "门下省"
            task["now"] = data.get("summary") or "中书省已起草方案，待门下省审议。"
            task.setdefault("flow_log", []).append(
                {"at": now_iso(), "from": "中书省", "to": "门下省", "remark": data.get("remark") or "方案提交审议"}
            )
            task.setdefault("progress_log", []).append(
                {"at": now_iso(), "agent": agent_id, "text": task["now"], "todos": todos}
            )
            task["updatedAt"] = now_iso()
            task["sourceMeta"] = {"agentId": agent_id, "updatedAt": now_iso()}
            sched = task.setdefault("_scheduler", {})
            sched["lastDispatchStatus"] = "success"
            sched["snapshot"] = {
                "state": "Menxia",
                "org": "门下省",
                "now": task["now"],
                "savedAt": now_iso(),
                "note": "zhongshu-complete",
            }
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])
        return {"next_state": "Menxia"}

    def _apply_menxia(self, task_id: str, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        approved = bool(data.get("approved", True))

        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task = _find_task(tasks, task_id)
            if not task:
                return tasks
            if approved:
                task["state"] = "Assigned"
                task["org"] = "尚书省"
                task["now"] = data.get("summary") or "门下省准奏，移交尚书省派发。"
                to_dept = "尚书省"
                remark = data.get("remark") or "门下省准奏，通过执行"
            else:
                round_num = int(task.get("review_round") or 0) + 1
                task["review_round"] = round_num
                task["state"] = "Zhongshu"
                task["org"] = "中书省"
                task["now"] = data.get("revision_notes") or "门下省封驳，退回中书省修订。"
                to_dept = "中书省"
                remark = data.get("remark") or "门下省封驳，退回修订"
            task.setdefault("flow_log", []).append(
                {"at": now_iso(), "from": "门下省", "to": to_dept, "remark": remark}
            )
            task.setdefault("progress_log", []).append(
                {"at": now_iso(), "agent": agent_id, "text": task["now"], "todos": task.get("todos", [])}
            )
            task["updatedAt"] = now_iso()
            task["sourceMeta"] = {"agentId": agent_id, "updatedAt": now_iso()}
            task.setdefault("_scheduler", {})["lastDispatchStatus"] = "success"
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])
        return {"next_state": "Assigned" if approved else "Zhongshu"}

    def _apply_shangshu_dispatch(self, task_id: str, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        dept = data.get("primary_dept") or _guess_dept(data.get("summary", ""))
        dept = dept if dept in AGENT_LABEL_TO_ID else _guess_dept(dept)

        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task = _find_task(tasks, task_id)
            if not task:
                return tasks
            task["targetDept"] = dept
            task["state"] = "Doing"
            task["org"] = dept
            task["now"] = data.get("summary") or f"尚书省已派发至{dept}执行。"
            task.setdefault("flow_log", []).append(
                {"at": now_iso(), "from": "尚书省", "to": dept, "remark": data.get("remark") or f"派发至{dept}"}
            )
            if data.get("collaborators"):
                task.setdefault("progress_log", []).append(
                    {
                        "at": now_iso(),
                        "agent": agent_id,
                        "text": f"协同部门：{'、'.join(data['collaborators'])}",
                        "todos": task.get("todos", []),
                    }
                )
            task["updatedAt"] = now_iso()
            task["sourceMeta"] = {"agentId": agent_id, "updatedAt": now_iso()}
            task.setdefault("_scheduler", {})["lastDispatchStatus"] = "success"
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])
        return {"next_state": "Doing"}

    def _apply_department(self, task_id: str, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        output_path = OUTPUTS_DIR / f"{task_id}.md"
        deliverable = data.get("deliverable_markdown") or f"# {task_id}\n\n{data.get('summary', '')}\n"
        output_path.write_text(deliverable, encoding="utf-8")

        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task = _find_task(tasks, task_id)
            if not task:
                return tasks
            task["output"] = str(output_path)
            task["state"] = "Review"
            task["org"] = "尚书省"
            task["now"] = data.get("summary") or "执行部门已完成任务，待尚书省汇总。"
            todos = task.get("todos", [])
            for todo in todos:
                if todo.get("status") != "completed":
                    todo["status"] = "completed"
            task["todos"] = todos
            task.setdefault("flow_log", []).append(
                {
                    "at": now_iso(),
                    "from": AGENT_META[agent_id]["label"],
                    "to": "尚书省",
                    "remark": data.get("remark") or "执行完成，提交汇总",
                }
            )
            task.setdefault("progress_log", []).append(
                {"at": now_iso(), "agent": agent_id, "text": task["now"], "todos": todos}
            )
            task["updatedAt"] = now_iso()
            task["sourceMeta"] = {"agentId": agent_id, "updatedAt": now_iso()}
            task.setdefault("_scheduler", {})["lastDispatchStatus"] = "success"
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])
        return {"next_state": "Review"}

    def _apply_shangshu_review(self, task_id: str, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        output_path = OUTPUTS_DIR / f"{task_id}.md"
        memorial = data.get("memorial") or data.get("summary") or "任务完成。"
        if output_path.exists():
            existing = output_path.read_text(encoding="utf-8", errors="ignore")
        else:
            existing = f"# {task_id}\n\n"
        if "## 尚书省回奏" not in existing:
            existing = existing.rstrip() + "\n\n## 尚书省回奏\n\n" + memorial.strip() + "\n"
        output_path.write_text(existing, encoding="utf-8")

        def modifier(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task = _find_task(tasks, task_id)
            if not task:
                return tasks
            task["output"] = str(output_path)
            task["state"] = "Done"
            task["org"] = "回奏"
            task["now"] = data.get("summary") or "任务已完成，已回奏。"
            task.setdefault("flow_log", []).append(
                {"at": now_iso(), "from": "尚书省", "to": "皇上", "remark": data.get("remark") or "汇总回奏完成"}
            )
            task.setdefault("progress_log", []).append(
                {"at": now_iso(), "agent": agent_id, "text": memorial.strip(), "todos": task.get("todos", [])}
            )
            task["updatedAt"] = now_iso()
            task["sourceMeta"] = {"agentId": agent_id, "updatedAt": now_iso()}
            task.setdefault("_scheduler", {})["lastDispatchStatus"] = "success"
            return tasks

        atomic_json_update(TASKS_FILE, modifier, [])
        return {"next_state": "Done"}

    def _extract_task_id(self, text: str) -> str | None:
        match = re.search(r"(JJC-\d{8}-\d{3})", text)
        return match.group(1) if match else None


_RUNTIME: EdictRuntime | None = None


def get_runtime() -> EdictRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = EdictRuntime()
    return _RUNTIME
