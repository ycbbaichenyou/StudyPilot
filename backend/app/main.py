from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from app.api.answers import router as answers_router
from app.api.documents import router as documents_router
from app.api.knowledge_bases import router as knowledge_bases_router
from app.database import engine, init_db


def create_app(
    *,
    initialize_database: bool = True,
    database_engine: Engine = engine,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if initialize_database:
            init_db(database_engine)
        yield

    application = FastAPI(title="StudyPilot", lifespan=lifespan)

    @application.get("/api/health")
    def health_check() -> dict[str, str]:
        return {
            "status": "ok",
            "app": "StudyPilot",
        }

    application.include_router(knowledge_bases_router)
    application.include_router(documents_router)
    application.include_router(answers_router)
    return application


app = create_app()
