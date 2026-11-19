"""Text-to-speech providers and audio coordination."""
from .base import BaseTTSProvider
from .edge_tts_provider import EdgeTTSProvider
from .factory import AudioNarrationCoordinator, get_tts_provider
from .models import TTSAudioResult, TTSChunkResult, TTSVoice, WordTimestamp
from .pyttsx3_provider import PyTTSx3OfflineProvider
from .remote_provider import RemoteServerTTSProvider

__all__ = [
    "AudioNarrationCoordinator",
    "BaseTTSProvider",
    "EdgeTTSProvider",
    "PyTTSx3OfflineProvider",
    "RemoteServerTTSProvider",
    "TTSAudioResult",
    "TTSChunkResult",
    "TTSVoice",
    "WordTimestamp",
    "get_tts_provider",
]
