// ─── Shared types ────────────────────────────────────────────────────────────

export type BackendHealth = {
  status: string;
  service: string;
  version: string;
  mode: string;
  ai_enabled: boolean;
};

export type FileInfo = {
  file_id: string;
  original_name: string;
  kind: 'text' | 'presentation' | 'image';
  size_bytes: number;
};

export type JobCreatedResponse = {
  job_id: string;
  status: string;
  created_at: string;
  file_count: number;
  has_text: boolean;
  target_duration_seconds?: number;
  aspect_ratio?: string;
};

export type JobStatus =
  | 'pending'
  | 'extracting'
  | 'ready_for_ai'
  | 'analyzing'
  | 'analyzed'
  | 'processing'
  | 'completed'
  | 'failed';

export type JobStatusResponse = {
  job_id: string;
  status: JobStatus;
  stage: string;
  created_at: string;
  updated_at: string;
  has_text: boolean;
  files: FileInfo[];
  error: string | null;
  output_path: string | null;
  target_duration_seconds?: number;
  aspect_ratio?: string;
  progress_pct?: number;
  eta_seconds?: number | null;
  progress_detail?: string | null;
};

export type ContentBlock = {
  id: string;
  source_id: string;
  original_filename: string;
  source_type: string;
  slide_number?: number | null;
  block_type: string;
  text?: string | null;
  asset_reference?: string | null;
  extraction_metadata?: Record<string, unknown>;
};

export type SourceMetadata = {
  source_id: string;
  original_filename: string;
  source_type: string;
  file_size_bytes: number;
  sha256: string;
  num_blocks: number;
  extracted_asset_count: number;
};

export type JobSourcesResponse = {
  job_id: string;
  status: JobStatus;
  extracted: boolean;
  stage?: string | null;
  extracted_at?: string | null;
  sources: SourceMetadata[];
  blocks: ContentBlock[];
  total_blocks: number;
  total_assets: number;
  error?: string | null;
};

export type AIHealthResponse = {
  status: string;
  connected: boolean;
  base_url: string;
  latency_ms: number | null;
  tunnel_active: boolean;
  error: string | null;
  note: string;
};

export type AIModelsResponse = {
  connected: boolean;
  base_url: string;
  all_expected_present: boolean;
  expected_models: string[];
  installed_models: string[];
  verified_models: string[];
  missing_models: string[];
  models: Array<Record<string, unknown>>;
  error: string | null;
  note: string;
};

// ─── AI Analysis Types ────────────────────────────────────────────────────────

export type SourceReference = {
  source_id: string;
  original_filename: string;
  slide_number?: number | null;
  content_block_id?: string | null;
  asset_reference?: string | null;
};

export type MainConcept = {
  name: string;
  explanation: string;
  importance: string;
  source_references: SourceReference[];
};

export type SupportingConcept = {
  name: string;
  explanation: string;
  source_references: SourceReference[];
};

export type ImportantFact = {
  statement: string;
  context?: string | null;
  source_references: SourceReference[];
};

export type Definition = {
  term: string;
  definition: string;
  source_references: SourceReference[];
};

export type ConceptRelationship = {
  from_concept: string;
  to_concept: string;
  relationship_type: string;
  description: string;
};

export type Example = {
  concept: string;
  description: string;
  source_references: SourceReference[];
};

export type LearningObjective = {
  objective: string;
  blooms_level?: string | null;
};

export type VisualAnalysisItem = {
  source_id: string;
  original_filename: string;
  slide_number?: number | null;
  content_block_id?: string | null;
  asset_reference: string;
  visual_type: string;
  description: string;
  extracted_text?: string | null;
  key_takeaways: string[];
  relevance_to_topic?: string | null;
};

export type AmbiguityItem = {
  topic: string;
  issue: string;
  missing_information: string;
};

export type AIAnalysisResult = {
  job_id: string;
  analyzed_at: string;
  title: string;
  subject: string;
  summary: string;
  main_concepts: MainConcept[];
  supporting_concepts: SupportingConcept[];
  important_facts: ImportantFact[];
  definitions: Definition[];
  concept_relationships: ConceptRelationship[];
  examples: Example[];
  prerequisites: string[];
  learning_objectives: LearningObjective[];
  ambiguities_and_gaps: AmbiguityItem[];
  visual_analyses: VisualAnalysisItem[];
  metadata?: Record<string, unknown>;
};

export type TeachingModule = {
  module_id: string;
  title: string;
  purpose: string;
  target_concepts: string[];
  prerequisite_bridges: string[];
  key_analogies: string[];
  depth_level: string;
  estimated_duration_seconds: number;
  start_slide?: number;
  end_slide?: number;
  slide_numbers?: number[];
  learning_objective?: string;
  important_facts?: string[];
  render_status?: string;
  output_video_path?: string | null;
};

export type EducationalPlan = {
  job_id: string;
  audience_level: string;
  pedagogical_approach: string;
  pacing_strategy: string;
  modules: TeachingModule[];
  total_estimated_seconds: number;
};

export type ScriptSegment = {
  segment_id: string;
  scene_id: string;
  module_id: string;
  speaker_tone: string;
  narration_text: string;
  word_count: number;
  estimated_duration_seconds: number;
  source_references: SourceReference[];
};

export type NarrationScript = {
  job_id: string;
  title: string;
  target_audience: string;
  total_word_count: number;
  estimated_reading_time_seconds: number;
  segments: ScriptSegment[];
};

export type StoryboardScene = {
  scene_id: string;
  module_id: string;
  scene_purpose: string;
  narration_text: string;
  visual_description: string;
  animation_type: string;
  on_screen_text: string[];
  visual_asset_references: string[];
  source_references: SourceReference[];
  estimated_visual_duration: number;
  transition_instructions: string;
};

export type Storyboard = {
  job_id: string;
  title: string;
  total_scenes: number;
  estimated_total_duration_seconds: number;
  scenes: StoryboardScene[];
};

export type SceneDurationBreakdown = {
  scene_id: string;
  narration_duration_seconds: number;
  visual_demonstration_duration_seconds: number;
  cognitive_pause_seconds: number;
  reading_time_seconds: number;
  transition_duration_seconds: number;
  total_scene_duration_seconds: number;
};

export type ModuleDurationReconciliation = {
  module_id: string;
  title: string;
  planned_module_duration_seconds: number;
  actual_calculated_duration_seconds: number;
  reconciliation_note: string;
};

export type DurationEstimate = {
  total_duration_seconds: number;
  formatted_duration: string;
  narration_duration_seconds: number;
  visual_demonstration_duration_seconds?: number;
  cognitive_pause_seconds?: number;
  reading_time_not_already_accounted_for_seconds?: number;
  transition_duration_seconds?: number;
  visual_reading_pause_seconds: number;
  transition_padding_seconds: number;
  words_per_minute: number;
  total_words?: number;
  pacing_note: string;
  scene_breakdowns?: SceneDurationBreakdown[];
  module_reconciliations?: ModuleDurationReconciliation[];
  measured_audio_duration_override?: number | null;
};

export type QualityReport = {
  overall_score: number;
  passed: boolean;
  source_coverage_pct: number;
  total_claims_grounded: number;
  invalid_source_references: string[];
  mismatched_source_references?: string[];
  unsupported_claims: string[];
  missing_concepts: string[];
  duplicate_explanations: string[];
  script_completeness_check: boolean;
  storyboard_consistency_check: boolean;
  actionable_reasons?: string[];
  warnings: string[];
};

export type JobAnalysisResponse = {
  job_id: string;
  status: JobStatus;
  stage?: string | null;
  analyzed: boolean;
  analysis?: AIAnalysisResult | null;
  plan?: EducationalPlan | null;
  duration?: DurationEstimate | null;
  quality?: QualityReport | null;
  error?: string | null;
};

export type JobScriptResponse = {
  job_id: string;
  status: JobStatus;
  stage?: string | null;
  has_script: boolean;
  script?: NarrationScript | null;
  error?: string | null;
};

export type JobStoryboardResponse = {
  job_id: string;
  status: JobStatus;
  stage?: string | null;
  has_storyboard: boolean;
  storyboard?: Storyboard | null;
  duration?: DurationEstimate | null;
  quality?: QualityReport | null;
  error?: string | null;
};

export type JobRenderResponse = {
  job_id: string;
  status: string;
  rendering_authorized: boolean;
  overall_score: number;
  message: string;
};

// ─── Config ──────────────────────────────────────────────────────────────────

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
).replace(/\/$/, '');

export { API_BASE_URL };

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Extract a human-readable error message from a failed fetch response. */
async function extractError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? String(d)).join(' · ');
    return `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

// ─── Endpoints ───────────────────────────────────────────────────────────────

export async function checkBackendHealth(): Promise<BackendHealth> {
  const res = await fetch(`${API_BASE_URL}/health`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`Backend returned HTTP ${res.status}`);
  return res.json() as Promise<BackendHealth>;
}

export async function checkAIHealth(): Promise<AIHealthResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/ai/health`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<AIHealthResponse>;
}

export type ServiceDiagnostic = {
  status: 'ok' | 'warning' | 'error' | 'offline' | 'busy';
  message: string;
  latency_ms?: number | null;
  [key: string]: unknown;
};

export type ServicesHealthResponse = {
  status: 'ready' | 'degraded' | 'not_ready';
  ready: boolean;
  ai_server_ready: boolean;
  total_latency_ms: number;
  hardware_profile?: string;
  services: {
    backend: ServiceDiagnostic;
    tunnel: ServiceDiagnostic & { active: boolean; base_url?: string; remediation?: string | null };
    ollama: ServiceDiagnostic & { connected: boolean; latency_ms?: number | null; base_url?: string };
    models: ServiceDiagnostic & { all_present: boolean; verified_models: string[]; missing_models: string[] };
    gpu: ServiceDiagnostic & { is_busy: boolean; queue_count: number; current_task?: string | null };
    storage: ServiceDiagnostic & { free_gb: number; total_gb: number };
    tts: ServiceDiagnostic & { ready: boolean; provider: string };
    hardware?: Record<string, unknown>;
  };
};

export async function checkServicesHealth(): Promise<ServicesHealthResponse> {
  const res = await fetch(`${API_BASE_URL}/health/services`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(6000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<ServicesHealthResponse>;
}

export async function checkAIModels(): Promise<AIModelsResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/ai/models`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<AIModelsResponse>;
}

export async function createJob(
  text: string,
  files: File[],
  settings: { audience: string; animationStyle: string; subtitles: boolean; targetDurationSeconds?: number; aspectRatio?: string },
): Promise<JobCreatedResponse> {
  const form = new FormData();
  if (text.trim()) form.append('text', text.trim());
  form.append('audience', settings.audience);
  form.append('animation_style', settings.animationStyle);
  form.append('subtitles', String(settings.subtitles ?? false));
  if (settings.targetDurationSeconds) {
    form.append('target_duration_seconds', String(settings.targetDurationSeconds));
  }
  if (settings.aspectRatio) {
    form.append('aspect_ratio', settings.aspectRatio);
  }
  for (const file of files) {
    form.append('files', file);
  }

  const res = await fetch(`${API_BASE_URL}/v1/jobs`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: form,
    signal: AbortSignal.timeout(60_000),
  });

  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<JobCreatedResponse>;
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${jobId}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<JobStatusResponse>;
}

export async function getJobSources(jobId: string): Promise<JobSourcesResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${jobId}/sources`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<JobSourcesResponse>;
}

export async function triggerAnalysis(
  jobId: string,
): Promise<{ job_id: string; status: JobStatus; stage: string }> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${jobId}/analyze`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<{ job_id: string; status: JobStatus; stage: string }>;
}

export async function getJobAnalysis(jobId: string): Promise<JobAnalysisResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${jobId}/analysis`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<JobAnalysisResponse>;
}

export async function getJobScript(jobId: string): Promise<JobScriptResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${jobId}/script`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<JobScriptResponse>;
}

export async function getJobStoryboard(jobId: string): Promise<JobStoryboardResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${jobId}/storyboard`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json() as Promise<JobStoryboardResponse>;
}

export async function triggerJobRender(jobId: string): Promise<JobRenderResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}/render`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => null);
    const detail = errorBody?.detail;
    if (typeof detail === 'object' && detail?.message) {
      const reasons = Array.isArray(detail.actionable_reasons) ? detail.actionable_reasons.join('; ') : '';
      throw new Error(`${detail.message} ${reasons}`.trim());
    }
    throw new Error(await extractError(res));
  }
  return res.json() as Promise<JobRenderResponse>;
}

export function getMasterVideoUrl(jobId: string): string {
  return `${API_BASE_URL}/v1/output/${encodeURIComponent(jobId)}`;
}

export function getLessonVideoUrl(jobId: string, lessonId: string): string {
  return `${API_BASE_URL}/v1/output/${encodeURIComponent(jobId)}/lessons/${encodeURIComponent(lessonId)}`;
}

// ─── Multilingual Course Types & Endpoints ────────────────────────────────────

export type SupportedLanguage = {
  code: string;
  name: string;
  native_name: string;
  default_voice: string;
};

export type SlideExplanation = {
  slide_number: number;
  slide_id: string;
  module_id: string;
  title: string;
  key_points: string[];
  table_summaries: string[];
  image_descriptions: string[];
  slide_image_path: string;
  explanations: Record<string, string>;
  durations_seconds: Record<string, number>;
  audio_files: Record<string, string>;
  module_start_seconds: Record<string, number>;
  module_end_seconds: Record<string, number>;
  course_start_seconds: Record<string, number>;
  course_end_seconds: Record<string, number>;
};

export type CourseModule = {
  module_id: string;
  module_number: number;
  title: string;
  description: string;
  learning_objectives: string[];
  slide_numbers: number[];
  slides: SlideExplanation[];
  durations_seconds: Record<string, number>;
  module_ready?: Record<string, boolean>;
  video_files?: Record<string, string>;
};

export type MultilingualCourse = {
  job_id: string;
  title: string;
  total_slides: number;
  total_modules: number;
  languages: string[];
  modules: CourseModule[];
  glossary: Record<string, Record<string, string>>;
  course_video_files: Record<string, string>;
  validation_report: {
    valid: boolean;
    total_presentation_slides: number;
    total_allocated_slides: number;
    module_count: number;
    missing_slides: number[];
    extra_slides: number[];
    overlaps: unknown[];
    message: string;
  };
};

export type CourseResponse = {
  job_id: string;
  status: JobStatus;
  stage: string;
  has_course: boolean;
  course: MultilingualCourse | null;
  error: string | null;
};

export async function triggerGenerateAllLanguages(
  jobId: string,
): Promise<{ job_id: string; status: JobStatus; stage: string; message: string }> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}/generate_all_languages`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json();
}

export async function getJobCourse(jobId: string): Promise<CourseResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}/course`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return res.json();
}

export function getRenderedSlideUrl(jobId: string, slideNumber: number): string {
  return `${API_BASE_URL}/v1/courses/${encodeURIComponent(jobId)}/slides/${slideNumber}`;
}

export function getSlideAudioUrl(
  jobId: string,
  langCode: string,
  slideNumber: number,
  gender: 'male' | 'female' = 'female'
): string {
  return `${API_BASE_URL}/v1/courses/${encodeURIComponent(jobId)}/audio/${encodeURIComponent(langCode)}/${slideNumber}?gender=${gender}`;
}

export function getModuleVideoStreamUrl(jobId: string, moduleId: string, langCode: string): string {
  return `${API_BASE_URL}/v1/courses/${encodeURIComponent(jobId)}/stream/module/${encodeURIComponent(moduleId)}/${encodeURIComponent(langCode)}`;
}

export function getCompleteCourseStreamUrl(jobId: string, langCode: string): string {
  return `${API_BASE_URL}/v1/courses/${encodeURIComponent(jobId)}/stream/complete/${encodeURIComponent(langCode)}`;
}

export function getModuleVideoDownloadUrl(jobId: string, moduleId: string, langCode: string): string {
  return `${API_BASE_URL}/v1/courses/${encodeURIComponent(jobId)}/download/module/${encodeURIComponent(moduleId)}/${encodeURIComponent(langCode)}`;
}

export function getCompleteCourseDownloadUrl(jobId: string, langCode: string): string {
  return `${API_BASE_URL}/v1/courses/${encodeURIComponent(jobId)}/download/complete/${encodeURIComponent(langCode)}`;
}

export function getAllModulesZipDownloadUrl(jobId: string, langCode: string): string {
  return `${API_BASE_URL}/v1/courses/${encodeURIComponent(jobId)}/download/all/${encodeURIComponent(langCode)}`;
}


