# StudyPilot

StudyPilot 是一个供本科生学习和实践 AI 应用开发的项目。

当前仓库已完成 V1 Stage 5-2。项目提供可启动的 FastAPI 后端、Vue 3 前端、健康检查接口，以及基于 SQLite 和 SQLAlchemy 2.x 的知识库、文档上传、文档管理、文档解析、内容查询、字符分块和显式 Embedding 能力。Alembic 管理数据库结构版本；PyMuPDF 和 python-docx 与显式 TXT/Markdown 解析器组成文档解析 Pipeline；DashScope `text-embedding-v4` 生成向量，Chroma PersistentClient 保存每个 Chunk 对应的向量记录。Chunk 重建、成功重新解析、文档删除和知识库删除会显式维护 SQLite、Chroma 与上传文件之间的生命周期一致性。检索、RAG、LLM 和 Agent 尚未实现。

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
- 文档信息查询：`GET http://127.0.0.1:8000/api/documents/{document_id}`
- 解析内容查询：`GET http://127.0.0.1:8000/api/documents/{document_id}/contents`
- 文档解析接口：`POST http://127.0.0.1:8000/api/documents/{document_id}/parse`
- Chunk 查询：`GET http://127.0.0.1:8000/api/documents/{document_id}/chunks`
- Chunk 创建或重建：`POST http://127.0.0.1:8000/api/documents/{document_id}/chunks`
- Embedding 状态查询：`GET http://127.0.0.1:8000/api/documents/{document_id}/embedding`
- Embedding 创建或重建：`POST http://127.0.0.1:8000/api/documents/{document_id}/embedding`
- API 文档：`http://127.0.0.1:8000/docs`

运行 `alembic upgrade head` 会在全新环境中创建 `backend/data/studypilot.db` 及当前数据表，并把数据库升级到最新 revision。应用启动时不会自动创建或升级数据表；所有正常运行环境都必须先通过 Alembic 将数据库升级到当前版本。测试可以显式调用 `init_db(create_tables=True)` 创建隔离 schema。该本地数据库文件已被 Git 忽略。

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

调用 Embedding 接口前必须配置 `DASHSCOPE_API_KEY`。默认使用 DashScope 中国（北京）公共 endpoint、`text-embedding-v4`、1024 维稠密向量，以及 `backend/data/chroma` 持久化目录；可以通过 `.env.example` 中的可选变量覆盖 endpoint、模型名称和 Chroma 路径。Chroma collection 显式采用 cosine distance，并按 provider、model、dimensions 和向量 schema version 隔离。上传、解析和分块都不会自动触发 Embedding。

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
