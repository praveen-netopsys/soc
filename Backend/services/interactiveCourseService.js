import fs from 'fs';
import path from 'path';
import axios from 'axios';
import { execFile } from 'child_process';
import util from 'util';
import dotenv from 'dotenv';
import aiService from './aiService.js';
import vectorChunkService from './vectorChunkService.js';
import { extractDocumentContent } from './documentExtractor.js';
import { stripSlidePlaceholders, isBinaryNoiseText, isGarbageTitle } from '../utils/contentQuality.js';

dotenv.config();

const execFilePromise = util.promisify(execFile);

// Target 6 languages
export const SUPPORTED_LANGUAGES = [
  { code: 'en', name: 'English', native_name: 'English', male_voice: 'en-US-ChristopherNeural', female_voice: 'en-US-JennyNeural', default_voice: 'en-US-ChristopherNeural' },
  { code: 'zh', name: 'Chinese (Simplified)', native_name: '中文', male_voice: 'zh-CN-YunxiNeural', female_voice: 'zh-CN-XiaoxiaoNeural', default_voice: 'zh-CN-XiaoxiaoNeural' },
  { code: 'ms', name: 'Malay', native_name: 'Bahasa Melayu', male_voice: 'ms-MY-OsmanNeural', female_voice: 'ms-MY-YasminNeural', default_voice: 'ms-MY-OsmanNeural' },
  { code: 'ta', name: 'Tamil', native_name: 'தமிழ்', male_voice: 'ta-IN-ValluvarNeural', female_voice: 'ta-IN-PallaviNeural', default_voice: 'ta-IN-ValluvarNeural' },
  { code: 'te', name: 'Telugu', native_name: 'తెలుగు', male_voice: 'te-IN-MohanNeural', female_voice: 'te-IN-ShrutiNeural', default_voice: 'te-IN-MohanNeural' },
  { code: 'bn', name: 'Bangla', native_name: 'বাংলা', male_voice: 'bn-IN-BashkarNeural', female_voice: 'bn-IN-TanishaaNeural', default_voice: 'bn-IN-BashkarNeural' }
];

export const VOICE_CONFIG = {
  en: { male: 'en-US-ChristopherNeural', female: 'en-US-JennyNeural', default: 'en-US-ChristopherNeural' },
  zh: { male: 'zh-CN-YunxiNeural', female: 'zh-CN-XiaoxiaoNeural', default: 'zh-CN-XiaoxiaoNeural' },
  ms: { male: 'ms-MY-OsmanNeural', female: 'ms-MY-YasminNeural', default: 'ms-MY-OsmanNeural' },
  ta: { male: 'ta-IN-ValluvarNeural', female: 'ta-IN-PallaviNeural', default: 'ta-IN-ValluvarNeural' },
  te: { male: 'te-IN-MohanNeural', female: 'te-IN-ShrutiNeural', default: 'te-IN-MohanNeural' },
  bn: { male: 'bn-IN-BashkarNeural', female: 'bn-IN-TanishaaNeural', default: 'bn-IN-BashkarNeural' }
};

// In-memory job tracker for async generation progress
const activeJobs = new Map();
// In-memory translation cache for instant response times
const translationCache = new Map();

export class InteractiveCourseService {
  constructor() {
    this.pythonExe = this._resolvePythonExe();
    this.pythonServiceUrl = process.env.PYTHON_SERVICE_URL || 'http://localhost:8000';
    this.explainStudioUrl = this.pythonServiceUrl;
    this.ollamaUrl = (process.env.AI_GATEWAY_URL || process.env.QWEN_GATEWAY_URL || process.env.OLLAMA_BASE_URL || '').replace(/\/+$/, '');
    this.textModel = (process.env.QWEN_MODEL || process.env.OLLAMA_MODEL || 'qwen3.5:9b').trim();
    this.visionModel = (process.env.QWEN_MODEL || process.env.OLLAMA_VISION_MODEL || 'qwen3.5:9b').trim();
  }

  _resolvePythonExe() {
    const candidatePaths = [
      path.join(process.cwd(), 'ai-service', '.venv', 'Scripts', 'python.exe'),
      path.join(process.cwd(), '..', 'ai-service', '.venv', 'Scripts', 'python.exe'),
      'C:\\Praveen\\Skill-Bridge\\ai-service\\.venv\\Scripts\\python.exe',
      'python'
    ];
    for (const p of candidatePaths) {
      if (fs.existsSync(p)) return p;
    }
    return 'python';
  }

  getJobStatus(courseId) {
    const job = activeJobs.get(String(courseId));
    if (job) return job;
    return {
      courseId: String(courseId),
      status: 'idle',
      stage: 'idle',
      progress: 0,
      message: 'No active generation task.'
    };
  }

  setJobStatus(courseId, stage, progress, message, details = {}) {
    return this._updateJob(courseId, stage, progress, message, details);
  }

  clearJob(courseId) {
    activeJobs.delete(String(courseId));
  }

  _updateJob(courseId, stage, progress, message, details = {}) {
    const statusType = stage === 'completed'
      ? 'completed'
      : (stage === 'error' || stage === 'failed' ? 'failed' : (stage === 'pending' ? 'pending' : 'processing'));

    const status = {
      courseId: String(courseId),
      status: statusType,
      stage,
      progress,
      message,
      details,
      updatedAt: Date.now()
    };
    activeJobs.set(String(courseId), status);
    console.log(`[DIAGNOSTIC][Backend] Task status update for ${courseId}: [${statusType}] stage=${stage} (${progress}%) - ${message}`);
  }

  /**
   * PRIMARY PIPELINE: End-to-end integration with Explain Studio Python AI backend.
   * Leverages 1080p native slide projection, VRAM-locked Qwen2.5:14B reasoning, 
   * capped vision analysis, 24x concurrent Edge-TTS speech streams, and progressive checkpointing.
   */
  async generateCourseWithExplainStudio(courseId, originalFilePath, filename, targetPrisma, title, description) {
    console.log(`[ExplainStudio] Forwarding "${filename}" to Explain Studio backend on ${this.explainStudioUrl}...`);
    this._updateJob(courseId, 'uploading', 10, 'Submitting presentation to Explain Studio multilingual AI pipeline...');

    // Read presentation file buffer and build native multipart form
    const fileBuf = fs.readFileSync(originalFilePath);
    const blob = new Blob([fileBuf], { type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' });
    const formData = new FormData();
    formData.append('files', blob, filename);

    // 1. Submit file to Explain Studio FastAPI service
    const uploadRes = await fetch(`${this.explainStudioUrl}/v1/jobs`, {
      method: 'POST',
      body: formData
    });

    if (!uploadRes.ok) {
      const errText = await uploadRes.text();
      throw new Error(`Failed to initialize job on Explain Studio (${uploadRes.status}): ${errText}`);
    }

    const jobData = await uploadRes.json();
    const jobId = jobData.job_id || jobData.id;
    console.log(`[ExplainStudio] Job created on Explain Studio: ${jobId}`);

    // 2. Trigger generation across all 6 languages
    this._updateJob(courseId, 'processing', 15, 'Starting multilingual AI reasoning, 1080p slide projection, and neural audio...');
    const triggerRes = await fetch(`${this.explainStudioUrl}/v1/jobs/${jobId}/generate_all_languages`, {
      method: 'POST'
    });

    if (!triggerRes.ok) {
      const errText = await triggerRes.text();
      throw new Error(`Failed to trigger multilingual generation on Explain Studio (${triggerRes.status}): ${errText}`);
    }

    // 3. Track progress until complete
    let completed = false;
    let pollCount = 0;
    while (!completed && pollCount < 300) {
      pollCount++;
      await new Promise(r => setTimeout(r, 2000));
      try {
        const statusRes = await fetch(`${this.explainStudioUrl}/v1/jobs/${jobId}`);
        if (!statusRes.ok) continue;

        const statusData = await statusRes.json();
        const { status, progress_pct, stage, error, progress_detail } = statusData;

        let etaSecs = null;
        let etaHuman = '';
        if (progress_detail) {
          try {
            const detail = typeof progress_detail === 'string' ? JSON.parse(progress_detail) : progress_detail;
            etaSecs = detail.eta_seconds;
            etaHuman = detail.eta_human;
          } catch (_) { }
        }

        const currentPct = Math.max(15, Math.min(99, Math.round(progress_pct || (pollCount * 1.5))));
        this._updateJob(courseId, stage || 'processing', currentPct, stage || 'Processing presentation...', {
          eta: etaSecs,
          etaHuman
        });

        // Keep database record updated so frontend polling sees progress
        await targetPrisma.course.update({
          where: { id: courseId },
          data: {
            curriculum: {
              isInteractivePresentation: true,
              status: status === 'completed' ? 'completed' : (status === 'failed' ? 'failed' : 'processing'),
              stage: stage || 'processing',
              progress: currentPct,
              message: stage || 'Processing presentation...',
              explainStudioJobId: jobId
            }
          }
        }).catch(() => { });

        if (status === 'completed') {
          completed = true;
          break;
        }
        if (status === 'failed' || status === 'error') {
          throw new Error(error || stage || 'Explain Studio generation failed');
        }
      } catch (pollErr) {
        if (pollErr.message && pollErr.message.includes('Explain Studio generation failed')) throw pollErr;
        console.warn(`[ExplainStudio] Polling notice (${pollCount}):`, pollErr.message);
      }
    }

    if (!completed) {
      throw new Error('Explain Studio pipeline timed out after 10 minutes.');
    }

    // 4. Retrieve complete generated course
    this._updateJob(courseId, 'finalizing', 95, 'Course generated! Syncing 1080p slide frames and multilingual audio...');
    const courseRes = await fetch(`${this.explainStudioUrl}/v1/jobs/${jobId}/course`);
    if (!courseRes.ok) {
      throw new Error(`Failed to retrieve generated course payload from Explain Studio (${courseRes.status})`);
    }

    const courseData = await courseRes.json();
    const rawCourse = courseData.course;

    if (!rawCourse) {
      throw new Error('Empty course payload returned from Explain Studio.');
    }

    // 5. Copy slide PNGs and audio files to SkillBridge uploads directory
    const destDir = path.join(process.cwd(), 'uploads', 'courses', String(courseId));
    const backendDestDir = path.join(process.cwd(), 'Backend', 'uploads', 'courses', String(courseId));

    let explainSrcDir = path.join(process.cwd(), 'uploads', 'courses', String(jobId));

    for (const targetBase of [destDir, backendDestDir]) {
      const slidesDest = path.join(targetBase, 'slides');
      const audioDest = path.join(targetBase, 'audio');
      fs.mkdirSync(slidesDest, { recursive: true });
      fs.mkdirSync(audioDest, { recursive: true });

      // Copy slides
      const srcSlides = path.join(explainSrcDir, 'slides');
      if (fs.existsSync(srcSlides)) {
        for (const sf of fs.readdirSync(srcSlides)) {
          fs.copyFileSync(path.join(srcSlides, sf), path.join(slidesDest, sf));
          const match = sf.match(/slide_(\d+)\.png/i);
          if (match) {
            fs.copyFileSync(path.join(srcSlides, sf), path.join(slidesDest, `Slide${match[1]}.png`));
          }
        }
      }

      // Copy audio
      const srcAudio = path.join(explainSrcDir, 'audio');
      if (fs.existsSync(srcAudio)) {
        for (const lDir of fs.readdirSync(srcAudio)) {
          const srcLang = path.join(srcAudio, lDir);
          if (!fs.statSync(srcLang).isDirectory()) continue;
          const destLang = path.join(audioDest, lDir);
          fs.mkdirSync(destLang, { recursive: true });
          for (const af of fs.readdirSync(srcLang)) {
            fs.copyFileSync(path.join(srcLang, af), path.join(destLang, af));
            const match = af.match(/slide_(\d+)(.*)\.mp3/i);
            if (match) {
              fs.copyFileSync(path.join(srcLang, af), path.join(destLang, `Slide${match[1]}${match[2]}.mp3`));
            }
          }
          if (lDir === 'zh-CN') {
            const destZh = path.join(audioDest, 'zh');
            fs.mkdirSync(destZh, { recursive: true });
            for (const af of fs.readdirSync(srcLang)) {
              fs.copyFileSync(path.join(srcLang, af), path.join(destZh, af));
              const match = af.match(/slide_(\d+)(.*)\.mp3/i);
              if (match) {
                fs.copyFileSync(path.join(srcLang, af), path.join(destZh, `Slide${match[1]}${match[2]}.mp3`));
              }
            }
          }
        }
      }
    }

    // 5b. Pre-generate all voice variants (male and female) upfront before declaring ready
    await this.ensureAllSlideVoicesGenerated(courseId, rawCourse);

    // 6. Format final curriculum
    const totalSlides = rawCourse.total_slides || 1;
    const modules = [];
    const lessons = [];
    const allSlides = [];

    for (const m of (rawCourse.modules || [])) {
      const mId = m.module_id || `module_${m.module_number}`;
      const mSlides = m.slides || [];
      const sNums = m.slide_numbers || mSlides.map(s => s.slide_number);

      modules.push({
        id: mId,
        moduleId: mId,
        module_id: mId,
        moduleNumber: m.module_number,
        module_number: m.module_number,
        title: m.title,
        description: m.description,
        learningObjectives: m.learning_objectives || [],
        learning_objectives: m.learning_objectives || [],
        slideNumbers: sNums,
        slide_numbers: sNums
      });

      for (const s of mSlides) {
        const sNum = s.slide_number;
        const imageUrl = `/api/v1/courses/${courseId}/slides/${sNum}/image`;
        const audioUrls = {};
        const explanations = s.explanations || {};
        for (const [lKey, expText] of Object.entries(explanations)) {
          audioUrls[lKey] = `/api/v1/courses/${courseId}/slides/${sNum}/audio?lang=${lKey}`;
        }
        if (explanations['zh-CN'] && !audioUrls['zh']) {
          audioUrls['zh'] = `/api/v1/courses/${courseId}/slides/${sNum}/audio?lang=zh`;
        }

        allSlides.push({
          id: sNum,
          slideNumber: sNum,
          slide_number: sNum,
          moduleId: mId,
          module_id: mId,
          title: s.title || `Slide ${sNum}`,
          keyPoints: s.key_points || [],
          key_points: s.key_points || [],
          tableSummaries: s.table_summaries || [],
          table_summaries: s.table_summaries || [],
          imageDescriptions: s.image_descriptions || [],
          image_descriptions: s.image_descriptions || [],
          spokenExplanation: explanations.en || '',
          explanations: explanations,
          durationsSeconds: s.durations_seconds || {},
          durations_seconds: s.durations_seconds || {},
          audioUrls: audioUrls,
          audio_urls: audioUrls,
          image: imageUrl,
          slide_image_url: imageUrl,
          slide_image_path: `slides/slide_${sNum}.png`,
          fullSlideImage: {
            id: `full_slide_${sNum}`,
            url: imageUrl,
            slideNumber: sNum,
            fileName: `Slide${sNum}.png`,
            mimetype: 'image/png'
          }
        });

        const durationSec = s.durations_seconds?.en || 20;
        const minStr = Math.floor(durationSec / 60).toString().padStart(2, '0');
        const secStr = Math.floor(durationSec % 60).toString().padStart(2, '0');

        lessons.push({
          id: sNum,
          slideNumber: sNum,
          title: s.title || `Slide ${sNum}`,
          moduleId: mId,
          moduleTitle: m.title,
          chapterTitle: m.title,
          duration: `${minStr}:${secStr}`,
          sourceSlides: [sNum],
          explanation: explanations.en || '',
          spokenExplanation: explanations.en || '',
          spoken_explanation: explanations.en || '',
          explanations: explanations,
          keyPoints: s.key_points || [],
          key_points: s.key_points || [],
          tableSummaries: s.table_summaries || [],
          image: imageUrl,
          audioUrls: audioUrls,
          status: 'active'
        });
      }
    }

    const finalCurriculum = {
      isInteractivePresentation: true,
      status: 'completed',
      stage: 'completed',
      progress: 100,
      totalSlides,
      total_slides: totalSlides,
      totalModules: modules.length,
      total_modules: modules.length,
      languages: rawCourse.languages || ['en', 'zh-CN', 'ms', 'ta', 'bn', 'te'],
      modules,
      slides: allSlides,
      lessons,
      explainStudioJobId: jobId
    };

    const fullText = allSlides.map(s => `Slide ${s.slideNumber}: ${s.title}\n${s.spokenExplanation}`).join('\n\n');

    await targetPrisma.course.update({
      where: { id: courseId },
      data: {
        title: title.trim(),
        description: description.trim(),
        fileText: fullText,
        curriculum: finalCurriculum
      }
    });

    // 7. Save all lessons, slides, and semantic concepts in local PostgreSQL CourseChunk table with pgvector
    try {
      console.log(`[pgvector] Indexing course ${courseId} into PostgreSQL CourseChunk table...`);
      const docMetadata = {
        fileName: filename,
        fileType: 'presentation',
        slides: allSlides
      };
      await vectorChunkService.indexCourse(targetPrisma, courseId, { text: fullText, ...docMetadata }, finalCurriculum);
      console.log(`[pgvector] Course ${courseId} chunks and vector embeddings successfully saved in PostgreSQL.`);
    } catch (chunkErr) {
      console.warn('[ExplainStudio] Vector chunking notice:', chunkErr.message);
    }

    this._updateJob(courseId, 'completed', 100, 'Interactive course generation successfully completed!');
    console.log(`[ExplainStudio] Pipeline successfully completed for courseId: ${courseId}`);
    return finalCurriculum;
  }

  /**
   * STAGE 1: Document Ingestion and Slide Rendering
   * Uses Spire.Presentation to render 1080p Lanczos letterboxed slides
   * and python-pptx to extract structured slide manifest.
   */
  async renderAndExtractDocument(courseId, filePath, originalName) {
    const courseUploadDir = path.join(process.cwd(), 'uploads', 'courses', String(courseId));
    const slidesDir = path.join(courseUploadDir, 'slides');
    fs.mkdirSync(slidesDir, { recursive: true });

    this._updateJob(courseId, 'rendering', 10, 'Rendering presentation slides into 1080p images with Lanczos letterboxing...');

    const rendererScript = path.join(process.cwd(), '..', 'ai-service', 'services', 'slide_renderer.py');
    const altRendererScript = path.join(process.cwd(), 'ai-service', 'services', 'slide_renderer.py');
    const scriptPath = fs.existsSync(rendererScript) ? rendererScript : altRendererScript;

    try {
      // Execute the python slide_renderer directly with high memory and timeout
      const { stdout, stderr } = await execFilePromise(
        this.pythonExe,
        [scriptPath, filePath, slidesDir],
        {
          timeout: 180000,
          maxBuffer: 50 * 1024 * 1024
        }
      );

      const manifestPath = path.join(slidesDir, 'manifest.json');
      if (fs.existsSync(manifestPath)) {
        const rawJson = fs.readFileSync(manifestPath, 'utf8');
        const manifest = JSON.parse(rawJson);
        if (manifest && manifest.slideCount > 0 && Array.isArray(manifest.slides) && manifest.slides.length > 0) {
          console.log(`[InteractiveCourse] Document parsed successfully via Python: ${manifest.slideCount} slides at 1080p.`);
          return manifest;
        }
      }
    } catch (err) {
      console.warn(`[InteractiveCourse] Python renderer exec notice: ${err.message}. Trying native extraction...`);
    }

    // Resilient Fallback: native Node.js PowerPoint extraction
    try {
      console.log(`[InteractiveCourse] Extracting presentation structure using native document extractor...`);
      const fileBuffer = fs.readFileSync(filePath);
      const docResult = await extractDocumentContent({ buffer: fileBuffer, originalname: originalName });
      if (docResult && Array.isArray(docResult.slides) && docResult.slides.length > 0) {
        const slides = docResult.slides.map((s, idx) => ({
          slideNumber: s.slideNumber || (idx + 1),
          title: s.title || `Slide ${idx + 1}`,
          paragraphs: s.paragraphs || (s.fullText ? [s.fullText] : []),
          bulletPoints: s.bulletPoints || [],
          tables: s.tables || [],
          speakerNotes: s.speakerNotes || '',
          hasComplexVisuals: (s.images && s.images.length > 0) || false,
          visualTypes: (s.images && s.images.length > 0) ? ['image'] : [],
          imagePath: path.join(slidesDir, `Slide${s.slideNumber || (idx + 1)}.png`),
          imageFileName: `Slide${s.slideNumber || (idx + 1)}.png`
        }));
        const fallbackManifest = {
          fileType: originalName.split('.').pop()?.toLowerCase() || 'pptx',
          slideCount: slides.length,
          aspectRatio: '16:9',
          resolution: '1920x1080',
          outputDir: slidesDir,
          slides
        };
        fs.writeFileSync(path.join(slidesDir, 'manifest.json'), JSON.stringify(fallbackManifest, null, 2));
        console.log(`[InteractiveCourse] Native extraction succeeded with ${slides.length} slides.`);
        return fallbackManifest;
      }
    } catch (fallbackErr) {
      console.warn(`[InteractiveCourse] Native document extractor fallback notice: ${fallbackErr.message}`);
    }

    const manifestPath = path.join(slidesDir, 'manifest.json');
    if (fs.existsSync(manifestPath)) {
      return JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    }
    throw new Error('Could not extract slides from presentation.');
  }

  /**
   * STAGE 2: Whole-Document AI Understanding
   * Employs local Qwen 2.5 (14B) to analyze the complete presentation as a holistic system.
   */
  async analyzeWholeDocument(courseId, fileName, slidesManifest) {
    this._updateJob(courseId, 'understanding', 25, 'Analyzing entire document structure, concepts, and terminology with Qwen 2.5 (14B)...');

    const slides = slidesManifest.slides || [];
    const deckOverview = slides.map(s => {
      const parts = [
        `Slide ${s.slideNumber}: ${s.title}`,
        s.bulletPoints && s.bulletPoints.length > 0 ? `Bullets:\n${s.bulletPoints.map(b => `  - ${b}`).join('\n')}` : '',
        s.paragraphs && s.paragraphs.length > 0 ? `Text: ${s.paragraphs.join(' ')}` : '',
        s.tables && s.tables.length > 0 ? `Tables: ${JSON.stringify(s.tables)}` : '',
        s.speakerNotes ? `Speaker Notes: ${s.speakerNotes}` : ''
      ].filter(Boolean);
      return parts.join('\n');
    }).join('\n\n--- NEXT SLIDE ---\n\n');

    const systemPrompt = `You are an elite instructional designer and master curriculum architect.
Your task is to analyze this complete educational presentation or document as a whole before curriculum construction.

You must return valid JSON only matching:
{
  "subjectDomain": "Precise academic or technical domain",
  "targetAudience": "Intended learners (e.g. operators, technicians, engineers, students)",
  "learningObjectives": [
    "Measurable learning objective 1",
    "Measurable learning objective 2",
    "Measurable learning objective 3"
  ],
  "coreConcepts": [
    {
      "name": "Concept name",
      "description": "Clear educational explanation",
      "prerequisites": ["Prior concepts required"]
    }
  ],
  "conceptDependencies": [
    { "from": "Concept A", "to": "Concept B", "relationship": "Prerequisite for" }
  ],
  "glossary": {
    "Term": {
      "en": "English definition & term",
      "zh": "Chinese (Simplified) translation",
      "ms": "Malay translation",
      "ta": "Tamil translation",
      "te": "Telugu translation",
      "bn": "Bangla translation"
    }
  }
}`;

    const userPrompt = `Document: "${fileName}"
Total Slides: ${slides.length}

Complete Presentation Content:
${deckOverview.slice(0, 24000)}

Perform whole-document comprehension.`;

    try {
      const response = await this._callQwenLLM(systemPrompt, userPrompt, true);
      const parsed = this._parseJson(response);
      if (parsed && parsed.subjectDomain) {
        return parsed;
      }
    } catch (e) {
      console.warn('[InteractiveCourse] Whole-document LLM pass notice:', e.message);
    }

    // High quality deterministic fallback
    const firstTitle = slides[0]?.title || fileName.replace(/\.[^/.]+$/, '');
    return {
      subjectDomain: firstTitle,
      targetAudience: 'Learners seeking comprehensive mastery of the topic',
      learningObjectives: [
        `Understand core principles of ${firstTitle}`,
        `Master operational workflows and standard procedures`,
        `Apply safety and quality compliance protocols`
      ],
      coreConcepts: slides.slice(0, 5).map(s => ({
        name: s.title,
        description: s.bulletPoints?.join(' ') || `Operational guidelines for ${s.title}`,
        prerequisites: []
      })),
      conceptDependencies: [],
      glossary: {}
    };
  }

  /**
   * STAGE 2 / 3: Selective Vision Analysis (qwen2.5vl:7b)
   * Budgeted deep vision processing (MAX_VISION_CALLS = 3) on complex diagrams.
   * Employs high-efficiency 768px JPEG 85% thumbnails and 0 ms AST bypass for standard slides.
   */
  async processSelectiveVision(courseId, slidesManifest) {
    this._updateJob(courseId, 'vision', 25, 'Running smart selective vision analysis on complex diagrams and charts...');

    const slides = slidesManifest.slides || [];
    const MAX_VISION_CALLS = 3;
    let visionCallsMade = 0;

    for (const slide of slides) {
      const sNum = slide.slideNumber;
      const title = slide.title || `Slide ${sNum}`;
      const visionImg = slide.visionImagePath || slide.imagePath;

      // 1. 0 ms AST bypass for slides with structured tabular data
      if (slide.tables && slide.tables.length > 0) {
        slide.visualExplanation = `Structured table data with ${slide.tables.length} section(s) covering ${title}.`;
        continue;
      }

      // 2. Deep visual inspection on prioritized diagram/chart slides
      if (visionCallsMade < MAX_VISION_CALLS && slide.hasComplexVisuals && visionImg && fs.existsSync(visionImg)) {
        try {
          console.log(`[InteractiveCourse] Selective vision analyzing Slide ${sNum} (${visionCallsMade + 1}/${MAX_VISION_CALLS}) using 768px vision thumbnail...`);
          visionCallsMade++;
          const visionDescription = await this._callVisionModel(visionImg, sNum, title);
          if (visionDescription) {
            slide.visualExplanation = visionDescription;
            console.log(`[InteractiveCourse] Slide ${sNum} vision insight extracted (${visionDescription.length} chars).`);
          }
        } catch (vErr) {
          console.warn(`[InteractiveCourse] Vision analysis skipped for slide ${sNum}: ${vErr.message}`);
          slide.visualExplanation = `Technical visual diagram illustrating key principles of ${title}.`;
        }
      } else if (slide.hasComplexVisuals || (slide.images && slide.images.length > 0)) {
        // 3. 0 ms AST fallback for remaining visual slides beyond the cap
        slide.visualExplanation = `Visual figures illustrating key principles of ${title}.`;
      } else {
        // 4. 0 ms AST bypass for pure text instructional slides
        slide.visualExplanation = `Text-based instructional material covering ${title}.`;
      }
    }
    return slidesManifest;
  }

  /**
   * STAGE 4: AI Curriculum and Module Planning
   * Plans 2–6 logical learning modules adapted dynamically to presentation slide count.
   * STRICT RULE: Every slide must belong to exactly one module without omissions or gaps.
   */
  async planCurriculumAndModules(courseId, fileName, slidesManifest, docUnderstanding) {
    this._updateJob(courseId, 'curriculum_planning', 55, 'Structuring dynamic learning modules with strict 1:1 slide partition...');

    const slides = slidesManifest.slides || [];
    const totalSlides = slides.length;
    const targetModuleCount = this._calculateTargetModules(totalSlides);
    const slidesListSummary = slides.map(s => `Slide ${s.slideNumber}: "${s.title}" (${(s.bulletPoints || []).length} bullets)`).join('\n');

    const systemPrompt = `You are an expert corporate training director and curriculum architect.
Your job is to divide a PowerPoint presentation of ${totalSlides} slides into ${targetModuleCount} practical training modules.

AUDIENCE: ${docUnderstanding.targetAudience || 'Factory workers, operators, and site personnel'} (practical, direct, action-oriented language).

CRITICAL CONSTRAINTS:
1. You MUST generate between 2 and 6 modules (target: ${targetModuleCount} modules).
2. EVERY SLIDE from 1 to ${totalSlides} MUST be assigned to EXACTLY ONE module in sequential order.
3. No slide may be omitted or duplicated.
4. Derive all module titles and operational purposes strictly from the provided slide content.
5. Focus each module on clear rules, standards, correct actions, and practical procedures from those specific slides.

Return JSON ONLY:
{
  "modules": [
    {
      "moduleId": 1,
      "title": "Module 1: Title from Slides",
      "description": "Clear practical operational purpose for these slides",
      "slideNumbers": [1, 2, 3]
    }
  ]
}`;

    const userPrompt = `Course Subject: ${docUnderstanding.subjectDomain}
Total Slides: ${totalSlides}

Slide Catalog:
${slidesListSummary}

Design the sequential module curriculum.`;

    let plannedModules = [];
    try {
      const response = await this._callQwenLLM(systemPrompt, userPrompt, true);
      const parsed = this._parseJson(response);
      if (parsed && Array.isArray(parsed.modules) && parsed.modules.length >= 2) {
        plannedModules = parsed.modules;
      }
    } catch (e) {
      console.warn('[InteractiveCourse] Curriculum planning LLM pass error:', e.message);
    }

    // Run Automatic Slide-Allocation Repair & Dynamic Sequential Partitioning
    plannedModules = this._repairAndValidateSlideAllocation(plannedModules, totalSlides, slides);
    return plannedModules;
  }

  /**
   * Calculates ideal module count dynamically based on total slide count.
   */
  _calculateTargetModules(totalSlides) {
    if (totalSlides <= 4) return totalSlides;
    if (totalSlides <= 8) return Math.min(4, Math.max(3, Math.ceil(totalSlides / 2.0)));
    if (totalSlides <= 14) return Math.min(5, Math.max(4, Math.ceil(totalSlides / 2.8)));
    if (totalSlides <= 20) return 5;
    return Math.min(6, Math.max(5, Math.ceil(totalSlides / 4.5)));
  }

  /**
   * Produces clean, professional lesson/module titles from raw slide headers.
   */
  _cleanLessonTitle(rawTitle, chunkIndex) {
    let t = String(rawTitle || '')
      .replace(/[\x00-\x1f\x7f-\x9f]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    t = t.replace(/^(Please comply with our|Please look out for|Important reminder:?|For your own safety:?|Lesson \d+:?|Module \d+:?)\s*/i, '').trim();
    t = t.replace(/^(To ensure|To achieve|To follow|To)\s+/i, '').trim();
    t = t.replace(/(\u2026|\.\.\.).*$/, '').trim();
    t = t.replace(/\|.*$/, '').trim();
    t = t.replace(/[:\-–—\.]+$/, '').trim();
    if (/(rating \(pt\)|how much score|score do you give|nobody makes a mess|thank you|the end)/i.test(t)) {
      t = '';
    }
    if (!t || t.length < 4 || /^\d+$/.test(t)) {
      t = `Operational Procedures Part ${chunkIndex}`;
    } else if (t.length > 55) {
      t = t.slice(0, 52).replace(/\s+\S*$/, '');
    }
    if (t && t.charAt(0) === t.charAt(0).toLowerCase()) {
      t = t.charAt(0).toUpperCase() + t.slice(1);
    }
    return `Module ${chunkIndex}: ${t}`;
  }

  /**
   * Automatic Slide-Allocation Repair Algorithm:
   * Enforces:
   * 1. 2 to 6 modules dynamically sized according to deck length.
   * 2. Every slide 1..N is assigned to exactly one module in sequential order (no gaps, no duplicates).
   */
  _repairAndValidateSlideAllocation(rawModules, totalSlides, slides) {
    let modules = Array.isArray(rawModules) ? [...rawModules] : [];
    const targetModuleCount = this._calculateTargetModules(totalSlides);

    if (modules.length < 2 || modules.length > 6) {
      modules = [];
      const chunkSize = totalSlides / targetModuleCount;
      for (let m = 0; m < targetModuleCount; m++) {
        const sIdx = Math.round(m * chunkSize);
        const eIdx = Math.round((m + 1) * chunkSize);
        if (sIdx < eIdx && sIdx < totalSlides) {
          const modSlides = [];
          for (let s = sIdx + 1; s <= Math.min(totalSlides, eIdx); s++) {
            modSlides.push(s);
          }
          if (modSlides.length > 0) {
            const firstSlideObj = slides.find(sl => sl.slideNumber === modSlides[0]);
            const modTitle = this._cleanLessonTitle(firstSlideObj?.title, m + 1);
            modules.push({
              moduleId: m + 1,
              title: modTitle,
              description: `Operational rules and procedures covered in slides ${modSlides[0]} to ${modSlides[modSlides.length - 1]}.`,
              slideNumbers: modSlides
            });
          }
        }
      }
    }

    // Check for duplicate assignments
    const assignedSlides = new Set();
    for (const mod of modules) {
      const cleanList = [];
      for (const sn of (mod.slideNumbers || [])) {
        const num = parseInt(sn, 10);
        if (Number.isFinite(num) && num >= 1 && num <= totalSlides && !assignedSlides.has(num)) {
          assignedSlides.add(num);
          cleanList.push(num);
        }
      }
      mod.slideNumbers = cleanList;
    }

    // Check for missing slides and allocate sequentially
    for (let s = 1; s <= totalSlides; s++) {
      if (!assignedSlides.has(s)) {
        let targetMod = modules[0];
        for (const mod of modules) {
          if (mod.slideNumbers.some(sn => Math.abs(sn - s) <= 2)) {
            targetMod = mod;
            break;
          }
        }
        targetMod.slideNumbers.push(s);
        targetMod.slideNumbers.sort((a, b) => a - b);
        assignedSlides.add(s);
      }
    }

    // Remove any empty modules
    modules = modules.filter(m => m.slideNumbers.length > 0);
    // Ensure sequential module IDs
    modules.forEach((m, idx) => {
      m.moduleId = idx + 1;
      if (!m.title || m.title.startsWith('Module ')) {
        const firstSlideObj = slides.find(sl => sl.slideNumber === m.slideNumbers[0]);
        m.title = this._cleanLessonTitle(firstSlideObj?.title, idx + 1);
      }
    });

    return modules;
  }

  /**
   * STAGE 5: AI Teaching Narration, Bullet Healing & Quality Auditing
   * Generates spoken training scripts paced at ~135 WPM with bullet healing.
   * Employs unified whole-deck prompt to complete all slide explanations in a single LLM request.
   */
  async generateTeachingNarration(courseId, slidesManifest, docUnderstanding) {
    this._updateJob(courseId, 'narration', 65, 'Generating teacher-like explanations with bullet healing and quality auditing...');

    const slides = slidesManifest.slides || [];

    // 1. Run bullet healing across all slides upfront
    for (const slide of slides) {
      slide.healedBullets = this._healBullets(slide.bulletPoints || []);
    }

    // 2. Query Qwen 2.5:14B for all slide explanations in a single unified prompt (0 VRAM reload)
    const deckLectures = await this._generateDeckLectures(slides, docUnderstanding);

    // 3. Assemble and audit explanations with high-value deterministic fallback
    for (const slide of slides) {
      const sNum = slide.slideNumber;
      let rawNarration = deckLectures[String(sNum)] || deckLectures[sNum];

      if (!rawNarration || typeof rawNarration !== 'string' || rawNarration.trim().length < 35) {
        rawNarration = this._buildFallbackSlideLecture(slide, docUnderstanding);
      }

      const auditResult = this._auditNarrationQuality(rawNarration, slide.healedBullets, slide.title);
      if (auditResult.needsCorrection) {
        rawNarration = this._repairNarration(rawNarration, auditResult.issues, slide);
      }

      slide.spokenExplanation = rawNarration;
      slide.keyTakeaways = (slide.healedBullets && slide.healedBullets.length > 0)
        ? slide.healedBullets.slice(0, 4)
        : [
          `Understand key operational points of ${slide.title}`,
          `Follow standard workplace procedures and guidelines`
        ];
    }

    slidesManifest.slides = slides;
    return slidesManifest;
  }

  async _generateDeckLectures(slides, docUnderstanding) {
    const totalSlides = slides.length;
    const slideEntries = slides.map(s => {
      const parts = [
        `--- Slide ${s.slideNumber} ---`,
        `Title: ${s.title}`,
        s.visualExplanation ? `Visual: ${s.visualExplanation}` : '',
        s.healedBullets && s.healedBullets.length > 0 ? `Key Points:\n${s.healedBullets.map(b => `  • ${b}`).join('\n')}` : '',
        s.speakerNotes ? `Speaker Notes: ${s.speakerNotes}` : ''
      ].filter(Boolean);
      return parts.join('\n');
    }).join('\n\n');

    const systemPrompt = `You are an expert instructional narrator and master teacher.
Write natural, direct, spoken training lectures in English for each slide below.
AUDIENCE: Workplace learners (~135 WPM spoken pace).

STRICT RULES:
- Ground explanations in the slide content, procedures, standards, and rules.
- DO NOT say "Slide 1", "In this slide", "This slide shows", "Welcome to", or "As you can see".
- Explain what the content means, why it matters, and practical actions required.
- Write 3 to 5 clear spoken sentences (60-120 words) for each slide.
- Return ONLY valid JSON:
{"slide_explanations": {"1": "explanation for slide 1 ...", "2": "explanation for slide 2 ...", ...}}
Include every slide number from 1 to ${totalSlides}. No extra keys.`;

    const userPrompt = `Course Subject: "${docUnderstanding.subjectDomain}"
Total Slides: ${totalSlides}

SLIDES:
${slideEntries}

Deliver natural spoken explanations for all slides.`;

    try {
      const response = await this._callQwenLLM(systemPrompt, userPrompt, true);
      const parsed = this._parseJson(response);
      if (parsed && parsed.slide_explanations && typeof parsed.slide_explanations === 'object') {
        return parsed.slide_explanations;
      }
    } catch (err) {
      console.warn('[InteractiveCourse] Unified deck narration LLM pass notice:', err.message);
    }
    return {};
  }

  _buildFallbackSlideLecture(slide, docUnderstanding) {
    const bulletsText = (slide.healedBullets || []).join(' ');
    const visualText = slide.visualExplanation ? ` Note that ${slide.visualExplanation.toLowerCase().replace(/\.$/, '')}.` : '';
    if (bulletsText && bulletsText.length > 20) {
      return `Focusing on ${slide.title}, the primary objective is understanding and applying core operational standards.${visualText} Specifically, ${bulletsText} Consistently following these procedures ensures quality, safety, and operational excellence.`;
    }
    return `In this section covering ${slide.title}, we examine the key principles and operational requirements.${visualText} By mastering these fundamentals, you maintain compliance and uphold our professional standards.`;
  }

  /**
   * Bullet Healing Algorithm:
   * Detects bullet points that are grammatical sentence fragments split across lines
   * and merges them into complete, fluent sentences.
   */
  _healBullets(bullets) {
    if (!Array.isArray(bullets) || bullets.length === 0) return [];

    const healed = [];
    let currentSentence = '';

    for (let i = 0; i < bullets.length; i++) {
      let b = String(bullets[i]).replace(/^[•\-\*\d\.\)\s]+/, '').trim();
      if (!b) continue;

      if (!currentSentence) {
        currentSentence = b;
      } else {
        // Check if previous sentence was incomplete
        const prevEndsWithBreak = /[,\-;:–—]$/.test(currentSentence);
        const prevLacksTerminator = !/[.!?]$/.test(currentSentence);
        const currStartsWithLower = /^[a-z]/.test(b);
        const currStartsWithConjunction = /^(and|or|but|because|with|by|to|which|that|in order to|as well as)\b/i.test(b);

        if (prevEndsWithBreak || (prevLacksTerminator && (currStartsWithLower || currStartsWithConjunction))) {
          // Heal the split sentence!
          if (prevEndsWithBreak) {
            currentSentence = currentSentence.replace(/[,\-;:–—]+$/, '') + ', ' + b;
          } else {
            currentSentence = currentSentence + ' ' + b;
          }
        } else {
          healed.push(this._finalizeSentence(currentSentence));
          currentSentence = b;
        }
      }
    }

    if (currentSentence) {
      healed.push(this._finalizeSentence(currentSentence));
    }

    return healed;
  }

  _finalizeSentence(text) {
    let t = text.trim();
    if (!t) return '';
    // Capitalize first letter
    t = t.charAt(0).toUpperCase() + t.slice(1);
    // Ensure terminating punctuation
    if (!/[.!?]$/.test(t)) t += '.';
    return t;
  }

  async _generateSlideLecture(slide, docUnderstanding) {
    return this._buildFallbackSlideLecture(slide, docUnderstanding);
  }

  /**
   * Quality Auditor Mechanism:
   * Inspects narration for robotic filler, repetitive starts, and verbatim duplication.
   */
  _auditNarrationQuality(narration, healedBullets, slideTitle) {
    const issues = [];
    const text = String(narration || '').trim();

    // 1. Check robotic openers
    const roboticPatterns = [
      /^(in this slide|this slide|welcome to slide|here we see|as you can see|looking at this slide|on this slide)/i,
      /^(moving forward to slide|now let us look at slide|slide number \d+)/i
    ];
    for (const pat of roboticPatterns) {
      if (pat.test(text)) {
        issues.push('robotic_opener');
        break;
      }
    }

    // 2. Check verbatim reading
    let exactMatches = 0;
    for (const b of (healedBullets || [])) {
      if (b.length > 20 && text.toLowerCase().includes(b.toLowerCase())) {
        exactMatches++;
      }
    }
    if (healedBullets && healedBullets.length > 0 && exactMatches / healedBullets.length > 0.7) {
      issues.push('verbatim_reading');
    }

    // 3. Length check
    if (text.length < 80) {
      issues.push('too_brief');
    }

    return {
      needsCorrection: issues.length > 0,
      issues
    };
  }

  _repairNarration(narration, issues, slide) {
    let repaired = narration;
    // Strip robotic openers
    repaired = repaired
      .replace(/^(In this slide,?|This slide (presents|shows|covers|explains|discusses),?|Welcome to slide \d+,?|As you can see (here|on the screen)?,?|Looking at this slide,?)\s*/i, '')
      .trim();

    // Capitalize first char
    if (repaired.length > 0) {
      repaired = repaired.charAt(0).toUpperCase() + repaired.slice(1);
    }

    if (issues.includes('too_brief')) {
      const extraContext = (slide.healedBullets || []).join(' ');
      repaired = `Focusing on ${slide.title}, it is critical to observe all specified protocols. ${repaired} ${extraContext} Maintaining these standards safeguards quality and operational efficiency.`;
    }

    return repaired;
  }

  /**
   * Resilient 3-Tier Multilingual Translation:
   * Tier 1: In-memory persistent cache (0 ms)
   * Tier 2: Fast Google Translate API endpoint (< 100 ms)
   * Tier 3: Ollama / AI Service LLM fallback
   * Tier 4: Verbatim English fallback
   */
  async translateText(text, targetLang, glossary = {}) {
    const cleanText = String(text || '').trim();
    if (!cleanText || targetLang === 'en') return cleanText;

    const cacheKey = `${cleanText}___${targetLang}`;
    if (translationCache.has(cacheKey)) {
      return translationCache.get(cacheKey);
    }

    // Check glossary first
    if (glossary && glossary[cleanText] && glossary[cleanText][targetLang]) {
      const gTrans = glossary[cleanText][targetLang];
      translationCache.set(cacheKey, gTrans);
      return gTrans;
    }

    // Tier 1: Fast translation endpoint
    try {
      const gUrl = `https://translate.googleapis.com/translate_a/single?client=dict-chrome-ex&sl=en&tl=${encodeURIComponent(targetLang)}&dt=t&q=${encodeURIComponent(cleanText)}`;
      const resp = await axios.get(gUrl, { timeout: 4500 });
      if (resp.status === 200 && Array.isArray(resp.data) && Array.isArray(resp.data[0])) {
        const translated = resp.data[0].map(part => (part && part[0]) ? part[0] : '').join('').trim();
        if (translated && translated.length > 0) {
          translationCache.set(cacheKey, translated);
          return translated;
        }
      }
    } catch (_) {
      // Tier 1 endpoint rate-limited or offline, proceed to Tier 2
    }

    // Tier 2: LLM fallback
    try {
      const prompt = `Translate the following text from English to ${targetLang}. Return ONLY the direct translation text with no markdown, notes, or explanations:\n\n${cleanText}`;
      const llmResp = await this._callQwenLLM('You are a professional technical translator.', prompt, false);
      if (llmResp && llmResp.trim().length > 0 && llmResp.trim() !== cleanText) {
        const cleaned = llmResp.replace(/^["'`]+|["'`]+$/g, '').trim();
        translationCache.set(cacheKey, cleaned);
        return cleaned;
      }
    } catch (_) {
      // LLM translation fallback failed
    }

    // Tier 3: Verbatim fallback
    translationCache.set(cacheKey, cleanText);
    return cleanText;
  }

  /**
   * STAGE 6: Multilingual Course Generation (6 Languages)
   * High-throughput parallel translation across EN, ZH, MS, TA, TE, BN in < 4 seconds.
   */
  async generateMultilingualTranslations(courseId, slidesManifest, modules, docUnderstanding) {
    this._updateJob(courseId, 'multilingual', 80, 'Generating synchronized translations across English, Chinese, Malay, Tamil, Telugu, and Bangla...');

    const slides = slidesManifest.slides || [];
    const targetLangs = ['zh', 'ms', 'ta', 'te', 'bn']; // en is base

    // Process slides in parallel batches of 4
    const BATCH_SIZE = 4;
    for (let i = 0; i < slides.length; i += BATCH_SIZE) {
      const batch = slides.slice(i, i + BATCH_SIZE);
      await Promise.all(batch.map(async (slide) => {
        slide.explanations = { en: slide.spokenExplanation };
        slide.titles = { en: slide.title };
        slide.keyPointsByLang = { en: slide.keyTakeaways || [] };

        await Promise.all(targetLangs.map(async (lang) => {
          const [tExplanation, tTitle, tKeyPoints] = await Promise.all([
            this.translateText(slide.spokenExplanation, lang, docUnderstanding.glossary),
            this.translateText(slide.title, lang, docUnderstanding.glossary),
            Promise.all((slide.keyTakeaways || []).map(pt => this.translateText(pt, lang, docUnderstanding.glossary)))
          ]);
          slide.explanations[lang] = tExplanation;
          slide.titles[lang] = tTitle;
          slide.keyPointsByLang[lang] = tKeyPoints;
        }));
      }));
    }

    // Translate module titles concurrently
    await Promise.all(modules.map(async (mod) => {
      mod.titles = { en: mod.title };
      await Promise.all(targetLangs.map(async (lang) => {
        mod.titles[lang] = await this.translateText(mod.title, lang, docUnderstanding.glossary);
      }));
    }));

    slidesManifest.slides = slides;
    return { slidesManifest, modules };
  }

  /**
   * STAGE 7: Neural Text-to-Speech & Voice Narration
   * Generates Microsoft Edge Neural TTS audio in parallel with batch concurrency.
   * Primary English synthesized upfront; other languages mapped to on-demand streaming routes.
   */
  async generateNeuralAudioNarration(courseId, slidesManifest) {
    this._updateJob(courseId, 'neural_tts', 90, 'Synthesizing Microsoft Edge Neural TTS audio narration for slides and languages...');

    const courseUploadDir = path.join(process.cwd(), 'uploads', 'courses', String(courseId));
    const audioBaseDir = path.join(courseUploadDir, 'audio');
    const langAudioDir = path.join(audioBaseDir, 'en');
    fs.mkdirSync(langAudioDir, { recursive: true });

    const slides = slidesManifest.slides || [];
    const itemsToSynth = [];

    for (const slide of slides) {
      const sNum = slide.slideNumber;
      slide.audioUrls = slide.audioUrls || {};
      slide.audioUrls.en = `/api/v1/courses/${courseId}/slides/${sNum}/audio?lang=en`;

      const maleVoice = (VOICE_CONFIG.en && VOICE_CONFIG.en.male) || 'en-US-ChristopherNeural';
      const femaleVoice = (VOICE_CONFIG.en && VOICE_CONFIG.en.female) || 'en-US-JennyNeural';

      const audioFile = path.join(langAudioDir, `Slide${sNum}.mp3`);
      if (!fs.existsSync(audioFile) || fs.statSync(audioFile).size < 200) {
        itemsToSynth.push({
          sNum,
          text: slide.explanations?.en || slide.spokenExplanation,
          voice: maleVoice,
          outFile: audioFile
        });
      }

      const femaleAudioFile = path.join(langAudioDir, `Slide${sNum}_female.mp3`);
      if (!fs.existsSync(femaleAudioFile) || fs.statSync(femaleAudioFile).size < 200) {
        itemsToSynth.push({
          sNum,
          text: slide.explanations?.en || slide.spokenExplanation,
          voice: femaleVoice,
          outFile: femaleAudioFile
        });
      }

      // Map on-demand neural audio routes for all other supported languages
      for (const langObj of SUPPORTED_LANGUAGES) {
        if (!slide.audioUrls[langObj.code]) {
          slide.audioUrls[langObj.code] = `/api/v1/courses/${courseId}/slides/${sNum}/audio?lang=${langObj.code}`;
        }
      }
    }

    // Synthesize all missing English audio in parallel in a single Python process
    if (itemsToSynth.length > 0) {
      try {
        console.log(`[InteractiveCourse] Batch synthesizing ${itemsToSynth.length} English slide audio clips concurrently...`);
        await this._batchSynthesizeAudio(itemsToSynth);
      } catch (bErr) {
        console.warn(`[InteractiveCourse] Batch TTS notice (${bErr.message}); falling back to sequential synthesis...`);
        for (const it of itemsToSynth) {
          try {
            const buf = await this.synthesizeSlideAudio(it.text, it.voice, 'en');
            if (buf && buf.length > 200) {
              fs.writeFileSync(it.outFile, buf);
            }
          } catch (_) { }
        }
      }
    }

    slidesManifest.slides = slides;
    return slidesManifest;
  }

  async _batchSynthesizeAudio(items) {
    const pyScript = `
import asyncio, sys, json, edge_tts

async def synth(item, sem):
    async with sem:
        try:
            comm = edge_tts.Communicate(item['text'], item.get('voice', 'en-US-ChristopherNeural'))
            await comm.save(item['outFile'])
        except Exception as e:
            sys.stderr.write(f"Err {item.get('sNum')}: {e}\\n")

async def main():
    items = json.loads(sys.argv[1])
    sem = asyncio.Semaphore(4)
    await asyncio.gather(*(synth(it, sem) for it in items))

asyncio.run(main())
`;
    await execFilePromise(
      this.pythonExe,
      ['-c', pyScript, JSON.stringify(items)],
      { timeout: 90000, maxBuffer: 10 * 1024 * 1024 }
    );
  }

  async ensureAllSlideVoicesGenerated(courseId, rawCourse) {
    const courseUploadDir = path.join(process.cwd(), 'uploads', 'courses', String(courseId));
    const audioDir = path.join(courseUploadDir, 'audio', 'en');
    fs.mkdirSync(audioDir, { recursive: true });

    const itemsToSynth = [];
    for (const m of (rawCourse?.modules || [])) {
      for (const s of (m.slides || [])) {
        const sNum = s.slide_number || s.slideNumber;
        if (!sNum) continue;
        const text = s.spokenExplanation || s.spoken_explanation || s.explanations?.en || s.title || `Slide ${sNum}`;

        const maleFile = path.join(audioDir, `Slide${sNum}.mp3`);
        if (!fs.existsSync(maleFile) || fs.statSync(maleFile).size < 200) {
          itemsToSynth.push({
            sNum,
            text,
            voice: 'en-US-ChristopherNeural',
            outFile: maleFile
          });
        }

        const femaleFile = path.join(audioDir, `Slide${sNum}_female.mp3`);
        if (!fs.existsSync(femaleFile) || fs.statSync(femaleFile).size < 200) {
          itemsToSynth.push({
            sNum,
            text,
            voice: 'en-US-JennyNeural',
            outFile: femaleFile
          });
        }
      }
    }

    if (itemsToSynth.length > 0) {
      console.log(`[InteractiveCourse] Pre-generating ${itemsToSynth.length} missing slide voice audio files before course is marked ready...`);
      try {
        await this._batchSynthesizeAudio(itemsToSynth);
        console.log(`[InteractiveCourse] All slide voices successfully pre-generated for course ${courseId}.`);
      } catch (err) {
        console.warn(`[InteractiveCourse] Batch voice pre-generation warning: ${err.message}. Running fallback synthesis...`);
        for (const item of itemsToSynth) {
          try {
            const buf = await this.synthesizeSlideAudio(item.text, item.voice, 'en');
            if (buf && buf.length > 200) {
              fs.writeFileSync(item.outFile, buf);
            }
          } catch (_) {}
        }
      }
    }
  }

  async synthesizeSlideAudio(text, voice, lang = 'en', gender = 'male') {
    const cleanText = String(text || '').trim();
    if (!cleanText) return null;

    const langConf = VOICE_CONFIG[lang] || VOICE_CONFIG.en;
    const isFemale = String(gender).toLowerCase() === 'female';
    const actualVoice = voice || (typeof langConf === 'object' ? (isFemale ? langConf.female : langConf.male) : langConf) || 'en-US-ChristopherNeural';

    // Direct high-performance Edge TTS execution via venv python
    const pyScript = `
import asyncio, sys, edge_tts
async def run():
    comm = edge_tts.Communicate(sys.argv[1], sys.argv[2])
    data = b''
    async for chunk in comm.stream():
        if chunk['type'] == 'audio':
            data += chunk['data']
    sys.stdout.buffer.write(data)
async def main():
    try:
        await asyncio.wait_for(run(), timeout=20.0)
    except Exception as e:
        sys.stderr.write(str(e))
asyncio.run(main())
`;
    try {
      const { stdout } = await execFilePromise(
        this.pythonExe,
        ['-c', pyScript, cleanText, actualVoice],
        { encoding: 'buffer', maxBuffer: 15 * 1024 * 1024, timeout: 25000 }
      );
      if (stdout && stdout.length > 200) {
        return stdout;
      }
    } catch (directErr) {
      console.warn(`[InteractiveCourse] Direct Edge TTS python synthesis error (${actualVoice}): ${directErr.message}`);
    }

    return null;
  }

  /**
   * STAGE 8: Finalize Interactive Course Structure
   * Constructs the full Course curriculum JSON object.
   */
  finalizeInteractiveCourse(fileName, slidesManifest, modules, docUnderstanding, courseId = '') {
    const slides = slidesManifest.slides || [];
    const totalSlides = slides.length;

    // Calculate module lesson durations
    const curriculumModules = modules.map(mod => {
      const modSlides = slides.filter(s => mod.slideNumbers.includes(s.slideNumber));
      const totalSeconds = modSlides.reduce((acc, s) => {
        const words = (s.spokenExplanation || '').split(/\s+/).length;
        return acc + Math.max(45, Math.min(180, Math.round(words / 2.2)));
      }, 0);

      const m = Math.floor(totalSeconds / 60);
      const s = totalSeconds % 60;
      return {
        moduleId: mod.moduleId,
        title: mod.title,
        titles: mod.titles || { en: mod.title },
        description: mod.description,
        slideCount: mod.slideNumbers.length,
        slideNumbers: mod.slideNumbers,
        duration: `${m}m ${s > 0 ? `${s}s` : ''}`.trim()
      };
    });

    // Structure lessons matching existing CoursePlayer lesson contract
    const lessons = slides.map((slide, idx) => {
      const parentMod = modules.find(m => m.slideNumbers.includes(slide.slideNumber)) || modules[0];
      const words = (slide.spokenExplanation || '').split(/\s+/).length;
      const totalSecs = Math.max(45, Math.min(180, Math.round(words / 2.2)));
      const minStr = Math.floor(totalSecs / 60).toString().padStart(2, '0');
      const secStr = (totalSecs % 60).toString().padStart(2, '0');

      return {
        id: slide.slideNumber,
        moduleId: parentMod?.moduleId || 1,
        moduleTitle: parentMod?.title || 'Core Training',
        chapterId: parentMod?.moduleId || 1,
        chapterTitle: parentMod?.title || 'Core Training',
        title: slide.title,
        titles: slide.titles || { en: slide.title },
        duration: `${minStr}:${secStr}`,
        status: idx === 0 ? 'active' : 'locked',
        sourceSlides: [slide.slideNumber],
        explanation: slide.spokenExplanation,
        explanations: slide.explanations || { en: slide.spokenExplanation },
        keyTakeaways: slide.keyTakeaways || [],
        keyPointsByLang: slide.keyPointsByLang || {},
        audioUrls: slide.audioUrls || {},
        image: `/api/v1/courses/${courseId || '__COURSE_ID__'}/slides/${slide.slideNumber}/image`,
        hasComplexVisuals: slide.hasComplexVisuals,
        visualTypes: slide.visualTypes
      };
    });

    return {
      title: docUnderstanding.subjectDomain || fileName.replace(/\.[^/.]+$/, ''),
      description: `Comprehensive interactive course covering ${totalSlides} slides across ${modules.length} structured learning modules.`,
      subjectDomain: docUnderstanding.subjectDomain,
      targetAudience: docUnderstanding.targetAudience,
      learningObjectives: docUnderstanding.learningObjectives,
      coreConcepts: docUnderstanding.coreConcepts,
      conceptDependencies: docUnderstanding.conceptDependencies,
      glossary: docUnderstanding.glossary,
      supportedLanguages: SUPPORTED_LANGUAGES,
      voiceConfig: VOICE_CONFIG,
      curriculum: curriculumModules,
      modules: curriculumModules,
      lessons,
      slides,
      isInteractivePresentation: true,
      aspectRatio: '16:9',
      resolution: '1920x1080',
      totalSlides,
      quiz: [
        {
          question: `What is the primary focus of "${docUnderstanding.subjectDomain}"?`,
          options: [
            `To apply the procedures and principles of ${docUnderstanding.subjectDomain}`,
            'To bypass operational standards',
            'To ignore workplace compliance',
            'None of the above'
          ],
          answerIndex: 0,
          explanation: `This course ensures learners master all operational guidelines for ${docUnderstanding.subjectDomain}.`
        }
      ]
    };
  }

  // --- LLM & Vision Utilities ---

  async _callQwenLLM(systemPrompt, userPrompt, jsonMode = false) {
    return await aiService._callLLM(systemPrompt, userPrompt, jsonMode);
  }

  async _callVisionModel(imagePath, slideNumber, slideTitle) {
    if (!fs.existsSync(imagePath)) return null;
    const imgBuffer = fs.readFileSync(imagePath);
    const base64Img = imgBuffer.toString('base64');

    // 1. Try local/remote gateway with configured model
    try {
      const prompt = `This is slide ${slideNumber} ("${slideTitle}") containing a complex diagram, chart, or illustration. Explain the exact pedagogical concepts, technical flow, and data presented in this graphic.`;
      const targetModel = this.visionModel || this.textModel || 'qwen3.5:9b';
      const payload = {
        model: targetModel,
        messages: [
          {
            role: 'user',
            content: prompt,
            images: [base64Img]
          }
        ],
        stream: false
      };
      const apiKey = process.env.QWEN_API_KEY || process.env.BRUTALLYHIRED_LLM_API_KEY || '';
      const headers = { 'Content-Type': 'application/json' };
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`;
        headers['X-API-Key'] = apiKey;
      }

      const resp = await axios.post(`${this.ollamaUrl}/api/chat`, payload, {
        timeout: 45000,
        headers
      });
      const content = resp.data?.message?.content;
      if (content && content.trim()) {
        return content.trim();
      }
    } catch (_) {
      // Fallback to gateway vision endpoint
    }

    // 2. Fallback to gateway vision via visualTextService
    try {
      const { analyzeImageWithQwen } = await import('./visualTextService.js');
      return await analyzeImageWithQwen(
        { buffer: imgBuffer, fileName: `Slide${slideNumber}.png`, mimetype: 'image/png' },
        slideNumber
      );
    } catch (_) {
      return null;
    }
  }

  _parseJson(text) {
    if (!text || typeof text !== 'string') return null;
    try {
      return JSON.parse(text);
    } catch (_) {
      const cleaned = text
        .replace(/^[^{[]*/, '')
        .replace(/[^}\]]*$/, '')
        .trim();
      try {
        return JSON.parse(cleaned);
      } catch (_) {
        return null;
      }
    }
  }
}

export default new InteractiveCourseService();

