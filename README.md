# 三省六部 · Edict

Edict 是一个本地运行的多智能体任务编排系统。它借鉴“三省六部”的治理结构，把任务强制拆成固定链路：

`皇上/太子 -> 中书省规划 -> 门下省审核 -> 尚书省派发 -> 六部执行 -> 回奏归档`

当前版本已经移除了旧运行时依赖，改为仓库内自带的本地编排器，并通过 ChatGPT Plus / Pro OAuth 的 Codex 通道完成模型调用。前端看板、任务流转、官员统计、技能配置、朝堂议政、天下要闻等功能保持不变。

## 现在的运行结构

- `edict_runtime/`：本地多智能体运行时、任务编排、会话日志、模型调用
- `dashboard/server.py`：看板 API 和静态资源服务
- `edict/frontend/`：React 看板前端
- `scripts/`：数据同步、统计、新闻采集、登录与运维脚本
- `data/`：任务、配置、统计和看板数据

## 快速启动

先创建虚拟环境并安装依赖：

```bash
uv venv .venv
uv sync
```

登录 ChatGPT OAuth：

```bash
uv run python scripts/login_openai_codex.py
```

启动看板和后台刷新：

```bash
uv run python dashboard/server.py
bash scripts/run_loop.sh
```

默认地址：

- 看板：`http://127.0.0.1:7891`
- 健康检查：`http://127.0.0.1:7891/healthz`

## 当前能力

- 多智能体任务流转与自动派发
- 中书/门下/尚书审核链路
- 六部执行与回奏归档
- React 前端看板与任务详情时间线
- 模型配置、技能配置、官员统计
- 朝堂议政与天下要闻
- 本地会话日志和运行状态追踪

## 文档

- 运行说明：[`docs/local-runtime.md`](docs/local-runtime.md)

## 开发

前端源码在 `edict/frontend/src/`，服务端入口在 `dashboard/server.py`，本地编排器在 `edict_runtime/`。
