# StudyPilot

StudyPilot 是一个供本科生学习和实践 AI 应用开发的项目。

当前仓库处于 V1 Stage 0，只提供可启动的 FastAPI 后端、Vue 3 前端和健康检查接口。尚未包含数据库、RAG、Embedding、Chroma、LLM 或 Agent 功能。

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
- API 文档：`http://127.0.0.1:8000/docs`

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
