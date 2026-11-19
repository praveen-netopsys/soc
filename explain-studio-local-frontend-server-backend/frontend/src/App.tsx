import { useEffect, useRef, useState } from 'react';
import {
  Film,
  Presentation,
  Trash2,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  Play,
  Languages,
  Layers,
  FileCheck2,
  Server,
  Activity,
  HardDrive,
  Cpu,
  Volume2,
  RefreshCw,
  Copy,
  Check,
  Clock,
  X,
} from 'lucide-react';
import {
  checkBackendHealth,
  checkServicesHealth,
  createJob,
  getJobStatus,
  triggerGenerateAllLanguages,
  getJobCourse,
  type BackendHealth,
  type ServicesHealthResponse,
  type JobStatus,
  type MultilingualCourse,
  type SupportedLanguage,
} from './api';
import { PresentationPlayer } from './PresentationPlayer';

const DEFAULT_LANGUAGES: SupportedLanguage[] = [
  { code: 'en', name: 'English', native_name: 'English', default_voice: 'en-US-ChristopherNeural' },
  { code: 'zh-CN', name: 'Chinese (Simplified)', native_name: '中文', default_voice: 'zh-CN-XiaoxiaoNeural' },
  { code: 'ms', name: 'Malay', native_name: 'Bahasa Melayu', default_voice: 'ms-MY-OsmanNeural' },
  { code: 'ta', name: 'Tamil', native_name: 'தமிழ்', default_voice: 'ta-IN-ValluvarNeural' },
  { code: 'bn', name: 'Bangla', native_name: 'বাংলা', default_voice: 'bn-IN-BashkarNeural' },
  { code: 'te', name: 'Telugu', native_name: 'తెలుగు', default_voice: 'te-IN-MohanNeural' },
];

const SSH_TUNNEL_COMMAND =
  (import.meta as any).env?.VITE_SSH_TUNNEL_COMMAND ||
  'ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -L 127.0.0.1:11435:127.0.0.1:11434 intern@192.168.50.6';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function App() {
  const [backendState, setBackendState] = useState<'checking' | 'connected' | 'offline'>('checking');
  const [backendHealth, setBackendHealth] = useState<BackendHealth | null>(null);
  const [servicesHealth, setServicesHealth] = useState<ServicesHealthResponse | null>(null);
  const [showDiagnosticsModal, setShowDiagnosticsModal] = useState<boolean>(false);
  const [copiedTunnelCmd, setCopiedTunnelCmd] = useState<boolean>(false);

  // Upload & Job state
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | 'idle' | 'uploading'>('idle');
  const [stageMessage, setStageMessage] = useState<string>('');
  const [progressPct, setProgressPct] = useState<number>(0);
  const [etaSeconds, setEtaSeconds] = useState<number | null>(null);
  const [progressDetail, setProgressDetail] = useState<{
    eta_human?: string;
    total_slides?: number;
    slides_analyzed?: number;
    audio_synthesized?: number;
    total_audio?: number;
    videos_rendered?: number;
    total_videos?: number;
    elapsed_seconds?: number;
  } | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  // Multilingual course state
  const [course, setCourse] = useState<MultilingualCourse | null>(null);
  const [selectedLanguage] = useState<string>('en');

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // 1. Initial Health & Diagnostics Check
  const checkHealth = async () => {
    setBackendState('checking');
    try {
      const h = await checkBackendHealth();
      setBackendHealth(h);
      setBackendState('connected');
      // Also fetch multi-service diagnostic report
      try {
        const s = await checkServicesHealth();
        setServicesHealth(s);
      } catch {
        // Non-blocking
      }
    } catch {
      setBackendState('offline');
      setServicesHealth(null);
    }
  };

  useEffect(() => {
    checkHealth();
  }, []);

  // 2. Poll job & course status
  useEffect(() => {
    if (!jobId) return;
    if (jobStatus === 'completed' && course) return;

    const interval = setInterval(async () => {
      try {
        const statusRes = await getJobStatus(jobId);
        setJobStatus(statusRes.status);
        if (statusRes.stage) setStageMessage(statusRes.stage);
        if (statusRes.error) setErrorDetail(statusRes.error);
        if (statusRes.progress_pct !== undefined) setProgressPct(statusRes.progress_pct);
        if (statusRes.eta_seconds !== undefined) setEtaSeconds(statusRes.eta_seconds);

        if (statusRes.progress_detail) {
          try {
            const parsed = JSON.parse(statusRes.progress_detail);
            setProgressDetail(parsed);
          } catch {
            // ignore JSON parse
          }
        }

        // Check if course metadata is ready
        const courseRes = await getJobCourse(jobId);
        if (courseRes.has_course && courseRes.course) {
          setCourse(courseRes.course);
          if (courseRes.status === 'completed') {
            setJobStatus('completed');
            setProgressPct(100);
          }
        }
      } catch (err) {
        console.warn('Polling error:', err);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [jobId, jobStatus, course]);

  // 3. Handle File Selection and Upload
  const handleFileChange = async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pptx')) {
      setErrorDetail('Please select a valid PowerPoint presentation (.pptx).');
      return;
    }

    setSourceFile(file);
    setErrorDetail(null);
    setJobStatus('uploading');
    setStageMessage('Uploading presentation to Explain Studio...');

    try {
      const res = await createJob('', [file], {
        audience: 'general',
        animationStyle: 'presentation',
        subtitles: false,
      });
      setJobId(res.job_id);
      setJobStatus('ready_for_ai');
      setStageMessage('Presentation uploaded. Ready for multilingual generation.');
    } catch (err: unknown) {
      setJobStatus('failed');
      setErrorDetail(err instanceof Error ? err.message : 'Upload failed.');
    }
  };

  // 4. Handle "GENERATE ALL LANGUAGES"
  const handleGenerateAll = async () => {
    if (!jobId) return;
    setErrorDetail(null);
    setJobStatus('processing');
    setProgressPct(2);
    setStageMessage('Initiating single-action generation across all 6 languages...');

    try {
      await triggerGenerateAllLanguages(jobId);
    } catch (err: unknown) {
      setJobStatus('failed');
      setErrorDetail(err instanceof Error ? err.message : 'Generation trigger failed.');
    }
  };

  const activeTunnelCommand =
    (servicesHealth?.services?.tunnel?.remediation && servicesHealth.services.tunnel.remediation.includes('ssh'))
      ? servicesHealth.services.tunnel.remediation.replace('Run tunnel command: ', '').replace(' (or start local Ollama: \'ollama serve\')', '')
      : SSH_TUNNEL_COMMAND;

  const copyTunnelCommand = () => {
    navigator.clipboard.writeText(activeTunnelCommand);
    setCopiedTunnelCmd(true);
    setTimeout(() => setCopiedTunnelCmd(false), 2500);
  };

  const isServerOffline = backendState === 'offline' || (servicesHealth && !servicesHealth.ai_server_ready);

  return (
    <div className="app">
      <div className="page" style={{ maxWidth: 960 }}>
        {/* ─── Header ──────────────────────────────────────────────────────── */}
        <header className="header">
          <div className="header-brand">
            <Film size={28} style={{ color: '#4f46e5' }} />
            <div>
              <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Explain Studio</h1>
              <span style={{ fontSize: 13, color: '#64748b', fontWeight: 500 }}>
                Server-Powered Multilingual PowerPoint Video Platform
              </span>
            </div>
          </div>

          <div className="header-badges">
            {servicesHealth?.hardware_profile && (
              <span
                style={{
                  fontSize: 11,
                  background: '#f1f5f9',
                  border: '1px solid #cbd5e1',
                  color: '#334155',
                  padding: '4px 8px',
                  borderRadius: 6,
                  fontWeight: 700,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  textTransform: 'uppercase',
                  letterSpacing: '0.03em',
                }}
                title="Hardware Execution Profile"
              >
                ⚡ {servicesHealth.hardware_profile.replace(/_/g, ' ')}
              </span>
            )}
            <button className="conn-badge" onClick={checkHealth} title="Click to refresh server diagnostics">
              <span className={`conn-dot ${backendState === 'connected' ? 'connected' : 'offline'}`} />
              <span>
                {backendState === 'connected'
                  ? `Engine Active (v${backendHealth?.version || '0.6'})`
                  : backendState === 'checking'
                  ? 'Checking...'
                  : 'Engine Offline'}
              </span>
            </button>
            <button
              onClick={() => setShowDiagnosticsModal(true)}
              style={{
                fontSize: 12,
                background: '#e0e7ff',
                color: '#3730a3',
                padding: '4px 10px',
                borderRadius: 6,
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
              }}
            >
              <Activity size={13} />
              <span>Diagnostics</span>
            </button>
          </div>
        </header>

        {/* ─── Server Alert Banner (When Server/Tunnel is Offline) ─────────── */}
        {isServerOffline && (
          <div className="server-alert-banner offline">
            <div className="server-alert-content">
              <AlertTriangle size={22} className="server-alert-icon" style={{ color: '#dc2626' }} />
              <div>
                <div className="server-alert-title">AI Server Status Notice</div>
                <div className="server-alert-desc">
                  AI server is currently unavailable. We cannot process your presentation right now. Please check the
                  server connection or try again later. Your uploaded presentation has not been lost.
                </div>
              </div>
            </div>
            <div className="server-alert-actions">
              <button className="server-btn-retry" onClick={checkHealth}>
                <RefreshCw size={13} />
                <span>Retry Connection</span>
              </button>
              <button className="server-btn-diag" onClick={() => setShowDiagnosticsModal(true)}>
                <span>Diagnostics</span>
              </button>
            </div>
          </div>
        )}

        {/* ─── SECTION A: Presentation Upload ──────────────────────────────── */}
        <section className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h2 className="card-title" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Presentation size={18} style={{ color: '#4f46e5' }} />
              Section A — Presentation Upload
            </h2>
            {sourceFile && (
              <span
                style={{
                  fontSize: 12,
                  background: '#f0fdf4',
                  color: '#15803d',
                  padding: '3px 8px',
                  borderRadius: 4,
                  fontWeight: 600,
                }}
              >
                Ready
              </span>
            )}
          </div>

          {!sourceFile ? (
            <div
              style={{
                border: '2px dashed #cbd5e1',
                borderRadius: 10,
                padding: '36px 20px',
                textAlign: 'center',
                background: '#f8fafc',
                cursor: 'pointer',
              }}
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (e.dataTransfer.files?.[0]) handleFileChange(e.dataTransfer.files[0]);
              }}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".pptx"
                style={{ display: 'none' }}
                onChange={(e) => {
                  if (e.target.files?.[0]) handleFileChange(e.target.files[0]);
                }}
              />
              <Presentation size={36} style={{ color: '#94a3b8', margin: '0 auto 12px' }} />
              <div style={{ fontSize: 15, fontWeight: 600, color: '#334155', marginBottom: 4 }}>
                Drop your PowerPoint presentation here, or click to browse
              </div>
              <div style={{ fontSize: 13, color: '#64748b' }}>Supports .pptx presentations up to 50 MB</div>
            </div>
          ) : (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '12px 16px',
                background: '#f8fafc',
                border: '1px solid #e2e8f0',
                borderRadius: 8,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <Presentation size={24} style={{ color: '#4f46e5' }} />
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: '#1e293b' }}>{sourceFile.name}</div>
                  <div style={{ fontSize: 12, color: '#64748b' }}>
                    {formatBytes(sourceFile.size)} • PowerPoint Presentation
                    {course ? ` • ${course.total_slides} Slides Extracted` : ''}
                  </div>
                </div>
              </div>

              {jobStatus !== 'processing' && (
                <button
                  style={{
                    background: 'none',
                    border: 'none',
                    color: '#94a3b8',
                    cursor: 'pointer',
                    padding: 6,
                  }}
                  title="Remove presentation"
                  onClick={() => {
                    setSourceFile(null);
                    setJobId(null);
                    setCourse(null);
                    setJobStatus('idle');
                    setStageMessage('');
                    setProgressPct(0);
                  }}
                >
                  <Trash2 size={18} />
                </button>
              )}
            </div>
          )}
        </section>

        {/* ─── SECTION B: Language Configuration ───────────────────────────── */}
        <section className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <h2 className="card-title" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Languages size={18} style={{ color: '#4f46e5' }} />
              Section B — Multilingual Language Suite
            </h2>
            <span style={{ fontSize: 12, color: '#64748b', fontWeight: 500 }}>
              Single-Run Simultaneous Generation
            </span>
          </div>
          <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 12px' }}>
            All six primary languages are synthesized simultaneously from your PowerPoint presentation:
          </p>

          <div className="lang-grid">
            {DEFAULT_LANGUAGES.map((lang) => (
              <div key={lang.code} className="lang-card selected">
                <div className="lang-card-info">
                  <span className="lang-card-native">{lang.native_name}</span>
                  <span className="lang-card-name">{lang.name}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span className="lang-card-code">{lang.code}</span>
                  <CheckCircle2 size={18} style={{ color: '#10b981' }} />
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ─── SECTION C: Concept-Based Module Review & Uniqueness ─────────── */}
        {course && (
          <section className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h2 className="card-title" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Layers size={18} style={{ color: '#4f46e5' }} />
                Section C — Concept-Based Modules & Slide Uniqueness
              </h2>
              {course.validation_report?.valid && (
                <div className="badge-uniqueness">
                  <FileCheck2 size={16} />
                  <span>Strict Slide Uniqueness: 100% Unique & Disjoint</span>
                </div>
              )}
            </div>

            <div style={{ fontSize: 13, color: '#475569', marginBottom: 14 }}>
              Total Presentation Slides: <strong>{course.total_slides}</strong> | Generated Modules:{' '}
              <strong>{course.total_modules}</strong> | Languages: <strong>6</strong>
            </div>

            <div className="module-list">
              {course.modules.map((mod) => (
                <div key={mod.module_id} className="module-card">
                  <div className="module-header">
                    <span className="module-title">{mod.title}</span>
                    <span className="slide-pill">
                      Slides {mod.slide_numbers[0]}–{mod.slide_numbers[mod.slide_numbers.length - 1]} (
                      {mod.slide_numbers.length} {mod.slide_numbers.length === 1 ? 'slide' : 'slides'})
                    </span>
                  </div>
                  <div className="module-body">
                    <div style={{ fontSize: 13, color: '#334155', marginBottom: 6 }}>{mod.description}</div>
                    {mod.learning_objectives?.length > 0 && (
                      <div style={{ fontSize: 12, color: '#64748b' }}>
                        <strong>Objective:</strong> {mod.learning_objectives.join('; ')}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ─── SECTION D: Single Generation Action & Dynamic Progress ──────── */}
        <section className="card" style={{ background: '#f8fafc', border: '1.5px solid #e2e8f0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h2 className="card-title" style={{ margin: 0 }}>
              Section D — Single Action Production
            </h2>
            {jobStatus === 'processing' && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#4f46e5', fontWeight: 600 }}>
                <Loader2 size={16} className="animate-spin" />
                Generating all 6 language videos...
              </span>
            )}
          </div>

          <button
            className="btn-generate-all"
            disabled={!jobId || jobStatus === 'processing' || jobStatus === 'uploading'}
            onClick={handleGenerateAll}
          >
            {jobStatus === 'processing' ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                <span>GENERATING ALL 6 LANGUAGES ({Math.round(progressPct)}%)...</span>
              </>
            ) : (
              <>
                <Play size={20} fill="#ffffff" />
                <span>GENERATE ALL LANGUAGES</span>
              </>
            )}
          </button>

          {/* Dynamic Progress Loader with Live ETA and Breakdown */}
          {jobStatus === 'processing' && (
            <div className="progress-container">
              <div className="progress-header-row">
                <span className="progress-stage-title">
                  <Loader2 size={16} className="animate-spin" style={{ color: '#4f46e5' }} />
                  <span>{stageMessage || 'Processing presentation...'}</span>
                </span>
                <span className="progress-pct-badge">{Math.round(progressPct)}%</span>
              </div>

              <div className="progress-bar-track">
                <div className="progress-bar-fill" style={{ width: `${Math.max(3, Math.min(100, progressPct))}%` }} />
              </div>

              <div className="progress-footer-row">
                <span className="eta-pill">
                  <Clock size={13} />
                  <span>{progressDetail?.eta_human || (etaSeconds ? `~${Math.round(etaSeconds)}s remaining` : 'Calculating ETA...')}</span>
                </span>

                <div className="stat-pills-row">
                  {progressDetail?.slides_analyzed !== undefined && (
                    <span className="stat-pill">
                      Slides: {progressDetail.slides_analyzed}/{progressDetail.total_slides}
                    </span>
                  )}
                  {progressDetail?.audio_synthesized !== undefined && (
                    <span className="stat-pill">
                      Audio: {progressDetail.audio_synthesized}/{progressDetail.total_audio}
                    </span>
                  )}
                  {progressDetail?.videos_rendered !== undefined && (
                    <span className="stat-pill">
                      Clips: {progressDetail.videos_rendered}/{progressDetail.total_videos}
                    </span>
                  )}
                  {progressDetail?.elapsed_seconds !== undefined && (
                    <span className="stat-pill">
                      Elapsed: {Math.round(progressDetail.elapsed_seconds)}s
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}

          {jobStatus !== 'processing' && stageMessage && (
            <div
              style={{
                marginTop: 14,
                padding: '10px 14px',
                background: '#ffffff',
                border: '1px solid #e2e8f0',
                borderRadius: 8,
                fontSize: 13,
                color: '#334155',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              {jobStatus === 'completed' ? (
                <CheckCircle2 size={16} style={{ color: '#10b981' }} />
              ) : (
                <AlertTriangle size={16} style={{ color: '#f59e0b' }} />
              )}
              <span>{stageMessage}</span>
            </div>
          )}

          {errorDetail && (
            <div
              style={{
                marginTop: 12,
                padding: '10px 14px',
                background: '#fef2f2',
                border: '1px solid #fecaca',
                borderRadius: 8,
                fontSize: 13,
                color: '#991b1b',
              }}
            >
              {errorDetail}
            </div>
          )}
        </section>

        {/* ─── SECTION E: Multilingual Presentation Player ─────────────── */}
        {course && jobId && (
          <section className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '20px 24px', borderBottom: '1px solid #e2e8f0', background: '#ffffff' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <h2 className="card-title" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Presentation size={20} style={{ color: '#4f46e5' }} />
                    <span>Section E — Original PowerPoint Presentation Player</span>
                  </h2>
                  <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
                    Synchronized in-app presentation displaying original PowerPoint slides with natural AI teaching narration.
                  </div>
                </div>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    background: '#ecfdf5',
                    color: '#065f46',
                    padding: '6px 14px',
                    borderRadius: 20,
                    fontSize: 12,
                    fontWeight: 600,
                  }}
                >
                  <CheckCircle2 size={16} style={{ color: '#10b981' }} />
                  <span>Interactive 6-Language Player</span>
                </div>
              </div>
            </div>

            <PresentationPlayer
              jobId={jobId}
              course={course}
              languages={DEFAULT_LANGUAGES}
              initialLanguage={selectedLanguage}
              initialVoiceGender={
                (() => {
                  try {
                    const admin = JSON.parse(sessionStorage.getItem('skillbridge_admin') || localStorage.getItem('skillbridge_admin') || 'null');
                    if (admin?.aiVoiceGender) return String(admin.aiVoiceGender).toLowerCase() === 'male' ? 'male' : 'female';
                  } catch (_) {}
                  return 'female';
                })()
              }
            />
          </section>
        )}
      </div>

      {/* ─── Diagnostics & Infrastructure Modal ────────────────────────────── */}
      {showDiagnosticsModal && (
        <div className="diag-modal-backdrop" onClick={() => setShowDiagnosticsModal(false)}>
          <div className="diag-modal" onClick={(e) => e.stopPropagation()}>
            <div className="diag-modal-header">
              <div className="diag-modal-title">
                <Server size={18} style={{ color: '#4f46e5' }} />
                <span>Explain Studio Infrastructure Diagnostics</span>
              </div>
              <button
                onClick={() => setShowDiagnosticsModal(false)}
                style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', padding: 4 }}
              >
                <X size={20} />
              </button>
            </div>

            <div className="diag-modal-body">
              {/* SSH Tunnel */}
              <div className="diag-item">
                <div className="diag-item-left">
                  <Activity size={18} style={{ color: '#4f46e5', marginTop: 2 }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b' }}>SSH Tunnel (Port 11435)</div>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                      {servicesHealth?.services.tunnel.message || 'Connecting to private tunnel...'}
                    </div>
                  </div>
                </div>
                <span className={`diag-status-pill ${servicesHealth?.services.tunnel.status || 'offline'}`}>
                  {servicesHealth?.services.tunnel.status || 'OFFLINE'}
                </span>
              </div>

              {/* SSH Tunnel Command Helper */}
              {(!servicesHealth?.services.tunnel.active || backendState === 'offline') && (
                <div style={{ background: '#f1f5f9', padding: 12, borderRadius: 8 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#334155', marginBottom: 6 }}>
                    Run this command in a terminal to open the tunnel:
                  </div>
                  <div className="code-box">
                    <span>{activeTunnelCommand}</span>
                    <button
                      onClick={copyTunnelCommand}
                      style={{
                        background: '#334155',
                        border: 'none',
                        color: '#ffffff',
                        padding: '4px 8px',
                        borderRadius: 4,
                        cursor: 'pointer',
                        fontSize: 11,
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                      }}
                    >
                      {copiedTunnelCmd ? <Check size={12} /> : <Copy size={12} />}
                      <span>{copiedTunnelCmd ? 'Copied' : 'Copy'}</span>
                    </button>
                  </div>
                </div>
              )}

              {/* Ollama Service */}
              <div className="diag-item">
                <div className="diag-item-left">
                  <Server size={18} style={{ color: '#0284c7', marginTop: 2 }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b' }}>Ollama AI Service</div>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                      Base URL: {servicesHealth?.services.tunnel.base_url || 'http://127.0.0.1:11435'}
                      {servicesHealth?.services.ollama.latency_ms ? ` • ${servicesHealth.services.ollama.latency_ms} ms` : ''}
                    </div>
                  </div>
                </div>
                <span className={`diag-status-pill ${servicesHealth?.services.ollama.status || 'offline'}`}>
                  {servicesHealth?.services.ollama.status || 'OFFLINE'}
                </span>
              </div>

              {/* Models */}
              <div className="diag-item">
                <div className="diag-item-left">
                  <Layers size={18} style={{ color: '#7c3aed', marginTop: 2 }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b' }}>Required AI Models</div>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                      qwen2.5:14b (Reasoning) & qwen2.5vl:7b (Vision)
                    </div>
                    {servicesHealth?.services.models.verified_models && servicesHealth.services.models.verified_models.length > 0 && (
                      <div style={{ fontSize: 11, color: '#15803d', marginTop: 4 }}>
                        Verified: {servicesHealth.services.models.verified_models.join(', ')}
                      </div>
                    )}
                  </div>
                </div>
                <span className={`diag-status-pill ${servicesHealth?.services.models.status || 'offline'}`}>
                  {servicesHealth?.services.models.status || 'OFFLINE'}
                </span>
              </div>

              {/* GPU Safety Guard */}
              <div className="diag-item">
                <div className="diag-item-left">
                  <Cpu size={18} style={{ color: '#059669', marginTop: 2 }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b' }}>GPU Safety Manager</div>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                      {servicesHealth?.services.gpu.message || 'Single-task serialized execution guard active.'}
                    </div>
                  </div>
                </div>
                <span className={`diag-status-pill ${servicesHealth?.services.gpu.status || 'ok'}`}>
                  {servicesHealth?.services.gpu.status || 'READY'}
                </span>
              </div>

              {/* Storage */}
              <div className="diag-item">
                <div className="diag-item-left">
                  <HardDrive size={18} style={{ color: '#d97706', marginTop: 2 }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b' }}>Disk Storage</div>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                      {servicesHealth?.services.storage.free_gb !== undefined
                        ? `${servicesHealth.services.storage.free_gb} GB Free / ${servicesHealth.services.storage.total_gb} GB Total`
                        : 'Storage available'}
                    </div>
                  </div>
                </div>
                <span className={`diag-status-pill ${servicesHealth?.services.storage.status || 'ok'}`}>
                  {servicesHealth?.services.storage.status || 'OK'}
                </span>
              </div>

              {/* Hardware Profile & Execution Limits */}
              {servicesHealth?.services.hardware && (
                <div className="diag-item" style={{ background: '#f8fafc', padding: '10px 14px', borderRadius: 8 }}>
                  <div className="diag-item-left">
                    <Activity size={18} style={{ color: '#0d9488', marginTop: 2 }} />
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b' }}>
                        System Profile: {(servicesHealth.hardware_profile || 'standard').replace(/_/g, ' ').toUpperCase()}
                      </div>
                      <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                        OS: {String((servicesHealth.services.hardware as any).os_name || 'Host')} •
                        RAM: {String((servicesHealth.services.hardware as any).ram_total_gb || '?')} GB •
                        GPU: {String((servicesHealth.services.hardware as any).primary_gpu || 'Integrated / Remote')}
                      </div>
                      <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
                        Concurrency Limits: {String((servicesHealth.services.hardware as any).audio_concurrency || 4)} audio,{' '}
                        {String((servicesHealth.services.hardware as any).translation_concurrency || 4)} translation
                      </div>
                    </div>
                  </div>
                  <span className="diag-status-pill ok">ACTIVE</span>
                </div>
              )}

              {/* Speech Synthesis Engine */}
              <div className="diag-item">
                <div className="diag-item-left">
                  <Volume2 size={18} style={{ color: '#db2777', marginTop: 2 }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b' }}>Speech Synthesis Engine</div>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                      {servicesHealth?.services.tts.message || 'Edge TTS neural voice synthesis.'}
                    </div>
                  </div>
                </div>
                <span className={`diag-status-pill ${servicesHealth?.services.tts.status || 'ok'}`}>
                  {servicesHealth?.services.tts.status || 'OK'}
                </span>
              </div>
            </div>

            <div style={{ padding: '14px 20px', background: '#f8fafc', borderTop: '1px solid #e2e8f0', display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                className="server-btn-retry"
                style={{ background: '#4f46e5' }}
                onClick={checkHealth}
              >
                <RefreshCw size={13} />
                <span>Recheck All Services</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
