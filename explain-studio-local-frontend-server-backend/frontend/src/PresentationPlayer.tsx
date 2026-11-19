import { useEffect, useRef, useState } from 'react';
import {
  Play,
  Pause,
  RotateCcw,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  Maximize2,
  Minimize2,
  BookOpen,
  Globe,
  Layers,
  Sparkles,
  ZoomIn,
  Subtitles,
  Layout,
  FileText,
  X,
} from 'lucide-react';
import {
  getRenderedSlideUrl,
  getSlideAudioUrl,
  type MultilingualCourse,
  type CourseModule,
  type SlideExplanation,
  type SupportedLanguage,
} from './api';

export type PresentationPlayerProps = {
  jobId: string;
  course: MultilingualCourse;
  languages: SupportedLanguage[];
  initialLanguage?: string;
  initialVoiceGender?: 'male' | 'female';
};

export function PresentationPlayer({
  jobId,
  course,
  languages,
  initialLanguage = 'en',
  initialVoiceGender,
}: PresentationPlayerProps) {
  const [selectedLanguage, setSelectedLanguage] = useState<string>(initialLanguage);
  const [activeModuleIndex, setActiveModuleIndex] = useState<number>(0);
  const [activeSlideIndex, setActiveSlideIndex] = useState<number>(0);

  // Playback state
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [duration, setDuration] = useState<number>(0);
  const [playbackRate, setPlaybackRate] = useState<number>(1.0);
  const [volume, setVolume] = useState<number>(1.0);
  const [isMuted, setIsMuted] = useState<boolean>(false);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const [showExplanationText, setShowExplanationText] = useState<boolean>(true);

  // Dual-mode canvas: 'image' (Mode A: 1080p slide screen) vs 'card' (Mode B: interactive typography stage)
  const [renderMode, setRenderMode] = useState<'image' | 'card'>('image');
  const [slideImageLoadError, setSlideImageLoadError] = useState<boolean>(false);
  const [lightboxOpen, setLightboxOpen] = useState<boolean>(false);
  const [lightboxZoom, setLightboxZoom] = useState<number>(1.0);
  const [showCC, setShowCC] = useState<boolean>(true);
  
  // Voice gender defaults to Admin settings (SkillBridge default is 'female')
  const [voiceGender, setVoiceGender] = useState<'male' | 'female'>(() => {
    if (initialVoiceGender) return initialVoiceGender;
    const courseGender = (course as any)?.aiVoiceGender || (course as any)?.ai_voice_gender || (course as any)?.company?.aiVoiceGender;
    if (courseGender) {
      return String(courseGender).toLowerCase() === 'male' ? 'male' : 'female';
    }
    try {
      const adminSession = JSON.parse(sessionStorage.getItem('skillbridge_admin') || localStorage.getItem('skillbridge_admin') || 'null');
      if (adminSession?.aiVoiceGender) {
        return String(adminSession.aiVoiceGender).toLowerCase() === 'male' ? 'male' : 'female';
      }
    } catch (_) {}
    return 'female';
  });

  const isPlayingRef = useRef<boolean>(isPlaying);
  useEffect(() => {
    isPlayingRef.current = isPlaying;
  }, [isPlaying]);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const preloadAudioRef = useRef<HTMLAudioElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const activeModule: CourseModule | undefined = course.modules[activeModuleIndex];
  const activeSlide: SlideExplanation | undefined = activeModule?.slides[activeSlideIndex];

  // Next slide for preloading
  const nextSlide: SlideExplanation | undefined =
    activeSlideIndex < (activeModule?.slides.length || 0) - 1
      ? activeModule?.slides[activeSlideIndex + 1]
      : course.modules[activeModuleIndex + 1]?.slides[0];

  const currentAudioUrl =
    activeSlide && jobId
      ? getSlideAudioUrl(jobId, selectedLanguage, activeSlide.slide_number, voiceGender)
      : '';

  const nextAudioUrl =
    nextSlide && jobId
      ? getSlideAudioUrl(jobId, selectedLanguage, nextSlide.slide_number, voiceGender)
      : '';

  const currentSlideImageUrl =
    activeSlide && jobId
      ? getRenderedSlideUrl(jobId, activeSlide.slide_number)
      : '';

  // Synchronize audio playback speed and volume
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.playbackRate = playbackRate;
      audioRef.current.volume = isMuted ? 0 : volume;
    }
  }, [playbackRate, volume, isMuted]);

  // Handle voice gender change with seamless audio handover (no playback dropout or abort)
  const handleVoiceGenderChange = (newGender: 'male' | 'female') => {
    if (newGender === voiceGender) return;
    const wasPlaying = isPlayingRef.current;
    const audio = audioRef.current;
    if (audio) {
      try { audio.pause(); } catch (_) {}
    }
    setVoiceGender(newGender);
    if (audio && activeSlide && jobId) {
      const newUrl = getSlideAudioUrl(jobId, selectedLanguage, activeSlide.slide_number, newGender) + `&t=${Date.now()}`;
      audio.src = newUrl;
      audio.load();
      if (wasPlaying) {
        setIsPlaying(true);
        const playPromise = audio.play();
        if (playPromise !== undefined) {
          playPromise.catch((err) => {
            console.debug('Gender switch playback catch, resuming on canplay:', err);
            const onCanPlay = () => {
              audio.removeEventListener('canplay', onCanPlay);
              if (isPlayingRef.current) {
                audio.play().catch(() => {});
              }
            };
            audio.addEventListener('canplay', onCanPlay);
          });
        }
      }
    }
  };

  // Handle language switch with seamless audio handover
  const handleLanguageChange = (newLang: string) => {
    if (newLang === selectedLanguage) return;
    const wasPlaying = isPlayingRef.current;
    const audio = audioRef.current;
    if (audio) {
      try { audio.pause(); } catch (_) {}
    }
    setSelectedLanguage(newLang);
    if (audio && activeSlide && jobId) {
      const newUrl = getSlideAudioUrl(jobId, newLang, activeSlide.slide_number, voiceGender) + `&t=${Date.now()}`;
      audio.src = newUrl;
      audio.load();
      if (wasPlaying) {
        setIsPlaying(true);
        const playPromise = audio.play();
        if (playPromise !== undefined) {
          playPromise.catch((err) => {
            console.debug('Language switch playback catch, resuming on canplay:', err);
            const onCanPlay = () => {
              audio.removeEventListener('canplay', onCanPlay);
              if (isPlayingRef.current) {
                audio.play().catch(() => {});
              }
            };
            audio.addEventListener('canplay', onCanPlay);
          });
        }
      }
    }
  };

  // When active slide or module changes, reload audio and maintain play state
  useEffect(() => {
    const audio = audioRef.current;
    setSlideImageLoadError(false);
    setLightboxOpen(false);

    if (!audio || !currentAudioUrl) return;

    setCurrentTime(0);
    audio.load();

    if (isPlaying) {
      const playPromise = audio.play();
      if (playPromise !== undefined) {
        playPromise.catch((err) => {
          console.debug('Autoplay prevented or interrupted:', err);
          const onCanPlay = () => {
            audio.removeEventListener('canplay', onCanPlay);
            if (isPlayingRef.current) {
              audio.play().catch(() => {});
            }
          };
          audio.addEventListener('canplay', onCanPlay);
        });
      }
    }
  }, [activeModuleIndex, activeSlideIndex]);

  // Audio event handlers
  const handleTimeUpdate = () => {
    if (audioRef.current) {
      setCurrentTime(audioRef.current.currentTime);
      if (audioRef.current.duration && !isNaN(audioRef.current.duration)) {
        setDuration(audioRef.current.duration);
      }
    }
  };

  const handleLoadedMetadata = () => {
    if (audioRef.current) {
      const d = audioRef.current.duration;
      if (d && !isNaN(d)) {
        setDuration(d);
      }
    }
  };

  const handleAudioEnded = () => {
    // Check if next slide in this module exists
    if (activeModule && activeSlideIndex < activeModule.slides.length - 1) {
      setActiveSlideIndex((prev) => prev + 1);
    } else if (activeModuleIndex < course.modules.length - 1) {
      // Auto-advance to next module
      setActiveModuleIndex((prev) => prev + 1);
      setActiveSlideIndex(0);
    } else {
      // Finished entire course!
      setIsPlaying(false);
      setCurrentTime(0);
    }
  };

  const togglePlayPause = () => {
    const audio = audioRef.current;
    if (!audio) return;

    if (isPlaying) {
      audio.pause();
      setIsPlaying(false);
    } else {
      audio
        .play()
        .then(() => setIsPlaying(true))
        .catch((err) => {
          console.warn('Playback error:', err);
          setIsPlaying(false);
        });
    }
  };

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newTime = parseFloat(e.target.value);
    setCurrentTime(newTime);
    if (audioRef.current) {
      audioRef.current.currentTime = newTime;
    }
  };

  const goToPreviousSlide = () => {
    if (activeSlideIndex > 0) {
      setActiveSlideIndex((prev) => prev - 1);
    } else if (activeModuleIndex > 0) {
      const prevMod = course.modules[activeModuleIndex - 1];
      setActiveModuleIndex((prev) => prev - 1);
      setActiveSlideIndex(prevMod.slides.length - 1);
    }
  };

  const goToNextSlide = () => {
    if (activeModule && activeSlideIndex < activeModule.slides.length - 1) {
      setActiveSlideIndex((prev) => prev + 1);
    } else if (activeModuleIndex < course.modules.length - 1) {
      setActiveModuleIndex((prev) => prev + 1);
      setActiveSlideIndex(0);
    }
  };

  const replaySlide = () => {
    if (audioRef.current) {
      audioRef.current.currentTime = 0;
      setCurrentTime(0);
      audioRef.current.play().then(() => setIsPlaying(true));
    }
  };

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current
        .requestFullscreen()
        .then(() => setIsFullscreen(true))
        .catch((err) => console.warn('Could not enter fullscreen:', err));
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false));
    }
  };

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }
      if (e.code === 'Space') {
        e.preventDefault();
        togglePlayPause();
      } else if (e.code === 'ArrowRight' || e.code === 'PageDown') {
        e.preventDefault();
        goToNextSlide();
      } else if (e.code === 'ArrowLeft' || e.code === 'PageUp') {
        e.preventDefault();
        goToPreviousSlide();
      } else if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        setShowCC((v) => !v);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  });

  const formatTime = (secs: number) => {
    if (isNaN(secs) || secs < 0) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const currentLanguageMeta = languages.find((l) => l.code === selectedLanguage);
  const currentExplanation = activeSlide?.explanations?.[selectedLanguage] || activeSlide?.explanations?.['en'] || '';

  // Synchronized sentence caption calculation
  const sentences = currentExplanation
    ? currentExplanation.split(/(?<=[.!?])\s+/).filter(Boolean)
    : [];
  const currentSentenceIdx =
    duration > 0 && sentences.length > 0
      ? Math.min(sentences.length - 1, Math.floor((currentTime / duration) * sentences.length))
      : 0;
  const currentSentence = sentences[currentSentenceIdx] || currentExplanation;

  // Determine active presentation mode
  const effectiveMode = slideImageLoadError ? 'card' : renderMode;

  return (
    <div
      ref={containerRef}
      className={`presentation-player-root ${isFullscreen ? 'fullscreen' : ''}`}
      style={{
        background: '#090d16',
        color: '#f8fafc',
        borderRadius: isFullscreen ? 0 : 12,
        overflow: 'hidden',
        boxShadow: '0 20px 40px -15px rgba(0,0,0,0.5)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* ── Top Bar: Language Selector & Mode Switcher ── */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          padding: '12px 20px',
          background: '#131b2e',
          borderBottom: '1px solid #1e293b',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Layers size={18} style={{ color: '#6366f1' }} />
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#f8fafc' }}>
              {activeModule?.title || course.title}
            </div>
            <div style={{ fontSize: 11, color: '#94a3b8' }}>
              Module {activeModuleIndex + 1} of {course.modules.length} · Slide {activeSlide?.slide_number} of {course.total_slides}
            </div>
          </div>
        </div>

        {/* Right Action Controls: Mode Switcher & Languages */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {/* Dual-Mode Canvas Switcher: Mode A (Image) vs Mode B (Interactive Card) */}
          <button
            onClick={() => setRenderMode(renderMode === 'image' ? 'card' : 'image')}
            title="Switch between 1080p Slide View and Interactive Card View"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 12px',
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 600,
              background: renderMode === 'card' ? '#312e81' : '#1e293b',
              border: renderMode === 'card' ? '1px solid #6366f1' : '1px solid #334155',
              color: renderMode === 'card' ? '#a5b4fc' : '#cbd5e1',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}
          >
            {renderMode === 'image' ? <FileText size={14} /> : <Layout size={14} />}
            <span>{renderMode === 'image' ? 'Card View' : 'Slide View'}</span>
          </button>

          {/* Voice Gender Switcher: Male vs Female (No emojis/PFP, clean UI) */}
          <div
            style={{
              display: 'inline-flex',
              background: '#0f172a',
              padding: 2,
              borderRadius: 20,
              border: '1px solid #334155',
            }}
          >
            <button
              type="button"
              onClick={() => handleVoiceGenderChange('male')}
              style={{
                padding: '4px 12px',
                borderRadius: 16,
                fontSize: 12,
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                background: voiceGender === 'male' ? '#4f46e5' : 'transparent',
                color: voiceGender === 'male' ? '#ffffff' : '#94a3b8',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                transition: 'all 0.15s ease',
              }}
              title="Switch to Male Narrator Voice"
            >
              <span>Male</span>
            </button>
            <button
              type="button"
              onClick={() => handleVoiceGenderChange('female')}
              style={{
                padding: '4px 12px',
                borderRadius: 16,
                fontSize: 12,
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                background: voiceGender === 'female' ? '#ec4899' : 'transparent',
                color: voiceGender === 'female' ? '#ffffff' : '#94a3b8',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                transition: 'all 0.15s ease',
              }}
              title="Switch to Female Narrator Voice"
            >
              <span>Female</span>
            </button>
          </div>

          {/* 6 Native Language Selector Pills */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: '#94a3b8', marginRight: 2, display: 'flex', alignItems: 'center', gap: 4 }}>
              <Globe size={14} />
            </span>
            {languages.map((lang) => {
              const isReady = activeModule?.module_ready?.[lang.code] !== false;
              return (
                <button
                  key={lang.code}
                  type="button"
                  onClick={() => handleLanguageChange(lang.code)}
                  style={{
                    padding: '5px 12px',
                    borderRadius: 20,
                    fontSize: 12,
                    fontWeight: 600,
                    cursor: 'pointer',
                    border: selectedLanguage === lang.code ? '1px solid #6366f1' : '1px solid #334155',
                    background: selectedLanguage === lang.code ? '#4f46e5' : '#1e293b',
                    color: selectedLanguage === lang.code ? '#ffffff' : '#cbd5e1',
                    transition: 'all 0.15s ease',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                  }}
                >
                  <span>{lang.native_name}</span>
                  {isReady && <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#10b981' }} />}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* ── Main Presentation Canvas (Dual-Mode: 1080p Image vs Interactive Card) ── */}
      <div
        style={{
          position: 'relative',
          width: '100%',
          aspectRatio: '16 / 9',
          background: '#020617',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden',
        }}
      >
        {effectiveMode === 'image' && currentSlideImageUrl ? (
          /* Mode A: Clean 1080p Slide Screen with Click-to-Zoom */
          <div
            onClick={() => setLightboxOpen(true)}
            title="Click to open full-resolution Lightbox zoom"
            style={{
              position: 'relative',
              width: '100%',
              height: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'zoom-in',
            }}
          >
            <img
              key={`${activeSlide?.slide_number}-${currentSlideImageUrl}`}
              src={currentSlideImageUrl}
              alt={activeSlide?.title || `Slide ${activeSlide?.slide_number}`}
              onError={() => {
                console.warn('Slide image load error; automatically falling back to Mode B card stage');
                setSlideImageLoadError(true);
              }}
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                display: 'block',
                transition: 'opacity 0.2s ease',
              }}
            />

            {/* Hover Zoom Indicator */}
            <div
              style={{
                position: 'absolute',
                top: 16,
                right: 20,
                background: 'rgba(15, 23, 42, 0.75)',
                backdropFilter: 'blur(8px)',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                padding: '6px 10px',
                borderRadius: 8,
                fontSize: 11,
                color: '#94a3b8',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <ZoomIn size={14} />
              <span>Click to Zoom</span>
            </div>
          </div>
        ) : (
          /* Mode B: Interactive Typography Stage (100% Free, Zero License Watermarks, Styled Bullets) */
          <div
            style={{
              width: '100%',
              height: '100%',
              background: 'linear-gradient(135deg, #0b1329 0%, #101b3d 50%, #070d1e 100%)',
              padding: '30px 40px',
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'space-between',
              overflowY: 'auto',
              color: '#ffffff',
            }}
          >
            {/* Slide Stage Header */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ background: 'rgba(99, 102, 241, 0.2)', color: '#818cf8', border: '1px solid rgba(99, 102, 241, 0.4)', borderRadius: 8, padding: '6px 8px', fontSize: 13, fontWeight: 700, display: 'inline-flex', alignItems: 'center' }}>
                  <Layout size={14} />
                </span>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: '#f59e0b', textTransform: 'uppercase', letterSpacing: 0.8 }}>
                    PowerPoint Presentation · Slide {activeSlide?.slide_number} of {course.total_slides}
                  </div>
                  <h2 style={{ fontSize: 20, fontWeight: 800, color: '#ffffff', margin: 0, marginTop: 2 }}>
                    {activeSlide?.title}
                  </h2>
                </div>
              </div>
              <span style={{ fontSize: 11, fontWeight: 600, color: '#94a3b8', background: 'rgba(30, 41, 59, 0.8)', padding: '4px 10px', borderRadius: 6, border: '1px solid rgba(51, 65, 85, 0.8)' }}>
                {activeModule?.title}
              </span>
            </div>

            {/* Slide Body: Structured Bullet Points with Accent Pointers */}
            <div style={{ flex: 1, margin: '16px 0', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {activeSlide?.key_points && activeSlide.key_points.length > 0 ? (
                activeSlide.key_points.map((pt, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 12,
                      background: 'rgba(30, 41, 59, 0.45)',
                      border: '1px solid rgba(51, 65, 85, 0.6)',
                      borderRadius: 10,
                      padding: '12px 16px',
                      fontSize: 14,
                      color: '#e2e8f0',
                      lineHeight: 1.5,
                    }}
                  >
                    <span style={{ color: '#f59e0b', fontWeight: 800, fontSize: 16, lineHeight: 1 }}>▸</span>
                    <span style={{ flex: 1 }}>{pt.replace(/^[-*•]\s*/, '')}</span>
                  </div>
                ))
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#94a3b8', fontStyle: 'italic' }}>
                  {activeSlide?.title || 'Interactive Slide Concept'}
                </div>
              )}

              {/* Table Summaries if present */}
              {activeSlide?.table_summaries && activeSlide.table_summaries.length > 0 && (
                <div style={{ background: 'rgba(15, 23, 42, 0.7)', borderRadius: 8, padding: '10px 14px', border: '1px solid rgba(51, 65, 85, 0.6)', fontSize: 12, color: '#94a3b8' }}>
                  <strong style={{ color: '#a5b4fc' }}>Table Summary:</strong> {activeSlide.table_summaries.join(' · ')}
                </div>
              )}
            </div>

            {/* Slide Stage Footer: Pedagogical Guidance Notice */}
            <div
              style={{
                background: 'rgba(245, 158, 11, 0.12)',
                border: '1px solid rgba(245, 158, 11, 0.35)',
                borderRadius: 8,
                padding: '8px 14px',
                fontSize: 12,
                color: '#fef3c7',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <span>⚠️</span>
              <span><strong>Operational Standard:</strong> Ensure you master each concept thoroughly before carrying out related procedures.</span>
            </div>
          </div>
        )}

        {/* Floating Slide Badge */}
        <div
          style={{
            position: 'absolute',
            bottom: 16,
            left: 20,
            background: 'rgba(15, 23, 42, 0.85)',
            backdropFilter: 'blur(8px)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            padding: '6px 14px',
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            color: '#f8fafc',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <BookOpen size={14} style={{ color: '#38bdf8' }} />
          <span>{activeSlide?.title}</span>
        </div>

        {/* Audio Elements */}
        <audio
          ref={audioRef}
          crossOrigin="anonymous"
          src={currentAudioUrl}
          onTimeUpdate={handleTimeUpdate}
          onLoadedMetadata={handleLoadedMetadata}
          onEnded={handleAudioEnded}
          onError={(e) => {
            console.debug('Audio playback element notice:', e);
          }}
        />
        {/* Audio Preloader for Zero-Latency Next Slide */}
        {nextAudioUrl && <audio ref={preloadAudioRef} src={nextAudioUrl} preload="auto" />}
      </div>

      {/* ── Synchronized Subtitle Caption Ribbon (CC) ── */}
      {showCC && currentSentence && (
        <div
          style={{
            background: 'rgba(4, 8, 20, 0.94)',
            backdropFilter: 'blur(8px)',
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
            padding: '8px 24px',
            textAlign: 'center',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 10,
            minHeight: 38,
          }}
        >
          <span style={{ fontSize: 10, fontWeight: 800, color: '#f59e0b', background: 'rgba(245, 158, 11, 0.2)', padding: '2px 6px', borderRadius: 4, letterSpacing: 0.5 }}>
            CC
          </span>
          <span style={{ fontSize: 13, color: '#f8fafc', fontStyle: 'italic', fontWeight: 500, lineHeight: 1.4 }}>
            "{currentSentence}"
          </span>
        </div>
      )}

      {/* ── Bottom Controls & Progress Bar ── */}
      <div style={{ background: '#0f172a', borderTop: '1px solid #1e293b', padding: '14px 20px' }}>
        {/* Scrubbable Slide Progress Slider */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <span style={{ fontSize: 12, color: '#94a3b8', minWidth: 38, fontFamily: 'monospace' }}>
            {formatTime(currentTime)}
          </span>
          <input
            type="range"
            min={0}
            max={duration || 100}
            step={0.1}
            value={currentTime}
            onChange={handleSeek}
            style={{
              flex: 1,
              height: 5,
              borderRadius: 3,
              appearance: 'none',
              background: `linear-gradient(to right, #6366f1 ${(currentTime / (duration || 1)) * 100}%, #334155 ${(currentTime / (duration || 1)) * 100}%)`,
              cursor: 'pointer',
            }}
          />
          <span style={{ fontSize: 12, color: '#94a3b8', minWidth: 38, fontFamily: 'monospace' }}>
            {formatTime(duration)}
          </span>
        </div>

        {/* Player Controls Buttons Row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
          {/* Left: Previous, Play/Pause, Next, Replay & AI Tutor Co-Pilot */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              onClick={goToPreviousSlide}
              title="Previous Slide"
              style={{
                background: '#1e293b',
                border: '1px solid #334155',
                color: '#f8fafc',
                borderRadius: 8,
                padding: '8px 12px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <SkipBack size={16} />
            </button>

            <button
              onClick={togglePlayPause}
              style={{
                background: '#4f46e5',
                border: 'none',
                color: '#ffffff',
                borderRadius: 8,
                padding: '8px 18px',
                fontSize: 13,
                fontWeight: 700,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                boxShadow: '0 4px 12px rgba(79, 70, 229, 0.4)',
              }}
            >
              {isPlaying ? <Pause size={17} fill="#fff" /> : <Play size={17} fill="#fff" />}
              <span>{isPlaying ? 'PAUSE' : 'PLAY'}</span>
            </button>

            <button
              onClick={goToNextSlide}
              title="Next Slide"
              style={{
                background: '#1e293b',
                border: '1px solid #334155',
                color: '#f8fafc',
                borderRadius: 8,
                padding: '8px 12px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <SkipForward size={16} />
            </button>

            <button
              onClick={replaySlide}
              title="Replay Current Slide Narration"
              style={{
                background: '#1e293b',
                border: '1px solid #334155',
                color: '#94a3b8',
                borderRadius: 8,
                padding: '8px 10px',
                cursor: 'pointer',
              }}
            >
              <RotateCcw size={15} />
            </button>

            {/* AI Tutor Avatar Co-Pilot Indicator */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '4px 10px',
                background: '#1e293b',
                borderRadius: 20,
                border: '1px solid #334155',
                marginLeft: 4,
              }}
            >
              <div
                style={{
                  width: 20,
                  height: 20,
                  borderRadius: '50%',
                  background: isPlaying ? (voiceGender === 'female' ? '#ec4899' : '#f59e0b') : '#475569',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  boxShadow: isPlaying ? (voiceGender === 'female' ? '0 0 10px rgba(236, 72, 153, 0.6)' : '0 0 10px rgba(245, 158, 11, 0.6)') : 'none',
                  transition: 'all 0.3s ease',
                }}
              >
                <Volume2 size={11} style={{ color: '#ffffff' }} />
              </div>
              <span style={{ fontSize: 11, fontWeight: 600, color: isPlaying ? '#fef3c7' : '#94a3b8' }}>
                {isPlaying
                  ? `${voiceGender === 'female' ? 'Female' : 'Male'} Tutor Narrating`
                  : `${voiceGender === 'female' ? 'Female' : 'Male'} Voice Ready`}
              </span>
            </div>
          </div>

          {/* Center: Module Quick Selector */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, overflowX: 'auto', maxWidth: '35%' }}>
            {course.modules.map((m, idx) => {
              const isSelected = activeModuleIndex === idx;
              return (
                <button
                  key={m.module_id}
                  onClick={() => {
                    setActiveModuleIndex(idx);
                    setActiveSlideIndex(0);
                  }}
                  style={{
                    padding: '6px 12px',
                    borderRadius: 6,
                    fontSize: 11,
                    fontWeight: isSelected ? 700 : 500,
                    background: isSelected ? '#312e81' : '#1e293b',
                    border: isSelected ? '1px solid #6366f1' : '1px solid #334155',
                    color: isSelected ? '#a5b4fc' : '#94a3b8',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                  }}
                >
                  M{idx + 1}: {m.title.replace(/^Module \d+:\s*/i, '').slice(0, 20)}
                </button>
              );
            })}
          </div>

          {/* Right: Speed, Volume, CC Toggle, Fullscreen, Explanation Toggle */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {/* CC Caption Toggle */}
            <button
              onClick={() => setShowCC(!showCC)}
              title="Toggle Closed Captions (CC)"
              style={{
                background: showCC ? '#312e81' : '#1e293b',
                border: showCC ? '1px solid #6366f1' : '1px solid #334155',
                color: showCC ? '#f59e0b' : '#94a3b8',
                borderRadius: 6,
                padding: '6px 9px',
                fontSize: 11,
                fontWeight: 700,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <Subtitles size={14} />
              <span>CC</span>
            </button>

            {/* Speed Pill */}
            <select
              value={playbackRate}
              onChange={(e) => setPlaybackRate(parseFloat(e.target.value))}
              style={{
                background: '#1e293b',
                border: '1px solid #334155',
                color: '#f8fafc',
                borderRadius: 6,
                padding: '5px 8px',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              <option value={0.75}>0.75x</option>
              <option value={1.0}>1.0x</option>
              <option value={1.25}>1.25x</option>
              <option value={1.5}>1.5x</option>
              <option value={2.0}>2.0x</option>
            </select>

            {/* Volume / Mute with Slider */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <button
                onClick={() => setIsMuted(!isMuted)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: isMuted ? '#ef4444' : '#94a3b8',
                  cursor: 'pointer',
                  padding: 4,
                  display: 'flex',
                  alignItems: 'center',
                }}
              >
                {isMuted ? <VolumeX size={18} /> : <Volume2 size={18} />}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={isMuted ? 0 : volume}
                onChange={(e) => {
                  setVolume(parseFloat(e.target.value));
                  if (isMuted) setIsMuted(false);
                }}
                style={{ width: 55, height: 4, cursor: 'pointer' }}
                title="Volume"
              />
            </div>

            {/* Toggle Full Explanation Box */}
            <button
              onClick={() => setShowExplanationText(!showExplanationText)}
              title="Toggle Spoken Teaching Explanation"
              style={{
                background: showExplanationText ? '#334155' : 'transparent',
                border: '1px solid #334155',
                color: '#94a3b8',
                borderRadius: 6,
                padding: '5px 10px',
                fontSize: 11,
                cursor: 'pointer',
              }}
            >
              Script
            </button>

            {/* Fullscreen */}
            <button
              onClick={toggleFullscreen}
              title="Fullscreen"
              style={{
                background: 'transparent',
                border: 'none',
                color: '#94a3b8',
                cursor: 'pointer',
                padding: 4,
              }}
            >
              {isFullscreen ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
            </button>
          </div>
        </div>
      </div>

      {/* ── Spoken Teaching Explanation Box (Instructor Perspective) ── */}
      {showExplanationText && (
        <div
          style={{
            background: '#0d1322',
            padding: '16px 20px',
            borderTop: '1px solid #1e293b',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: '#6366f1', textTransform: 'uppercase', letterSpacing: 0.5, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Sparkles size={14} /> Instructor Spoken Explanation ({currentLanguageMeta?.native_name})
            </span>
            <span style={{ fontSize: 11, color: '#64748b' }}>
              Slide {activeSlide?.slide_number} of {course.total_slides}
            </span>
          </div>
          <div
            style={{
              fontSize: 14,
              lineHeight: 1.6,
              color: '#e2e8f0',
              fontStyle: 'normal',
              background: '#131b2e',
              padding: '12px 16px',
              borderRadius: 8,
              border: '1px solid #1e293b',
            }}
          >
            {currentExplanation || 'Generating narration explanation...'}
          </div>
        </div>
      )}

      {/* ── Module Slide Strip: Clickable Thumbnails ── */}
      {activeModule && (
        <div style={{ padding: '14px 20px', background: '#090d16', borderTop: '1px solid #1e293b' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', marginBottom: 8 }}>
            Module {activeModuleIndex + 1} Slides:
          </div>
          <div style={{ display: 'flex', gap: 10, overflowX: 'auto', paddingBottom: 6 }}>
            {activeModule.slides.map((s, idx) => {
              const isSelected = activeSlideIndex === idx;
              return (
                <div
                  key={s.slide_id}
                  onClick={() => setActiveSlideIndex(idx)}
                  style={{
                    flexShrink: 0,
                    width: 130,
                    borderRadius: 6,
                    border: isSelected ? '2px solid #6366f1' : '1px solid #334155',
                    background: '#1e293b',
                    overflow: 'hidden',
                    cursor: 'pointer',
                    boxShadow: isSelected ? '0 0 10px rgba(99, 102, 241, 0.5)' : 'none',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <img
                    src={getRenderedSlideUrl(jobId, s.slide_number)}
                    alt={s.title}
                    style={{ width: '100%', height: 74, objectFit: 'cover', display: 'block' }}
                  />
                  <div style={{ padding: '4px 6px', fontSize: 10, fontWeight: 600, color: isSelected ? '#a5b4fc' : '#cbd5e1', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {s.slide_number}. {s.title}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Fullscreen Lightbox Zoom Modal ── */}
      {lightboxOpen && (
        <div
          onClick={() => setLightboxOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 9999,
            background: 'rgba(0, 0, 0, 0.92)',
            backdropFilter: 'blur(10px)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'relative',
              maxWidth: '92vw',
              maxHeight: '90vh',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
            }}
          >
            <div style={{ position: 'absolute', top: -45, right: 0, display: 'flex', gap: 10, alignItems: 'center' }}>
              <button
                onClick={() => setLightboxZoom((z) => (z >= 2 ? 1 : z + 0.5))}
                style={{
                  background: '#1e293b',
                  border: '1px solid #334155',
                  color: '#fff',
                  borderRadius: 6,
                  padding: '5px 12px',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Zoom: {lightboxZoom}x
              </button>
              <button
                onClick={() => setLightboxOpen(false)}
                style={{
                  background: '#ef4444',
                  border: 'none',
                  color: '#fff',
                  borderRadius: 6,
                  padding: '5px 10px',
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                <X size={16} />
              </button>
            </div>
            <img
              src={currentSlideImageUrl}
              alt={activeSlide?.title}
              style={{
                maxWidth: '100%',
                maxHeight: '85vh',
                objectFit: 'contain',
                transform: `scale(${lightboxZoom})`,
                transition: 'transform 0.15s ease',
                borderRadius: 8,
                boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.7)',
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
