import React, { useEffect, useRef, useState } from 'react';
import { createLipSyncAvatar } from './lipsync/engine.js';
import { TutorState } from '../../services/voiceTutorService';

/**
 * LipSyncAvatar
 * Real-time audio lip-sync avatar powered by HTML5 Canvas and Web Audio API.
 * Automatically synchronizes mouth shapes and natural eye blinking to the
 * active audio element (window._skillbridgeCurrentAudio) or voice synthesis.
 */
export default function LipSyncAvatar({
  gender = 'female',
  isSpeaking = false,
  tutorVoiceState = TutorState.IDLE,
  onClick = () => {},
  tutorName = 'ARIA',
  wakeWord = 'Hey Aria',
  className = '',
  audioElement = null,
  voiceGender = null,
  onGenderChange = null
}) {
  const canvasRef = useRef(null);
  const playerRef = useRef(null);
  const activeAudioRef = useRef(null);
  const [loadError, setLoadError] = useState(false);

  // Normalize persona/gender to 'female' or 'male'
  const currentGender = voiceGender || gender;
  const selectedGender = String(currentGender || 'female').toLowerCase().includes('male') && !String(currentGender || '').toLowerCase().includes('fe')
    ? 'male'
    : 'female';

  const assetsUrl = `/avatars/${selectedGender}`;

  // Initialize and mount LipSync Avatar engine
  useEffect(() => {
    let isMounted = true;
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Destroy existing player if any
    if (playerRef.current) {
      playerRef.current.destroy();
      playerRef.current = null;
    }

    const currentAudio = audioElement || window._skillbridgeCurrentAudio || null;

    createLipSyncAvatar(canvas, {
      assetsUrl,
      configUrl: '/config/lipsync.config.json',
      headMotion: false,
      source: currentAudio
    })
      .then((player) => {
        if (!isMounted) {
          player.destroy();
          return;
        }
        playerRef.current = player;
        activeAudioRef.current = currentAudio;
      })
      .catch((err) => {
        console.warn('[LipSyncAvatar] Failed to initialize lip-sync engine:', err);
        if (isMounted) setLoadError(true);
      });

    return () => {
      isMounted = false;
      if (playerRef.current) {
        playerRef.current.destroy();
        playerRef.current = null;
      }
    };
  }, [assetsUrl]);

  // Keep player attached to current playing audio element
  useEffect(() => {
    const targetAudio = audioElement || window._skillbridgeCurrentAudio;
    if (targetAudio && targetAudio !== activeAudioRef.current && playerRef.current) {
      activeAudioRef.current = targetAudio;
      playerRef.current.setSource(targetAudio);
    }

    const checkAudioSource = () => {
      const currentAudio = audioElement || window._skillbridgeCurrentAudio;
      if (currentAudio && currentAudio !== activeAudioRef.current && playerRef.current) {
        activeAudioRef.current = currentAudio;
        playerRef.current.setSource(currentAudio);
      }
    };

    const timer = setInterval(checkAudioSource, 250);
    return () => clearInterval(timer);
  }, [audioElement]);

  const isSpeakingActive = isSpeaking ||
    tutorVoiceState === TutorState.ARIA_SPEAKING ||
    tutorVoiceState === TutorState.ARIA_GREETING;

  // Sync manual speaking state for Web Speech synthesis
  useEffect(() => {
    if (playerRef.current) {
      playerRef.current.setManualSpeaking(isSpeakingActive);
    }
  }, [isSpeakingActive]);

  const isListeningActive = tutorVoiceState === TutorState.ACTIVE_LISTENING ||
    tutorVoiceState === TutorState.FOLLOW_UP_LISTENING;

  return (
    <div className={`w-full h-full flex flex-col items-center justify-between select-none ${className}`}>
      {/* Full-Height AI Tutor Character Stage */}
      <button
        type="button"
        onClick={onClick}
        title={`Click to talk with ${tutorName} (or say "${wakeWord}")`}
        style={{ backgroundColor: selectedGender === 'female' ? '#0e69c0' : '#0457a6' }}
        className={`relative w-full flex-1 min-h-[360px] sm:min-h-[440px] flex items-center justify-center rounded-2xl border-2 cursor-pointer transition-colors duration-200 group focus:outline-none overflow-hidden shadow-2xl ${
          isSpeakingActive
            ? 'border-[#ff8c21] shadow-[0_0_35px_rgba(245,130,32,0.4)] ring-4 ring-[#ff8c21]/20'
            : isListeningActive
              ? 'border-[#ff8c21] shadow-[0_0_30px_rgba(245,130,32,0.35)] ring-4 ring-[#ff8c21]/20'
              : 'border-[#ff8c21]/80 hover:border-[#ff8c21] shadow-xl'
        }`}
      >
        {/* Instant Base Avatar Image (aligned pixel-perfect to canvas, zero zoom jumping) */}
        <img
          src={`${assetsUrl}/base.png`}
          alt={tutorName}
          className="absolute inset-0 w-full h-full object-contain select-none pointer-events-none"
        />

        {/* Real-time HTML5 Canvas Lip-Sync Avatar — Complete Character View filling container */}
        <div className="absolute inset-0 w-full h-full flex items-center justify-center overflow-hidden pointer-events-none">
          <canvas
            ref={canvasRef}
            className="absolute inset-0 w-full h-full pointer-events-none select-none"
          />
        </div>

        {/* Fallback if avatar assets could not load */}
        {loadError && (
          <span className="absolute inset-0 flex items-center justify-center text-slate-300 select-none">
            <svg className="w-20 h-20 fill-current opacity-80" viewBox="0 0 24 24">
              <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />
            </svg>
          </span>
        )}

        {/* Active Speaking Waveform Overlay (sleek bottom badge) */}
        {isSpeakingActive && (
          <div className="absolute bottom-2 left-3 right-3 bg-slate-900/90 backdrop-blur-md border border-[#ff8c21]/50 rounded-xl px-3 py-1 flex items-center justify-between text-[#ff8c21] text-[11px] font-bold shadow-lg animate-fadeIn pointer-events-none z-10">
            <span className="flex items-center gap-1.5 truncate">
              <span className="h-2 w-2 rounded-full bg-[#ff8c21] animate-ping shrink-0" />
              <span className="truncate">{tutorName} is speaking...</span>
            </span>
            <div className="flex items-end gap-1 h-3 shrink-0">
              <span className="w-1 bg-[#ff8c21] h-3 animate-pulse rounded-full" />
              <span className="w-1 bg-[#ff8c21] h-2 animate-bounce rounded-full" />
              <span className="w-1 bg-[#ff8c21] h-3.5 animate-pulse rounded-full" />
              <span className="w-1 bg-[#ff8c21] h-1.5 animate-bounce rounded-full" />
            </div>
          </div>
        )}

        {/* Active Listening Waveform Overlay (sleek bottom badge, never covering the face!) */}
        {isListeningActive && (
          <div className="absolute bottom-2 left-3 right-3 bg-slate-900/90 backdrop-blur-md border border-[#ff8c21]/50 rounded-xl px-3 py-1 flex items-center justify-between text-[#ff8c21] text-[11px] font-bold shadow-lg animate-fadeIn pointer-events-none z-10">
            <span className="flex items-center gap-1.5 truncate">
              <span className="h-2 w-2 rounded-full bg-[#ff8c21] animate-ping shrink-0" />
              <span className="truncate">{tutorName} is listening...</span>
            </span>
            <div className="flex items-end gap-1 h-3 shrink-0">
              <span className="w-1 bg-[#ff8c21] h-2 animate-pulse rounded-full" />
              <span className="w-1 bg-[#ff8c21] h-3 animate-pulse rounded-full" />
              <span className="w-1 bg-[#ff8c21] h-1.5 animate-pulse rounded-full" />
            </div>
          </div>
        )}
      </button>

      {/* TUTOR Pill Badge (Admin-Configured AI Tutor) */}
      <div className="mt-2.5 w-full flex flex-col items-center select-none shrink-0">
        <button
          type="button"
          onClick={onClick}
          className="px-5 py-1 rounded-full bg-[#131d2e] border border-[#2a3952] hover:border-[#ff8c21]/60 shadow-sm cursor-pointer transition flex items-center gap-2"
        >
          <span className="h-2 w-2 rounded-full bg-[#ff8c21] animate-pulse" />
          <span className="text-xs sm:text-[13px] font-extrabold text-[#ff8c21] tracking-wider uppercase">
            {tutorName} AI TUTOR
          </span>
        </button>
      </div>
    </div>
  );
}
