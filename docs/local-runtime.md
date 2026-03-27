# Edict Local Runtime

当前仓库已经移除了旧运行时依赖，改为仓库内自带的本地多智能体编排器：

- 模型接入：`oauth-cli-kit` + ChatGPT Plus / Pro OAuth
- 模型请求：`https://chatgpt.com/backend-api/codex/responses`
- 运行时目录：项目根目录下的 `.edict_runtime/`
- 看板服务：`dashboard/server.py`
- 数据文件：`data/*.json`

## 1. 准备环境

```bash
cd /Users/wangyc/Desktop/projects/edict
uv venv .venv
uv sync
```

## 2. 登录 Codex OAuth

首次使用需要完成一次 OAuth 登录：

```bash
uv run python scripts/login_openai_codex.py
```

检查本地 token 是否可用：

```bash
uv run python scripts/login_openai_codex.py --check
```

## 3. 启动看板与刷新循环

终端 1：

```bash
source .venv/bin/activate
python dashboard/server.py
```

终端 2：

```bash
source .venv/bin/activate
bash scripts/run_loop.sh
```

打开浏览器：

```bash
open http://127.0.0.1:7891
```

## 4. 当前运行方式

- 任务仍然写入 `data/tasks_source.json`
- 前端看板继续读取 `live_status.json` / `agent_config.json`
- Agent 会话与心跳写入 `.edict_runtime/agents/<agent>/sessions/`
- 每个 agent 的 workspace 位于 `.edict_runtime/workspace-<agent>/`
- 模型切换会更新 `data/runtime_config.json`

## 5. 说明

- 当前实现保留了原有看板和大部分 JSON 契约，所以前端无需重写。
- `朝堂议政` 面板也改为走同一套 Codex OAuth provider。
- 若某个任务卡在 `queued`，重启 `dashboard/server.py` 会触发启动恢复并重新派发。
