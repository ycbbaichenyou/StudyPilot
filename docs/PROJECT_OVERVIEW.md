# StudyPilot 项目概览

## 1. 项目背景

StudyPilot 是一个面向个人学习资料的 RAG 知识库问答系统。它将分散在 PDF、DOCX、TXT 和 Markdown 文件中的学习内容整理为可检索的知识库，并基于检索到的原始资料生成带引用的回答，帮助用户快速定位、理解和复习自己的学习材料。

项目同时面向 AI 应用开发学习：核心数据流以清晰、可测试的代码显式实现，便于理解一套完整 RAG 应用如何连接文档处理、向量检索、上下文组装、模型调用和引用校验。

## 2. 项目目标

StudyPilot v1.0 支持完整的单轮知识库问答流程：

- 文档上传
- 文档解析
- Chunk 文本分块
- Embedding 向量生成
- 向量检索
- Context Assembly 上下文组装
- LLM Answer 回答生成
- Citation Validation 引用校验
- RAG Debug 调试与评测观察

## 3. 技术栈

### Frontend

- Vue 3
- Vite
- Native Fetch

### Backend

- FastAPI
- SQLAlchemy
- Alembic
- SQLite

### AI / RAG

- DashScope `text-embedding-v4`
- Qwen Plus
- Chroma
- 自研 Retrieval / Context / Citation Pipeline
- 不使用 LangChain 实现核心 RAG

## 4. 系统架构

```text
Vue 3 + Vite
      ↓
FastAPI
      ↓
Knowledge Base / Document Pipeline
      ↓
Parser
      ↓
Chunk
      ↓
Embedding
      ↓
Chroma
      ↓
Retrieval
      ↓
Context Assembly
      ↓
Prompt
      ↓
Qwen Plus
      ↓
Citation Validation
      ↓
Answer
```

Vue 工作台通过 FastAPI 调用知识库、文档处理和问答接口。SQLite 保存知识库、文档、内容、Chunk 及处理状态等结构化数据；Chroma 保存向量及检索所需元数据。检索阶段先以 SQLite 建立有效记录 allowlist，再执行 Chroma 向量搜索并回到 SQLite hydrate，从而维持数据边界和知识库隔离。

## 5. 核心亮点

- **核心 RAG Pipeline 自研**：解析、分块、向量化、检索、上下文组装、Prompt、模型调用和引用校验均以项目代码显式实现，未使用 LangChain 封装核心流程。
- **SQLite 与 Chroma 职责分离**：SQLite 是业务数据与生命周期状态的事实来源，Chroma 专注向量存储和相似度检索。
- **Generation-based embedding lifecycle**：使用 generation 标识向量版本，支持 stale、失败、重建索引和删除同步，避免旧向量进入当前检索结果。
- **Citation Validation**：从模型答案中提取实际引用，校验引用编号，只向客户端返回实际命中的引用，并显式标记有效、缺失或无效引用。
- **Knowledge Base 隔离**：检索 allowlist、数据 hydrate 和二次校验共同阻止跨知识库结果泄漏。
- **Retrieval / Context / Answer Debug**：前端可查看 top-k 检索结果、距离、来源位置、Context Block、字符预算、截断状态和回答引用指标。
- **前后端完整闭环**：从知识库创建、文档处理、索引生成到回答与引用展示，均可在 Vue 工作台中完成。
- **清晰的异常与空结果处理**：区分解析、向量化、模型和向量库错误，并为资料不足场景返回 `insufficient_context`，避免将正常空状态误报为系统异常。

## 6. 当前版本

当前冻结版本：`v1.0.0-rag-mvp`

该版本已形成可运行、可调试、可展示的单轮文档知识库问答 MVP。
