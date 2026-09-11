from fastapi import FastAPI


app = FastAPI(title="StudyPilot")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "app": "StudyPilot",
    }

