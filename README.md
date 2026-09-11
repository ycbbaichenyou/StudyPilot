# StudyPilot

StudyPilot 是一个供本科生学习和实践 AI 应用开发的项目。

当前仓库已完成 V1 Stage 3-3。项目提供可启动的 FastAPI 后端、Vue 3 前端、健康检查接口，以及基于 SQLite 和 SQLAlchemy 2.x 的知识库、文档上传和文档管理能力。Alembic 管理数据库结构版本；PyMuPDF 和 python-docx 与显式 TXT/Markdown 解析器组成文档解析 Pipeline。Chunk、RAG、Embedding、Chroma、LLM 和 Agent 尚未实现。

## 环境要求

- Python 3.12
- uv
- Node.js `^20.19.0 || >=22.12.0`
- npm

## 启动后端

```bash
cd backend
uv sync --dev --python 3.12 --no-python-downloads
# 仅用于全新数据库；已有 Stage 2 数据库请先阅读下方的 stamp 说明。
uv run --no-python-downloads alembic upgrade head
uv run --no-python-downloads uvicorn app.main:app --reload
```

后端默认运行在 `http://127.0.0.1:8000`：

- 健康检查：`http://127.0.0.1:8000/api/health`
- 知识库接口：`http://127.0.0.1:8000/api/knowledge-bases`
- 文档解析接口：`POST http://127.0.0.1:8000/api/documents/{document_id}/parse`
- API 文档：`http://127.0.0.1:8000/docs`

运行 `alembic upgrade head` 会在全新环境中创建 `backend/data/studypilot.db` 及当前数据表，并把数据库升级到最新 revision。应用启动时仍会通过 `init_db()` 创建缺失的当前数据表，以保持现有启动和测试行为；已有数据库的结构升级必须使用 Alembic。该本地数据库文件已被 Git 忽略。

如果数据库是在 Stage 2 或更早版本中由应用创建的，并且已经包含 `knowledge_bases` 和 `documents` 表，请只在首次接入 Alembic 时执行：

```bash
cd backend
uv run --no-python-downloads alembic stamp 0001_stage_2_baseline
```

`stamp` 只登记当前 revision，不会重建或修改已有业务表。之后统一使用 `alembic upgrade head` 应用新迁移。

默认无需设置数据库环境变量。如需使用独立的 SQLite 数据库，请在启动进程前设置 `STUDYPILOT_DATABASE_URL`，例如：

```bash
export STUDYPILOT_DATABASE_URL="sqlite:////absolute/path/to/studypilot.db"
```

项目通过 Python 标准库读取进程环境，不会自动加载 `.env`。仓库根目录的 `.env.example` 提供了安全示例；如需使用 `.env`，请通过 shell 或 `uv run --env-file ../.env ...` 将其载入。

## 数据库迁移

Alembic 与应用共用 `STUDYPILOT_DATABASE_URL` 和 SQLAlchemy `Base.metadata`。修改模型后，可在后端目录生成并检查迁移：

```bash
cd backend
uv run --no-python-downloads alembic revision --autogenerate -m "describe schema change"
uv run --no-python-downloads alembic check
uv run --no-python-downloads alembic upgrade head
```

提交迁移前必须阅读自动生成的内容，确认它只包含本次模型变化。

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
