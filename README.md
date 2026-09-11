# StudyPilot

StudyPilot 是一个供本科生学习和实践 AI 应用开发的项目。

当前仓库处于 V1 Stage 1，提供可启动的 FastAPI 后端、Vue 3 前端、健康检查接口，以及基于 SQLite 和 SQLAlchemy 2.x 的基础数据模型。当前可通过 API 创建、查询和删除知识库；`Document` 仅建立了数据表和关系，尚未实现上传功能。文档解析、RAG、Embedding、Chroma、LLM 和 Agent 尚未实现。

## 环境要求

- Python 3.12
- uv
- Node.js `^20.19.0 || >=22.12.0`
- npm

## 启动后端

```bash
cd backend
uv sync --dev --python 3.12 --no-python-downloads
uv run --no-python-downloads uvicorn app.main:app --reload
```

后端默认运行在 `http://127.0.0.1:8000`：

- 健康检查：`http://127.0.0.1:8000/api/health`
- 知识库接口：`http://127.0.0.1:8000/api/knowledge-bases`
- API 文档：`http://127.0.0.1:8000/docs`

应用首次启动时会自动创建 `backend/data/studypilot.db` 及当前数据表。该本地数据库文件已被 Git 忽略。

默认无需设置数据库环境变量。如需使用独立的 SQLite 数据库，请在启动进程前设置 `STUDYPILOT_DATABASE_URL`，例如：

```bash
export STUDYPILOT_DATABASE_URL="sqlite:////absolute/path/to/studypilot.db"
```

项目通过 Python 标准库读取进程环境，不会自动加载 `.env`。仓库根目录的 `.env.example` 提供了安全示例；如需使用 `.env`，请通过 shell 或 `uv run --env-file ../.env ...` 将其载入。

## 运行后端测试

```bash
cd backend
uv run --no-python-downloads pytest
```

## 启动前端

```bash
cd frontend
npm ci
npm run dev
```

仓库已有 `package-lock.json`，首次复现环境时优先使用 `npm ci`。只有在明确需要更新依赖及锁文件时才使用 `npm install`。

前端开发服务器默认运行在 `http://localhost:5173`。
