# StudyPilot

StudyPilot 是一个面向本科生的可读、可调试 RAG 学习项目。它把课程资料从上传、解析、分块和向量化一路处理到知识库检索、上下文组装、单轮回答与引用校验，并通过 Vue 3 工作台展示完整流程。

当前版本已经形成文档知识库问答 MVP：

- 创建和删除多个知识库，知识库之间严格隔离。
- 上传 PDF、DOCX、TXT 和 Markdown 文件。
- 显式执行 Parse → Chunk → Embedding，并展示失败、重试和过期状态。
- 使用 DashScope Embedding、Chroma 和 SQLite 完成可追踪的向量检索。
- 组装有字符预算的 Context，调用 DashScope `qwen-plus` 生成单轮回答。
- 校验模型实际使用的 `[1]`、`[2]` 引用，并展示来源和引用状态。
- 在前端 Debug 模式中分别查看 Retrieval、Context 和 Answer 指标。

项目刻意不使用 LangChain 隐藏核心 RAG 流程。V1 也不包含 Agent、Tool Calling、Memory、多轮对话、登录权限和流式回答。

## 技术栈

| 范围 | 技术 | 职责 |
| --- | --- | --- |
| 后端 | Python 3.12、FastAPI | HTTP API 和应用流程编排 |
| 前端 | Vue 3、Vite、原生 Fetch | 知识库工作台、状态展示和 RAG 调试 |
| 关系数据 | SQLite、SQLAlchemy 2.x、Alembic | 知识库、文档、解析内容、Chunk 和状态 |
| 向量存储 | Chroma | 保存 Chunk 向量并执行 cosine search |
| 文档解析 | PyMuPDF、python-docx、显式文本解析器 | PDF、DOCX、TXT 和 Markdown 文本提取 |
| 模型服务 | DashScope | `text-embedding-v4` 和 `qwen-plus` |

## 架构说明

StudyPilot 使用前后端分离的模块化单体结构：

```text
Vue 3 工作台
  → FastAPI API
  → 应用 Service
  → SQLite / Chroma / DashScope
```

后端目录职责：

- `backend/app/api/`：请求模型、响应模型、HTTP 状态码和路由。
- `backend/app/services/`：文档生命周期及 Retrieval、Context、Prompt、Answer、Citation 等用例编排。
- `backend/app/document_processing/`：解析器、解析结果结构和确定性字符分块。
- `backend/app/embeddings/`：DashScope Embedding 适配器。
- `backend/app/llm/`：消息结构和 DashScope LLM 适配器。
- `backend/app/stores/`：Chroma collection、向量写入、搜索和清理边界。
- `backend/app/models/`：SQLAlchemy 模型。
- `backend/app/database.py`：SQLite Engine、Session 和初始化入口。

前端目录职责：

- `frontend/src/api/`：基于 Fetch 的 API 契约封装和统一错误处理。
- `frontend/src/composables/`：知识库、文档、问答和 Debug 请求状态。
- `frontend/src/components/`：职责单一的工作台组件。
- `frontend/src/views/`：页面级状态组合和布局。
- `frontend/src/utils/`：安全 Markdown 和纯展示格式化工具。

SQLite 是结构化业务状态的事实来源。Chroma 只保存检索所需的 Chunk 向量和元数据；Search 必须先使用 SQLite 生成 allowlist，再对结果进行 SQLite hydrate 和 generation 二次校验。更完整的边界见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## RAG 流程

文档索引链路：

```text
上传文件
  → 文档解析
  → DocumentContent
  → Chunk
  → DashScope document embedding
  → Chroma
  → SQLite 标记 embedded generation
```

问答链路：

```text
Query
  → DashScope query embedding
  → Chroma allowlist search
  → SQLite hydrate 和二次校验
  → Context Assembly
  → 固定 Prompt
  → DashScope qwen-plus
  → Citation Validation
  → Answer + 实际引用来源
```

Context 按 Retrieval 顺序组装，一个 Chunk 对应一个完整 Block。默认字符预算为 6000；下一个完整 Block 超出预算时停止，不截断单个 Chunk。回答没有引用时返回 `citation_status=missing`，引用了不存在的编号时返回 `invalid_reference`，不会为了修复引用再次调用模型。

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js `^20.19.0 || >=22.12.0`
- npm

## 环境变量

复制安全示例，并填写自己的 DashScope Key：

```bash
cp .env.example .env
```

| 变量 | 必需 | 默认值或用途 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 调用 Embedding、Search、Context、Answer 时必需 | DashScope API Key，不得提交 |
| `DASHSCOPE_EMBEDDING_URL` | 否 | DashScope 中国（北京）Embedding endpoint |
| `STUDYPILOT_EMBEDDING_MODEL` | 否 | `text-embedding-v4` |
| `DASHSCOPE_LLM_MODEL` | 否 | `qwen-plus` |
| `STUDYPILOT_DATABASE_URL` | 否 | `sqlite:///./data/studypilot.db`，相对 backend 目录 |
| `STUDYPILOT_CHROMA_PATH` | 否 | `./data/chroma`，相对 backend 目录 |
| `VITE_API_BASE_URL` | 否 | 前端生产构建使用的 API base URL；本地开发默认使用 Vite `/api` proxy |

后端使用 Python 标准库读取进程环境，不会自动加载 `.env`。下面的启动命令通过 uv 的 `--env-file` 显式加载根目录 `.env`。Vite 只会暴露以 `VITE_` 开头的前端变量；API Key 必须始终只存在于后端环境。

## 本地启动

### 1. 启动后端

```bash
cd backend
uv sync --dev --python 3.12 --no-python-downloads
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env uvicorn app.main:app --reload
```

后端默认地址为 `http://127.0.0.1:8000`，Swagger 文档位于 `http://127.0.0.1:8000/docs`。

应用启动时不会自动创建或升级表。全新环境必须先执行 `alembic upgrade head`。如果本地数据库是在 Stage 2 或更早阶段直接创建、已经含有 `knowledge_bases` 和 `documents` 表，只在首次接入 Alembic 时执行：

```bash
cd backend
uv run --env-file ../.env alembic stamp 0001_stage_2_baseline
uv run --env-file ../.env alembic upgrade head
```

`stamp` 只登记版本，不会重建已有业务表。

### 2. 启动前端

另开一个终端：

```bash
cd frontend
npm ci
npm run dev
```

打开 `http://localhost:5173`。开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。

### 3. 使用工作台

1. 创建知识库。
2. 上传支持的文件；单个文件最大 20 MiB。
3. 等待前端依次完成 Parse、Chunk 和 Embedding。
4. 文档显示“可检索”后提交单轮问题。
5. 查看 Markdown 回答、有效引用和 Context 截断状态。
6. 需要分析质量时切换到 Debug 模式，分别运行 Retrieval 和 Context。

## API 概览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET / POST | `/api/knowledge-bases` | 列出或创建知识库 |
| GET / DELETE | `/api/knowledge-bases/{knowledge_base_id}` | 查询或删除知识库 |
| GET / POST | `/api/knowledge-bases/{knowledge_base_id}/documents` | 列出或上传文档 |
| GET / DELETE | `/api/documents/{document_id}` | 查询或删除文档 |
| GET | `/api/documents/{document_id}/contents` | 查询解析后的有序文本单元 |
| POST | `/api/documents/{document_id}/parse` | 解析文档 |
| GET / POST | `/api/documents/{document_id}/chunks` | 查询或重建 Chunk |
| GET / POST | `/api/documents/{document_id}/embedding` | 查询或重建 Embedding |
| POST | `/api/knowledge-bases/{knowledge_base_id}/search` | 返回排序后的 Retrieval Chunk |
| POST | `/api/knowledge-bases/{knowledge_base_id}/context` | 返回组装后的 Context Blocks |
| POST | `/api/knowledge-bases/{knowledge_base_id}/answer` | 返回单轮回答和实际引用 |

### API 示例

创建知识库：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge-bases \
  -H 'Content-Type: application/json' \
  -d '{"name":"高等数学","description":"课程讲义与复习资料"}'
```

上传并依次处理文档，下面假设知识库 ID 为 `1`、返回的文档 ID 为 `10`：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge-bases/1/documents \
  -F 'file=@./notes.pdf'

curl -X POST http://127.0.0.1:8000/api/documents/10/parse
curl -X POST http://127.0.0.1:8000/api/documents/10/chunks
curl -X POST http://127.0.0.1:8000/api/documents/10/embedding
```

执行检索：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge-bases/1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"什么是增长率？","top_k":5}'
```

生成带引用的回答：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge-bases/1/answer \
  -H 'Content-Type: application/json' \
  -d '{"query":"什么是增长率？","top_k":5,"max_context_characters":6000}'
```

知识库不存在返回 404；没有有效 embedded Chunk 返回 409；请求校验错误返回 422；Embedding 或 LLM 失败返回 502；Chroma 失败返回 503。检索成功但没有合格命中时，Search 返回 200 和空 `results`；Context 为空时 Answer 返回 200 和 `status=insufficient_context`。

## 测试与构建

后端完整测试：

```bash
cd backend
uv run pytest
```

`backend/evals/` 中的固定 RAG 评测基线使用 mock answer，不需要真实 API Key。

前端生产构建：

```bash
cd frontend
npm run build
```

提交前检查：

```bash
git diff --check
git status
```

本地 SQLite、Chroma、上传文件、`.env`、Python 缓存和前端构建产物都已由 Git 忽略。
