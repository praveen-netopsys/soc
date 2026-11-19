import asyncio
import io
import os
import hashlib
try:
    import edge_tts
except ImportError:
    edge_tts = None
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import Response, StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

router = APIRouter()

# Global directory for caching synthesized neural audio
TTS_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tts_cache")
os.makedirs(TTS_CACHE_DIR, exist_ok=True)

VOICE_MAP = {
    # 6 Target Course Languages per SkillBridge Specification
    "en": "en-US-ChristopherNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ms": "ms-MY-OsmanNeural",
    "ta": "ta-IN-ValluvarNeural",
    "te": "te-IN-MohanNeural",
    "bn": "bn-IN-BashkarNeural",

    # Upper case language codes
    "EN": "en-US-ChristopherNeural",
    "ZH": "zh-CN-XiaoxiaoNeural",
    "MS": "ms-MY-OsmanNeural",
    "TA": "ta-IN-ValluvarNeural",
    "TE": "te-IN-MohanNeural",
    "BN": "bn-IN-BashkarNeural",

    # Full language names
    "ENGLISH": "en-US-ChristopherNeural",
    "CHINESE": "zh-CN-XiaoxiaoNeural",
    "MALAY": "ms-MY-OsmanNeural",
    "TAMIL": "ta-IN-ValluvarNeural",
    "TELUGU": "te-IN-MohanNeural",
    "BANGLA": "bn-IN-BashkarNeural",
    "BENGALI": "bn-IN-BashkarNeural",

    # Backward compatibility / Gender options
    "FEMALE_JENNY": "en-US-JennyNeural",
    "FEMALE_AVA": "en-US-AvaNeural",
    "MALE_GUY": "en-US-GuyNeural",
    "MALE_CHRISTOPHER": "en-US-ChristopherNeural",
    "FEMALE": "en-US-JennyNeural",
    "MALE": "en-US-ChristopherNeural",
}

class TtsRequest(BaseModel):
    text: str
    voice: Optional[str] = "en-US-ChristopherNeural"
    gender: Optional[str] = "MALE"
    rate: Optional[float] = 1.0
    pitch: Optional[float] = 1.0
    language: Optional[str] = "en"

class BatchSlideAudioRequest(BaseModel):
    course_id: str
    language: str
    slides: List[Dict[str, Any]] # [{ slideNumber: 1, text: "..." }]


def resolve_voice(voice_name: Optional[str], gender: Optional[str] = None, language: Optional[str] = None) -> str:
    # 1. Check language code first if provided
    if language:
        lang_key = language.strip().lower()
        if lang_key in VOICE_MAP:
            return VOICE_MAP[lang_key]

    if voice_name:
        v_upper = voice_name.strip()
        if v_upper in VOICE_MAP:
            return VOICE_MAP[v_upper]
        if v_upper.upper() in VOICE_MAP:
            return VOICE_MAP[v_upper.upper()]
        if "neural" in voice_name.lower():
            return voice_name
        if "male" in v_upper.lower():
            return "en-US-ChristopherNeural"
        if "female" in v_upper.lower():
            return "en-US-JennyNeural"

    g_upper = (gender or "MALE").upper().strip()
    return "en-US-ChristopherNeural" if g_upper == "MALE" else "en-US-JennyNeural"


def format_rate(rate: float) -> str:
    pct = int(round((rate - 1.0) * 100))
    return f"{pct:+d}%"


def format_pitch(pitch: float) -> str:
    hz = int(round((pitch - 1.0) * 50))
    return f"{hz:+d}Hz"


def get_cache_path(voice: str, text: str, rate_str: str, pitch_str: str) -> str:
    key = f"{voice}|{rate_str}|{pitch_str}|{text.strip()}".encode("utf-8")
    hash_digest = hashlib.sha256(key).hexdigest()
    return os.path.join(TTS_CACHE_DIR, f"{hash_digest}.mp3")


async def synthesize_speech(text: str, voice: str, rate_str: str, pitch_str: str) -> bytes:
    clean_text = text.strip()
    if not clean_text:
        return b""

    cache_file = get_cache_path(voice, clean_text, rate_str, pitch_str)
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 100:
        with open(cache_file, "rb") as f:
            return f.read()

    communicate = edge_tts.Communicate(
        text=clean_text,
        voice=voice,
        rate=rate_str,
        pitch=pitch_str
    )

    audio_stream = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_stream.write(chunk["data"])

    audio_bytes = audio_stream.getvalue()
    if audio_bytes:
        try:
            with open(cache_file, "wb") as f:
                f.write(audio_bytes)
        except Exception as cache_err:
            print(f"[TTS Cache] Warning writing cache: {cache_err}")

    return audio_bytes


@router.get("/speak")
async def speak_get(
    text: str = Query(..., description="Text to speak"),
    voice: Optional[str] = Query(None, description="Voice identifier"),
    gender: Optional[str] = Query("MALE", description="Voice gender (MALE or FEMALE)"),
    rate: Optional[float] = Query(1.0, description="Speech rate (0.8 - 1.3)"),
    pitch: Optional[float] = Query(1.0, description="Voice pitch (0.8 - 1.2)"),
    language: Optional[str] = Query(None, description="Language code (en, zh, ms, ta, te, bn)")
):
    clean_text = text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Text parameter cannot be empty.")

    selected_voice = resolve_voice(voice, gender, language)
    rate_str = format_rate(rate or 1.0)
    pitch_str = format_pitch(pitch or 1.0)

    try:
        audio_bytes = await synthesize_speech(clean_text, selected_voice, rate_str, pitch_str)
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="Failed to synthesize neural audio.")

        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline; filename=speech.mp3",
                "Cache-Control": "public, max-age=86400",
                "X-Voice-Used": selected_voice
            }
        )
    except Exception as e:
        print(f"[TTS] Synthesis error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS synthesis error: {str(e)}")


@router.post("/speak")
async def speak_post(req: TtsRequest):
    return await speak_get(
        text=req.text,
        voice=req.voice,
        gender=req.gender,
        rate=req.rate,
        pitch=req.pitch,
        language=req.language
    )


@router.get("/voices")
def list_voices():
    """Returns the official SkillBridge neural voice configuration for all 6 languages."""
    return {
        "languages": [
            {"code": "en", "name": "English", "voice": "en-US-ChristopherNeural"},
            {"code": "zh", "name": "Chinese (Simplified)", "voice": "zh-CN-XiaoxiaoNeural"},
            {"code": "ms", "name": "Malay", "voice": "ms-MY-OsmanNeural"},
            {"code": "ta", "name": "Tamil", "voice": "ta-IN-ValluvarNeural"},
            {"code": "te", "name": "Telugu", "voice": "te-IN-MohanNeural"},
            {"code": "bn", "name": "Bangla", "voice": "bn-IN-BashkarNeural"},
        ]
    }
