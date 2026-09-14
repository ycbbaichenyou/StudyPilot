# StudyPilot V1 系统架构

## 1. 文档状态

本文记录 StudyPilot 当前已经确定的 V1 架构边界，作为后续设计和实现的共同基线。

当前仓库已完成 V1 Stage 6-3。Stage 0 已完成可独立启动的最小 FastAPI 后端、`GET /api/health`、对应自动化测试，以及可独立启动的最小 Vue 3 + Vite 前端骨架。Stage 1 已增加 SQLite、SQLAlchemy 2.x、`KnowledgeBase` 和 `Document` 基础模型，并提供知识库的最小创建、查询和删除 API。Stage 2 增加了原始文档上传、保存、列表和删除能力。Stage 3-1 引入 Alembic 数据库迁移基础设施，并以 Stage 2 数据库结构建立首个基线 revision。Stage 3-2 增加文档解析状态、错误与完成时间字段，以及保存有序解析文本单元和来源位置的 `DocumentContent` 模型。Stage 3-3 实现 PDF、DOCX、TXT 和 Markdown 的显式解析 Pipeline，并通过应用服务将解析结果原子替换到 `DocumentContent`。Stage 4-1 增加单个文档信息和解析内容查询 API；解析内容由查询服务显式按 `sequence` 升序返回。Stage 4-2 增加 `Chunk` 模型、确定性的字符分块器，以及显式创建、重建和查询 Chunk 的 API。Stage 5-1 增加文档 Embedding 状态、DashScope `text-embedding-v4` 适配器、Chroma 持久化边界，以及显式创建和查询 Embedding 状态的 API。Stage 5-2 增加 `stale` Embedding 状态和按 Document 清理 Chroma 的统一边界，并在 Chunk 重建、成功重新解析、单个 Document 删除和 KnowledgeBase 删除时维护跨 SQLite、Chroma 与上传文件的生命周期一致性。Stage 6-1 增加 Query Embedding、基于 record allowlist 的 Chroma cosine search、SQLite 来源重载与二次有效性校验，以及知识库 Search API。Stage 6-2 增加独立 Context Assembly 服务及 Context API，在不改变 Retrieval 的前提下按字符预算组装完整 Chunk 和引用信息。Stage 6-3 增加固定 Prompt Builder、DashScope `qwen-plus` LLM Adapter、同步 Answer Generation Service 和 Answer API。前端与后端当前仍是各自独立运行，尚未实现业务级界面交互。

基础向量检索、Context Assembly、Prompt 和单轮 Answer Generation 已经实现；Agent、Tool Calling、Memory、多轮对话和前端问答界面尚未实现。`DocumentContent` 保存原始解析文本单元，`Chunk` 保存从单个 DocumentContent 派生的字符切片，Chroma 保存 Chunk 的向量副本，三者职责不同。本文中的“确定”表示后续 V1 实现必须遵守的方向；除当前知识库、文档管理、文档解析、分块、Embedding、Search、Context 和 Answer 接口外的后续业务接口与界面细节仍需在对应任务中按最小需求确定。

## 2. V1 目标

StudyPilot 是面向本科生的长期 AI 应用开发学习项目。V1 架构需要支持一条可以被完整理解和调试的基础链路：导入学习资料、解析并建立索引、检索相关内容、让模型基于检索上下文生成回答，并展示可追踪的来源信息。

V1 优先目标是：

- 让核心数据流清晰可见，适合学习和讲解。
- 以最少的必要组件形成可运行的端到端闭环。
- 保持模块边界清楚，便于单元测试和逐步替换局部实现。
- 在不过度设计的前提下，保留可靠的错误处理和来源追踪能力。

## 3. 确定的技术栈

| 范围 | 技术 | V1 职责 |
| --- | --- | --- |
| 后端 | Python、FastAPI | 提供 HTTP API，编排文档处理、检索和问答流程 |
| 前端 | Vue 3、Vite | 提供浏览器端交互界面并调用后端 API |
| 结构化数据 | SQLite、SQLAlchemy、Alembic | 保存结构化业务数据，并通过 migration 管理数据库结构版本 |
| 向量存储 | Chroma | 保存文本块的向量和检索元数据，执行相似度检索 |
| PDF 解析 | PyMuPDF | 提取 PDF 文本及页码信息 |
| DOCX 解析 | python-docx | 提取 Word 文档的段落等结构化文本 |

V1 暂时禁止使用 LangChain 抽象核心 RAG 流程。

## 4. 总体架构

V1 使用“前后端分离 + 后端模块化单体 + 本地持久化”的简单架构。

```text
┌─────────────────────────────┐
│ Vue 3 + Vite 单页应用       │
│ 上传资料 / 提问 / 展示来源  │
└──────────────┬──────────────┘
               │ HTTP / JSON
┌──────────────▼──────────────┐
│ FastAPI 模块化单体          │
│                             │
│ API 层                      │
│   ↓                         │
│ 应用服务层                  │
│   ↓                         │
│ 文档处理 / RAG 核心流程     │
│   ↓                         │
│ 数据访问与外部模型适配      │
└───────┬───────────┬─────────┘
        │           │
┌───────▼──────┐ ┌──▼─────────────┐
│ SQLite       │ │ Chroma          │
│ SQLAlchemy   │ │ 文本块与向量    │
│ 结构化数据   │ │ 检索元数据      │
└──────────────┘ └────────────────┘
```

这只是职责划分，不要求采用复杂的“整洁架构”或领域驱动设计。V1 应通过少量、命名直接的 Python 模块实现这些边界。

## 5. 组件职责

### 5.1 Vue 3 前端

前端负责：

- 提供资料导入、处理状态、提问和答案展示的交互界面。
- 对用户输入和文件选择进行基础校验。
- 调用 FastAPI，并显式展示加载、成功、空结果和失败状态。
- 展示后端返回的来源信息，不在浏览器中实现解析、向量化或检索。

前端不直接访问 SQLite、Chroma 或模型服务，也不保存任何服务端 API Key。

### 5.2 FastAPI API 层

API 层负责：

- 定义 HTTP 请求、响应、校验、状态码和错误格式。
- 将合法请求交给应用服务处理。
- 返回稳定的数据结构，不把数据库对象、内部异常堆栈或敏感配置直接暴露给前端。

路由只做接口层工作，不应堆积文档解析、检索或提示词拼装逻辑。

### 5.3 应用服务层

应用服务负责组织完整用例，例如“导入一个文档”或“回答一个问题”。它协调解析器、分块器、嵌入服务、Chroma、SQLAlchemy 和生成模型，但不隐藏各阶段的输入输出。

V1 保持同步、直接的调用链。只有在真实需求和测量证据出现后，才考虑任务队列、分布式服务或更复杂的异步基础设施。

### 5.4 文档处理模块

文档处理模块按文件类型选择解析器：

- PDF：使用 PyMuPDF，每页生成一个文本单元并保留 1-based 页码。
- DOCX：使用 python-docx，按正文段落顺序生成文本单元并保留 1-based 段落号。
- TXT：使用 UTF-8 或 UTF-8 BOM 读取，每行生成一个文本单元并保留 1-based 行号。
- Markdown：使用 UTF-8 读取为一个完整文本单元，保留 Markdown 原文和完整行号范围。

四种解析器只负责将 `Path` 转换为 `list[ParsedTextUnit]`，不接触数据库或 SQLAlchemy Session。`ParsedTextUnit.sequence` 从 0 开始，来源位置从 1 开始。解析应用服务负责查询文档、切换状态、选择解析器，并在一个事务中删除旧内容、写入全部新内容和将状态更新为 `parsed`。解析或结果事务失败时，服务回滚未完成写入并将状态记录为 `parse_failed`，因此不会留下部分新内容。

Stage 4-2 的分块器只接收单个 `DocumentContent.text`，不读取原文件、不调用 parser，也不跨越 DocumentContent 边界。默认按 800 个 Python 字符分块并保留 100 字符重叠；优先在窗口后半段的换行或中英文句末标点后切分，没有自然边界时按硬边界切分。每个 Chunk 保存其在 DocumentContent 文本中的 0-based、左闭右开字符 offset，因此必须满足 `chunk.text == content.text[start_offset:end_offset]`。纯空白内容不生成 Chunk。当前不进行分词、token 计算或文本归一化。

分块是解析之后的独立同步步骤：只有状态为 `parsed` 的文档可以显式创建或重建 Chunk。服务先在内存中生成全部分块结果，再在一个事务中原子替换旧 Chunk；失败时保留上一次完整结果。成功重新解析会通过外键级联删除由旧 DocumentContent 派生的 Chunk，解析失败和事务回滚则保留原有内容与 Chunk。

Embedding 是分块之后的独立同步步骤，不由上传、解析或分块自动触发。只有解析状态为 `parsed` 且至少已有一个 Chunk 的文档可以显式创建 Embedding。DashScope 适配器使用原生 HTTP API 调用默认模型 `text-embedding-v4`，每批最多发送 10 个 Chunk，显式指定 `text_type=document`、1024 维稠密向量，并按响应中的 `text_index` 恢复输入顺序。API Key 只从环境变量读取。

跨 SQLite 与 Chroma 的写入使用 generation 隔离。服务先确认 Document 存在、状态为 `parsed` 且已有 Chunk，再记录 `embedding` 状态并打开 Chroma；因此 Chroma 初始化或配置失败也会进入统一的 `embedding_failed` 状态流程。每次尝试生成新的 32 位 `generation_id`，在内存中取得全部向量后，再以 `generation_id:chunk_id` 作为 Chroma record id 写入新一代记录。写入后必须从 Chroma 重新读取候选 generation，并验证 record 数量和 `chunk_id` 集合与 SQLite 当前 Chunk 完全一致；只有验证和 SQLite 完成状态提交都成功后，`Document.embedding_generation_id` 才指向新一代。失败 generation 会被尽力清理，即使清理本身失败，它也不会成为 SQLite 指向的有效 generation，上一代有效 generation 也不会被删除。成功重建 Embedding 后才会清理上一代记录。Stage 5-1 不实现文档删除或重新解析时的 Chroma 同步。

Stage 5-2-2 重建 Chunk 时，先完整生成全部 ChunkDraft，再在同一个 SQLite transaction 中原子替换 Chunk；除 `pending` 且没有有效 generation 的文档外，同时将 Embedding 标记为 `stale` 并清空 generation、完成时间和错误。该 transaction 提交成功后才打开 Chroma，并按 `document_id` 删除全部旧向量；成功后以第二个 SQLite transaction 将状态从 `stale` 改为 `pending`。Chroma 清理失败不回退已经提交的新 Chunk，文档保持 `stale`、保存固定安全错误，并由 Chunk API 返回可重试的 503。`stale` 状态的后续 Chunk 重建会再次尝试清理。

Stage 5-2-3 成功重新解析时，在替换 DocumentContent、通过级联删除旧 Chunk、更新解析完成状态的同一个 SQLite transaction 中失效已有或历史 Embedding。首次 transaction 提交成功后复用相同的 Chroma 清理及 `stale` 到 `pending` 流程；清理失败时保留新的解析结果并由解析 API 返回 503。解析器失败或解析结果 transaction 失败时不清理 Chroma，并保留上一份完整 DocumentContent、Chunk 和 Embedding；解析状态仍按既有语义记录为 `parse_failed`。解析成功仍不自动重新分块，新的 Chunk 继续由独立 Chunk API 显式创建。

### 5.5 RAG 核心模块

Stage 6-1 只实现基础 Retrieval，不组装上下文或生成答案。服务先确认 KnowledgeBase 存在，再从 SQLite 查询属于该知识库、Embedding 状态为 `embedded` 且具有当前 generation id 的 Chunk，并生成 `{generation_id}:{chunk_id}` allowlist。allowlist 为空时直接返回不可检索错误，不调用 DashScope 或 Chroma。查询文本通过同一个 DashScope 适配器以 `text_type=query` 生成 1024 维稠密向量；文档 Embedding 继续使用 `text_type=document`。

Chroma Search 必须接收显式查询向量和完整 record allowlist，不配置 embedding function，也不在读取路径创建 collection。Chroma 返回的 id、metadata 和 distance 经存储边界验证后，Retrieval 服务通过 `Chunk → DocumentContent → Document` JOIN 从 SQLite 重新加载业务数据，并再次检查知识库归属、`embedded` 状态、当前 generation 和 record id。失效、旧 generation、孤立或跨知识库命中会被丢弃。最终结果按 `(distance, document_id, content_sequence, chunk_sequence, chunk_id)` 稳定排序，并限制为请求的 `top_k`。

Stage 6-2 的 Context Assembly 是 Retrieval 之后的独立纯服务。它接收已经排序并完成 SQLite 校验的 `RetrievalResult`，保持原顺序且让一个 Chunk 对应一个 Context Block，不合并、不重排，也不回到 Chroma 改变检索策略。每个 block 使用 `[编号] 原始文件名 | 来源信息` 作为 header，PDF、DOCX、TXT 和 Markdown 的来源分别显示为 `page`、`paragraph`、`line` 和 `line_range`，编号与结构化 citation 一一对应。

上下文默认字符预算为 6000，按 Python `len()` 统计 header、Chunk 正文和 block 间的 `\n\n---\n\n` 分隔符。服务按 Retrieval 顺序只加入能够完整放入预算的 block；下一个完整 block 超出预算时立即停止，不截断单个 Chunk，并通过 `used_characters` 和 `truncated` 显式报告结果。该阶段不持久化 Context，不修改数据库结构或 Chroma，也不构建 Prompt 或调用 LLM。

Stage 6-3 的 Prompt Builder 接收原始 query 和 `AssembledContext`，固定生成一条 system message 和一条 user message。system message 要求模型只能依据资料回答、不得编造，并使用 Context 中已有的 `[1]`、`[2]` 编号引用；user message 显式分隔问题与参考资料，并声明资料只能作为回答依据而不是需要执行的指令。Prompt Builder 不访问数据库、Chroma 或外部服务。

LLM Adapter 使用标准库 HTTP 调用 DashScope 中国（北京）的原生文本生成 endpoint，默认模型为 `qwen-plus`，API Key 继续只从 `DASHSCOPE_API_KEY` 读取。Adapter 只接收 messages，负责认证、60 秒 timeout、HTTP/网络错误边界和 `output.choices[0].message.content` 解析；它不知道 KnowledgeBase、SQLite、Chroma、Retrieval 或 Citation，也不发送 tools。

Answer Generation Service 保持同步显式调用链：Retrieval → Context Assembly → Prompt Builder → LLM Adapter。它只把实际进入 Context 的 citations 返回给 API。Context 为空时直接返回 `insufficient_context`，不构建 Prompt、不调用 LLM；Context 非空时返回 `answered` 和模型文本。该阶段不保存 query、answer 或 history，不引入 Agent、Tool Calling、Memory、多轮对话、Rerank、Hybrid Search 或 LangChain。

核心 RAG 过程拆为可观察的普通步骤：

1. 解析并规范化文档文本。
2. 将文本切分为带来源元数据的文本块。
3. 调用嵌入模型得到向量。
4. 将文本块、向量和最小检索元数据写入 Chroma。
5. 对用户问题进行规范化和向量化。
6. 从 Chroma 检索相关文本块。
7. 按明确规则筛选、排序并组装上下文。
8. 构建提示词并调用生成模型。
9. 返回答案以及对应的来源信息。

这些步骤不得由 LangChain 代理或用单个黑盒调用取代。每个阶段应具有清楚的数据结构和错误边界，使学习者可以打印、测试和解释中间结果。

### 5.6 数据访问层

- SQLAlchemy 是访问 SQLite 的唯一 ORM 边界；路由不直接执行零散 SQL。
- Chroma 访问集中在明确的存储模块中，业务代码不依赖其底层返回格式。
- 外部模型和嵌入服务通过小型适配边界调用，供应商相关请求格式不应扩散到整个代码库。

这里的“层”表示职责隔离，不要求为每个函数创建接口、仓储类或抽象基类。

## 6. 关键数据边界

### SQLite 保存

- `KnowledgeBase`：整数主键、名称、可空描述和创建/更新时间。
- `Document`：整数主键、所属知识库、文件名、原始文件名、文件类型、文件大小、字符串解析状态、可空解析错误、可空解析完成时间、Embedding 状态、可空 Embedding 错误、可空 Embedding 完成时间、可空有效 generation id 和创建/更新时间。解析状态统一为 `pending`、`parsing`、`parsed` 或 `parse_failed`；Embedding 状态统一为 `pending`、`embedding`、`embedded`、`embedding_failed` 或 `stale`，数据库继续使用字符串列。
- `DocumentContent`：整数主键、所属文档、有序序号、解析文本、来源类型、来源起止位置和创建时间。它保存解析阶段的文本单元，不是后续用于向量检索的 Chunk。
- `Chunk`：整数主键、所属 DocumentContent、在该内容内的有序序号、文本、字符起止 offset 和创建时间。一个 Chunk 只属于一个 DocumentContent；来源类型及页码、段落或行号继续以 DocumentContent 为事实来源。
- 后续业务明确需要的其他结构化数据。

每个 `Document` 必须通过非空外键 `knowledge_base_id` 属于一个 `KnowledgeBase`。所有 SQLite Engine 通过同一个创建函数配置，并在每个连接上启用外键约束。默认 SQLite 文件位于 `backend/data/studypilot.db`；设置 `STUDYPILOT_DATABASE_URL` 后，应用与 Alembic 都使用该环境变量指定的 SQLite URL。

从 Stage 3-1 开始，数据库结构版本由 Alembic migration 管理。`0001_stage_2_baseline` 完整描述 Stage 2 已有结构：全新数据库可以通过 `alembic upgrade head` 创建；已有 Stage 2 数据库使用 `alembic stamp 0001_stage_2_baseline` 接入版本管理，不能对已有业务表重复执行基线 DDL。`0002_stage_3_2_document_content` 为 `documents` 增加解析字段并创建 `document_contents` 表；`0003_stage_4_2_chunk` 创建 `chunks` 表及其外键、索引和完整性约束；`0004_stage_5_1_document_embedding` 为 `documents` 增加 Embedding 状态和有效 generation 字段。应用启动时的 `init_db()` 默认只准备数据库目录，不创建或升级 schema，正常运行前必须执行 `alembic upgrade head`。只有测试创建隔离数据库时，才可以显式调用 `init_db(create_tables=True)` 使用当前 SQLAlchemy metadata 建表。未来模型变化必须先生成并审查 migration，再通过 Alembic 升级。

SQLite 内部以 naive UTC 保存 `created_at` 和 `updated_at`。API 响应在序列化边界将这些时间重新标记为 UTC aware datetime，因此 JSON 时间戳必须包含 `Z` 或 `+00:00`。

### 当前知识库与文档 API

- `POST /api/knowledge-bases`：创建知识库。
- `GET /api/knowledge-bases`：按主键顺序列出知识库。
- `GET /api/knowledge-bases/{knowledge_base_id}`：查询单个知识库。
- `DELETE /api/knowledge-bases/{knowledge_base_id}`：删除知识库。
- `POST /api/knowledge-bases/{knowledge_base_id}/documents`：上传一个支持的原始文档并创建 `Document` 记录。
- `GET /api/knowledge-bases/{knowledge_base_id}/documents`：按主键顺序列出指定知识库的文档。
- `GET /api/documents/{document_id}`：查询单个文档的当前信息。
- `GET /api/documents/{document_id}/contents`：查询文档状态及解析文本单元，内容显式按 `sequence` 升序返回；尚未解析时返回空列表，解析失败时仍返回已有内容。
- `GET /api/documents/{document_id}/chunks`：查询当前已有 Chunk，不触发创建或重建；结果按 DocumentContent 和 Chunk 的 `sequence` 升序返回。
- `POST /api/documents/{document_id}/chunks`：为已解析文档同步创建或原子重建 Chunk；文档不存在返回 404，状态不是 `parsed` 返回 409。
- `GET /api/documents/{document_id}/embedding`：只查询当前 Embedding 状态、错误、完成时间和有效 generation id，不触发向量化。
- `POST /api/documents/{document_id}/embedding`：为已解析且已有 Chunk 的文档同步创建或重建 Embedding；文档不存在返回 404，尚未解析或没有 Chunk 返回 409。模型或 Chroma 操作失败返回 200，并以 `embedding_failed` 和安全错误信息明确表示失败。
- `POST /api/knowledge-bases/{knowledge_base_id}/search`：在知识库当前有效 Embedding allowlist 内执行向量检索；`query` 去除首尾空白后不能为空，`top_k` 默认为 5 且范围为 1 到 20。知识库不存在返回 404，没有有效 embedded Chunk 返回 409，DashScope 失败返回 502，Chroma 失败返回 503；完成搜索但没有合格命中时返回 200 和空结果。
- `POST /api/knowledge-bases/{knowledge_base_id}/context`：复用 Retrieval 服务后组装上下文；请求沿用 `query` 和 `top_k`，并接受默认 6000 的正整数 `max_context_characters`。响应返回 `context`、逐 Chunk 的 `blocks`、与编号对应的 `citations`、`used_characters` 和 `truncated`，错误状态沿用 Search API 的边界。
- `POST /api/knowledge-bases/{knowledge_base_id}/answer`：同步执行 Retrieval、Context Assembly、Prompt 和 LLM 调用；请求沿用 `query`、`top_k` 和 `max_context_characters`。正常回答和 `insufficient_context` 均返回 200；知识库不存在返回 404，没有有效 embedded Chunk 返回 409，Embedding 或 LLM 失败返回 502，Chroma 失败返回 503。响应不暴露 Context、Prompt、messages、generation id 或 Chroma record id。
- `DELETE /api/documents/{document_id}`：删除 `Document` 记录及对应的磁盘文件。
- `POST /api/documents/{document_id}/parse`：同步解析原始文件并保存 `DocumentContent`；文档不存在返回 404，解析失败返回 200 和状态为 `parse_failed` 的文档。

上传接口保存 `.pdf`、`.docx`、`.txt` 和 `.md` 原始文件。Stage 3-3 的解析接口显式触发同步文本抽取；上传本身仍不自动解析。单个文件最大 20 MiB，空文件会被拒绝。

### Chroma 保存

- 每个 Chunk 对应一个 Chroma record；record id 为 `generation_id:chunk_id`。
- Chunk 文本和显式传入的稠密向量；Chroma 不配置或调用 embedding function。
- collection 显式使用 cosine distance；名称由 schema version、provider、model 和 dimensions 共同生成，metadata 同时保存并校验这些配置，避免不同向量空间混入同一 collection。
- `generation_id`、`document_id`、`chunk_id`、`document_content_id`、顺序、offset 和来源位置等检索溯源所需元数据。
- Search 只打开并校验当前向量空间的既有 collection，使用显式 query embedding 和 SQLite 生成的 record id allowlist；搜索过程不写入 collection，也不创建缺失的 collection。

### 跨存储关联

SQLite 文档记录与向量记录共享稳定的 `document_id`，`Chunk.id` 作为稳定的 `chunk_id`。通过 Chunk 对应的 DocumentContent 取得文档标识和适用的来源位置，例如 PDF 页码或 DOCX 段落序号。SQLite 只保存当前有效的 `embedding_generation_id`，不保存向量；后续检索必须同时使用 `document_id` 和有效 generation 约束，不能把未激活或过期 generation 当成有效数据。

Stage 6-1 至 Stage 6-3 中 SQLite 是检索资格和返回业务字段的事实来源。Chroma 只负责在 allowlist 内计算向量距离；即使 Chroma 返回一条记录，也必须在 SQLite hydrate 和二次校验通过后才能进入 Search、Context 或 Answer 流程。API 不暴露内部 generation id、Chroma record id、查询向量或额外 score。Context Assembly 和 Answer Generation 只转换内存中的结果，不新增持久化数据。

不要在两个存储中无理由复制完整业务数据。SQLite 是结构化业务状态的事实来源；Chroma 是向量检索数据的事实来源。

原始上传文件保存在 `backend/data/uploads/`，该目录不会提交到 Git。磁盘文件使用 UUID 生成的安全名称，用户提供的名称只记录在 `original_filename` 中，不参与路径拼接。创建数据库记录失败时删除已保存文件。删除单个 Document 时，若需要清理 Embedding，先以 SQLite transaction 将其标记为 `stale` 并清空有效 generation，再删除并验证该 Document 的 Chroma records 为零；随后才提交 SQLite 记录删除并尽力清理对应磁盘文件。Chroma 清理或验证失败会保留 Document、内容、Chunk 和文件并返回 503；Chroma 已清空但 SQLite 删除失败时，Document 保持可重试的 `stale`。从未 Embedding 的 `pending` Document 不打开 Chroma。

删除 KnowledgeBase 时，先按 Document id 升序取得全部文档，在一个 SQLite transaction 中将所有需要清理的 Embedding 标记为 `stale`，再逐个删除并验证各自的 Chroma records。任一文档清理失败都会保留整个 KnowledgeBase 的 SQLite 数据和文件；此前已成功清理的文档仍保持 `stale`。只有全部清理成功后才在一个 SQLite transaction 中级联删除 KnowledgeBase、Document、DocumentContent 和 Chunk。单个 Document 或 KnowledgeBase 的 SQLite 删除成功后才 best-effort 删除文件；文件不存在时忽略，文件删除失败时记录 warning 但不恢复数据库记录。

## 7. 主要数据流

### 7.1 文档导入与索引

```text
用户选择文件
  → FastAPI 校验类型和请求
  → 创建文档记录并标记处理中
  → PyMuPDF / python-docx 提取文本和位置
  → 文本清洗与分块
  → 生成嵌入向量
  → 写入 Chroma
  → 更新 SQLite 文档状态为成功
```

任一步骤失败时，应记录可诊断但不包含秘密的信息，并让文档状态反映失败。重试不应无控制地生成重复文本块；具体幂等方案在实现数据模型时确定。

### 7.2 检索与回答

```text
用户问题
  → FastAPI 校验
  → 问题向量化
  → Chroma 相似度检索
  → SQLite hydrate 与当前 generation 二次校验
  → 返回排序后的 Chunk 与来源
  → 按顺序和字符预算组装完整 Chunk 与引用（Stage 6-2 截止）
  → 构建固定提示词
  → 调用 DashScope qwen-plus
  → 返回单轮答案和实际 Context 引用（Stage 6-3 截止）
  → 返回前端展示（尚未实现）
```

没有检索到足够上下文或模型调用失败时，应返回明确、可处理的结果，不能伪造来源或静默生成看似确定的答案。

## 8. 配置与安全

- API Key、令牌和其他敏感值只从环境变量读取，本地由未提交的 `.env` 提供。
- 可提交的 `.env.example` 只能包含变量名和安全占位值。
- 前端构建产物中的环境变量对浏览器可见，因此任何秘密都只能保存在后端。
- 数据库地址、Chroma 持久化位置、模型名称和允许的前端来源等可变配置由环境管理，不散落在业务代码中。SQLite 使用可选的 `STUDYPILOT_DATABASE_URL`；Chroma 使用可选的 `STUDYPILOT_CHROMA_PATH`；DashScope 使用必需的 `DASHSCOPE_API_KEY` 和可选的 `DASHSCOPE_EMBEDDING_URL`、`STUDYPILOT_EMBEDDING_MODEL`、`DASHSCOPE_LLM_MODEL`。项目只通过 Python 标准库读取进程环境。
- 日志和错误响应不得包含密钥、完整提示词中的敏感内容、内部堆栈或不必要的本地绝对路径。
- 上传文件必须在进入解析器前检查允许的类型和必要的大小边界；具体限制值由实现任务确定。

## 9. 建议的代码组织

下列目录是后续实现阶段的基线建议。Stage 1 已按实际职责增加数据库、模型、知识库 API 和轻量业务操作模块；其余目录应在真实职责出现时逐步落地，不要一次生成空目录和占位模块。

```text
StudyPilot/
├── AGENTS.md
├── README.md
├── docs/
│   └── ARCHITECTURE.md
├── backend/
│   ├── alembic/           # 数据库 migration 脚本与运行环境
│   ├── alembic.ini        # Alembic 配置入口
│   ├── app/
│   │   ├── api/          # FastAPI 路由和请求/响应模型
│   │   ├── services/     # 用例编排
│   │   ├── document_processing/ # 纯文档解析器和统一解析结果
│   │   ├── llm/          # 生成模型消息结构和供应商适配
│   │   ├── rag/          # 后续分块、嵌入、检索、上下文组装
│   │   ├── db/           # SQLAlchemy 与 SQLite
│   │   ├── stores/       # Chroma 访问
│   │   └── core/         # 配置、日志、公共错误
│   └── tests/
└── frontend/
    └── src/
        ├── api/
        ├── components/
        └── views/
```

如果某个目录在当时只有一个简单文件，可先采用更扁平的结构；当真实职责增加时再拆分。

## 10. 测试边界

V1 的测试应覆盖最重要且容易出错的边界：

- PDF 和 DOCX 解析结果及来源位置。
- 文本清洗、分块边界和元数据继承。
- SQLite 数据访问与状态变化。
- Alembic migration 与 SQLAlchemy metadata 是否保持一致。
- Chroma 显式向量写入、generation 清理和稳定标识关联。
- Query Embedding 的 `text_type`、Chroma allowlist search、返回结构校验和稳定排序。
- Retrieval 的 SQLite 资格筛选、hydrate、generation 二次校验、跨知识库隔离和失效记录过滤。
- Context Assembly 的顺序保持、完整 block 字符预算、来源格式、citation 编号和输入不变性。
- Prompt 的固定结构、引用约束和 Context 非指令边界。
- DashScope LLM Adapter 的请求结构、timeout、安全错误和非法响应校验。
- Answer Generation 的显式调用顺序、citation 一致性、空 Context 跳过 LLM 和 API 状态码。
- 无结果、解析失败、存储失败和模型失败等错误路径。
- FastAPI 请求校验、响应结构和关键用例。
- 前端关键交互状态与前后端契约。

测试工具应在具体实现任务中按必要性选择，不因本文档额外引入依赖。

## 11. V1 明确不做的架构扩展

除非用户后续明确改变范围，V1 不引入：

- LangChain 对核心 RAG 流程的抽象。
- 微服务拆分、服务注册或分布式事务。
- 消息队列、事件总线或独立任务集群。
- SQLite 和 Chroma 之外的替代数据库或向量数据库。
- 与当前学习闭环无关的通用插件平台或过度抽象。

## 12. 尚待后续任务确认的决策

以下事项当前没有足够需求，不应提前猜定：

- 除当前已列出的健康检查、知识库、文档管理、Embedding、Search、Context 和 Answer 接口外，后续业务 API 的具体路径、字段和版本策略。
- 用户、课程、会话等业务实体及数据模型。
- 部署方式、访问控制和生产环境规模。

确定其中任何一项时，应优先选择满足当前用例的最简单方案，并同步更新本文档中受影响的架构说明。
