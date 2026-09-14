# StudyPilot

StudyPilot 是一个供本科生学习和实践 AI 应用开发的项目。

当前仓库已完成 V1 Stage 6-4。项目提供可启动的 FastAPI 后端、Vue 3 前端、健康检查接口，以及基于 SQLite 和 SQLAlchemy 2.x 的知识库、文档上传、文档管理、文档解析、内容查询、字符分块、显式 Embedding、知识库向量检索、Context Assembly、Answer Generation 和 Citation Validation 能力。Alembic 管理数据库结构版本；PyMuPDF 和 python-docx 与显式 TXT/Markdown 解析器组成文档解析 Pipeline；DashScope `text-embedding-v4` 生成文档及查询向量，Chroma PersistentClient 保存每个 Chunk 对应的向量记录并执行 cosine search。Context Assembly 按检索顺序生成带编号引用的完整 Chunk block；固定 Prompt 要求回答只能依据资料并使用对应编号引用，DashScope `qwen-plus` 负责生成单轮回答；Citation Validation 校验模型实际使用的引用编号。Agent、Tool Calling、Memory、多轮对话和前端问答界面尚未实现。

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
- 知识库向量检索：`POST http://127.0.0.1:8000/api/knowledge-bases/{knowledge_base_id}/search`
- 检索上下文组装：`POST http://127.0.0.1:8000/api/knowledge-bases/{knowledge_base_id}/context`
- 知识库单轮问答：`POST http://127.0.0.1:8000/api/knowledge-bases/{knowledge_base_id}/answer`
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

调用 Embedding、知识库检索、上下文组装或问答接口前必须配置 `DASHSCOPE_API_KEY`。Embedding 默认使用 DashScope 中国（北京）公共 endpoint、`text-embedding-v4` 和 1024 维稠密向量；Answer Generation 使用 DashScope 原生文本生成 endpoint，模型由 `DASHSCOPE_LLM_MODEL` 配置并默认为 `qwen-plus`。Chroma 默认使用 `backend/data/chroma` 持久化目录；可以通过 `.env.example` 中的可选变量覆盖 Embedding endpoint、模型名称和 Chroma 路径。文档向量使用 `text_type=document`，查询向量使用 `text_type=query`。上传、解析和分块都不会自动触发 Embedding。

检索请求体包含非空 `query` 和可选 `top_k`；`top_k` 默认为 5，允许范围为 1 到 20。知识库不存在时返回 404，没有任何当前有效的 embedded Chunk 时返回 409；完成检索但没有合格命中时返回 200 和空 `results`。检索结果包含 SQLite 中重新加载的 Chunk、DocumentContent 和 Document 来源字段，不返回内部 generation id、Chroma record id、向量或额外 score。

上下文组装请求复用相同的 `query` 和 `top_k`，并接受默认值为 6000 的正整数 `max_context_characters`。服务保持 Retrieval 顺序，一个 Chunk 生成一个 `[编号] 文件名 | 来源信息` block；字符预算统计 header、正文和 block 分隔符，若下一个完整 block 超出预算就停止，不截断 Chunk。响应同时返回组装后的 `context`、已纳入的 `blocks` 和 `citations`、实际字符数以及是否因预算停止。

问答请求复用 `query`、`top_k` 和 `max_context_characters`。Answer Service 依次执行 Retrieval、Context Assembly、Prompt Builder、LLM Adapter 和 Citation Validation；有 Context 时返回 `status=answered`、模型回答及模型实际引用且存在于 Context 的 citations。`citation_status=valid` 表示所有引用编号有效，`invalid_reference` 表示答案含有 Context 中不存在的编号，`missing` 表示答案没有引用；后两种状态都保留模型原始答案，也不会再次调用 LLM。没有合格检索结果或首个完整 block 无法放入字符预算时，不调用 LLM，返回 200、`status=insufficient_context`、空 answer 和 `citation_status=missing`。知识库不存在返回 404，没有有效 embedded Chunk 返回 409，Embedding 或 LLM 失败返回 502，Chroma 失败返回 503。

`backend/evals/` 保存不依赖真实模型的固定 RAG 评测基线，覆盖可回答、不可回答、多文档来源、Context 截断、无效引用和无引用回答。评测测试使用固定 Retrieval 结果和 mock answer，可通过后端完整测试命令重复运行，不需要配置 DashScope API Key。

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
