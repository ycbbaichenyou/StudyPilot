# StudyPilot Project Handoff

本文件是后续 ChatGPT 与 Codex 跨对话、跨阶段开发的长期项目上下文和交接基线。当前事实依据为仓库代码、`README.md`、`AGENTS.md`、`docs/ARCHITECTURE.md` 和 Git 历史；实施时仍须遵守 `AGENTS.md` 与架构文档，并以最新代码和测试复核可能变化的状态。

## 1. 项目定位

StudyPilot 是个人学习资料 RAG 知识库问答系统，同时是面向本科生的 AI 应用开发学习项目。目标是把 PDF、DOCX、TXT 和 Markdown 学习资料变成可检索的知识库，基于资料生成带来源引用的单轮回答。核心链路应保持显式、易读、可调试和可测试。

## 2. 当前稳定版本

- `v1.0.0-rag-mvp` 已完成；Git 标签位于 `3a8ad9f`。
- 项目已发布至 GitHub；仓库 `origin` 指向 `https://github.com/ycbbaichenyou/StudyPilot.git`。
- MIT License 已添加，见根目录 `LICENSE`；README、展示文档和截图已纳入仓库。
- 当前 `main` 是最新开发基线；整理本文件时，`HEAD` 与本地远端跟踪分支 `origin/main` 均为 `2be1cd1`。后续开发前须重新检查 Git 状态。

## 3. 当前技术栈

| 范围 | 技术 | 用途 |
| --- | --- | --- |
| Frontend | Vue 3、Vite、Native Fetch | 单页工作台与 HTTP 请求 |
| Backend | Python 3.12、FastAPI、SQLAlchemy、Alembic、SQLite | API、业务状态与数据库迁移 |
| AI / RAG | DashScope `text-embedding-v4`、`qwen-plus`（Qwen Plus）、Chroma | 文本向量、回答生成与相似度检索 |
| Document | PyMuPDF、python-docx、项目内文本解析器 | PDF、DOCX、TXT、Markdown 解析 |

核心 RAG 流程由项目代码显式实现，不使用 LangChain 抽象。

## 4. 当前完整 RAG Pipeline

```text
Document
→ Parse
→ Chunk
→ Embedding
→ Chroma
→ Retrieval
→ Context Assembly
→ Prompt
→ Qwen Plus
→ Citation Validation
→ Answer
```

索引链路由上传后的 Parse、Chunk、Embedding 操作显式触发；上传本身不自动解析。问答时先对 Query 向量化，用 SQLite 中当前有效的 Chunk 生成 Chroma 查询 allowlist；检索结果还需经 SQLite hydrate 和 generation 二次校验。Context 按检索顺序和字符预算纳入完整 Chunk Block，随后构造固定 Prompt、调用模型并校验答案中的引用编号。

## 5. 当前已完成功能

- **Knowledge Base**：创建、列出、选择和删除多个知识库；检索按知识库隔离。
- **Document upload / Parse / Chunk / Embedding**：上传 PDF、DOCX、TXT、Markdown（单文件上限 20 MiB）；显式解析、确定性分块和 DashScope 向量化，保存阶段状态、来源位置与失败信息。
- **Retrieval / Context / Answer**：基于有效向量检索，组装带来源编号和字符预算的 Context，调用 `qwen-plus` 生成单轮回答。
- **Citation Validation**：识别答案实际使用的编号；返回有效来源并区分 `valid`、`missing`、`invalid_reference`，不伪造或自动修复引用。
- **Knowledge Base isolation**：SQLite allowlist、hydrate 和有效 generation 校验共同隔离不同知识库，过滤失效向量。
- **Document / KB delete**：删除文档或整个知识库时处理 SQLite、Chroma 与上传文件；向量清理失败时保留可诊断、可重试的状态。
- **Retry / Loading / Error**：工作台展示上传和处理进度、失败与重试、请求加载及错误状态。
- **Markdown Answer / Citation UI**：安全展示基础 Markdown 回答、引用列表及引用状态；资料不足时显示 `insufficient_context`。
- **Retrieval Debug / Context Debug / Answer Debug**：查看检索结果与距离、Context Blocks 与字符预算、回答及引用指标。Debug 模式不改变正常 Answer 链路。
- **GitHub 文档和截图**：`README.md`、`docs/PROJECT_OVERVIEW.md`、`docs/DEMO.md` 及 `docs/SCREENSHOTS/` 中的五张产品截图。

## 6. 关键架构约束

- SQLite 是知识库、文档、解析内容、Chunk 及处理状态等结构化业务状态的 **source of truth**。Chroma 只负责向量、检索所需元数据和相似度检索；不能用 Chroma 结果绕过 SQLite 资格校验。
- Embedding 使用 **generation-based lifecycle**：先写入并核验候选 generation，激活后清理旧 generation；检索只接受当前有效 generation。
- 重新解析、重新分块及删除必须使旧向量失效并正确清理，失败状态需可见、可诊断、可重试，避免 SQLite 与 Chroma 不一致时错误命中。
- **Citation Validation** 与 **Knowledge Base 隔离** 必须保留。新功能不得破坏稳定 RAG Core；修改检索、上下文或回答行为时需覆盖这些边界。
- 后续 Agent 作为现有 RAG Core 的上层能力设计，不重写核心 RAG 链路。Memory 后续独立设计，不提前混入当前文档、Chunk 或问答状态模型。
- 保持 Vue 3 + FastAPI 前后端分离的单体边界；敏感配置仅从后端环境变量读取，`.env` 不提交。

## 7. 当前测试基线

- 本次整理时，在 `backend/` 执行 `uv run --no-python-downloads pytest`：**279 passed**。测试覆盖文档和知识库、解析、分块、Embedding 生命周期、Chroma、检索、上下文、Prompt、模型适配、回答、引用及 Alembic migration 等；运行时有 18 条第三方或 Python 弃用警告。
- 在 `frontend/` 执行 `npm run build`：**passed**（Vite 生产构建完成）。仓库当前没有独立的前端自动化测试脚本。
- `backend/evals/rag_baseline.py` 与 `backend/tests/test_rag_evaluation_baseline.py` 已有 **6 个固定 mock 用例**，覆盖可回答、不可回答、多文档来源、Context 截断、无效引用和缺失引用。它们验证现有回答与引用行为；Stage 10-1 仍需建立可量化、可比较的 RAG Evaluation Framework。这里的 279 项是当前代码快照的实测结果，后续以重新运行的结果为准。

## 8. Git 工作流

每个 Stage 按以下顺序推进：

```text
审查 → 设计 → 实施 → 自动测试 → 只读代码审查
→ 修复 BLOCKER / MAJOR → 人工验收 → 用户手动 git add / commit / push
```

Codex **禁止主动执行** `git add`、`git commit`、`git push`。Codex 应交付变更说明、实际测试结果、剩余风险与可执行的验收步骤，由用户完成 Git 提交与推送。

## 9. Codex 执行规则

1. 每次只做一个 Stage，先明确最小可验证范围。
2. 修改前阅读 `README.md`、`AGENTS.md`、`docs/ARCHITECTURE.md`、本文件及相关实现和测试，检查工作区状态。
3. 不做无关重构、批量格式化或目录调整。
4. 不擅自增加依赖；确需新增时先确认现有依赖和标准库不能合理完成任务。
5. 不擅自修改现有 API 契约。
6. 不擅自修改数据库 schema；若 Stage 经确认需要变更，应设计 migration 并同步相关文档和测试。
7. 优先最小、清晰、完整的改动，保留用户已有改动。
8. 所有新能力必须可测试，实际运行相关检查并如实报告结果。
9. 重大能力尽量设计为可关闭或可回滚，并给出验证方式。
10. 保持已有功能兼容，尤其是 RAG Core、引用校验、知识库隔离和向量生命周期。

## 10. 用户协作方式

用户希望获得直接、简短的下一步操作：说明**在哪个终端执行、执行什么命令、预期看到什么**。除非用户主动询问，不做长篇原理讲解。

| 角色 | 职责 |
| --- | --- |
| ChatGPT | 负责技术路线、生成每个 Stage 的 Codex 提示词、审核 Codex 结果，并告诉用户下一步具体操作。 |
| Codex | 负责实际代码与文档执行、测试、只读审查及修复。 |
| 用户 | 负责终端操作、人工验收，以及手动 `git add`、`git commit`、`git push`。 |

## 11. 后续优化优先级

按以下顺序规划；各 Stage 的具体范围在开始时单独审查和设计：

1. Stage 10-1 — RAG Evaluation Framework
2. Stage 10-2 — Retrieval Quality Optimization
3. Stage 10-3 — Reranker
4. Stage 10-4 — Dockerization
5. Stage 10-5 — Streaming Answer
6. Stage 10-6 — Frontend UX Optimization
7. Stage 11 — Agent
8. Stage 12 — Memory
9. Stage 13 — Production Hardening

## 12. 下一阶段

**Stage 10-1：RAG Evaluation Framework。** 在继续改变 Retrieval、Chunk、Reranker 等能力之前，建立可量化、可重复的 RAG baseline。先审查现有固定 mock 用例和当前检索、回答契约，再确定评测数据、指标、运行方式与结果记录；以此作为后续优化前后的比较基线。此处是下一阶段目标，不代表评测框架已经实现。
