import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.face import router as face_router
from api.transcribe import router as transcribe_router
from api.tts import router as tts_router
from api.slides import router as slides_router
from services.face_service import get_face_service

app = FastAPI(
    title="SkillBridge AI Service",
    description="Python microservice for face biometrics, Whisper speech transcription, Neural TTS, and 1080p slide rendering",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(face_router)
app.include_router(transcribe_router)
app.include_router(tts_router, prefix="/api/tts", tags=["TTS"])
app.include_router(slides_router)

# Mount Explain Studio presentation and narration engine natively (workspace priority)
import sys
from pathlib import Path
_local_explain = Path(__file__).resolve().parent.parent / "explain-studio-local-frontend-server-backend" / "backend"
_candidates = [
    _local_explain,
    Path("C:/Users/pavankumar/Music/Skill-Bridge/explain-studio-local-frontend-server-backend/backend"),
]
for _p in _candidates:
    if _p.exists() and str(_p.resolve()) not in sys.path:
        sys.path.insert(0, str(_p.resolve()))
        break

try:
    from app.routers.jobs import router as jobs_router
    from app.routers.multilingual import router as multilingual_router
    from app.routers.ai import router as ai_router
    from app.db import init_db
    init_db()
    app.include_router(jobs_router)
    app.include_router(multilingual_router)
    app.include_router(ai_router)
    print("Explain Studio presentation and narration engine natively mounted into SkillBridge AI Service!")
except Exception as e:
    print(f"Explain Studio router mount warning: {e}")

@app.on_event("startup")
def startup_event():
    print("Warming up Face ID Models (MTCNN + FaceNet) during startup...")
    try:
        get_face_service()
        print("Model warming complete. Ready for biometric requests.")
    except Exception as e:
        print(f"CRITICAL: Failed to load Face ID Models: {str(e)}")

@app.get("/")
def read_root():
    return {"status": "online", "message": "SkillBridge AI Microservice is running."}

if __name__ == "__main__":
    import os
    from config.settings import settings
    port = int(os.environ.get("PORT", settings.PORT))
    # Bind to all interfaces (0.0.0.0) for containerization / network access
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info", reload=True)
