import express from 'express';
import multer from 'multer';
import axios from 'axios';
import fs from 'fs';
import path from 'path';
import prisma, { defaultPrisma } from '../config/prisma.js';
import masterPrisma from '../config/masterPrisma.js';
import aiService from '../services/aiService.js';
import vectorChunkService from '../services/vectorChunkService.js';
import documentExtractor from '../services/documentExtractor.js';
import interactiveCourseService, { SUPPORTED_LANGUAGES, VOICE_CONFIG } from '../services/interactiveCourseService.js';
import tenantService from '../services/tenantService.js';
import { authenticateToken, requireRole } from '../middleware/auth.js';
import { resolveLanguageInfo, textMatchesLanguage } from '../utils/language.js';
import { cleanUnsupportedWords, stripSlidePlaceholders, isBinaryNoiseText, isPlaceholderTitle } from '../utils/contentQuality.js';
import aiGatewayClient from '../services/aiGatewayClient.js';

// In-memory high-speed cache for pre-generated quizzes (Key: `${courseId}_${lessonId}_${languageCode}`)
const quizCache = new Map();
const inFlightQuizzes = new Map();

export function getCourseUploadDir(courseId) {
  const candidates = [
    path.join(process.cwd(), 'uploads', 'courses', String(courseId)),
    path.join(process.cwd(), 'Backend', 'uploads', 'courses', String(courseId)),
    path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'explain-studio', 'backend', 'storage', 'courses', String(courseId)),
    path.join(process.cwd(), '..', 'explain-studio-local-frontend-server-backend', 'explain-studio', 'backend', 'storage', 'courses', String(courseId)),
    path.join('C:\\Users\\pavankumar\\Music\\Skill-Bridge\\explain-studio-local-frontend-server-backend\\explain-studio\\backend\\storage\\courses', String(courseId)),
    path.join('C:\\Users\\pavankumar\\Downloads\\explain-studio-local-frontend-server-backend\\explain-studio\\backend\\storage\\courses', String(courseId))
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return candidates[0];
}

const router = express.Router();
const upload = multer({
  storage: multer.memoryStorage(),
});

/**
 * Extracts text and segmented pages/slides from any supported document format.
 * Supported: PPTX, PPT, PDF, DOCX, DOC, TXT, MD, CSV, JSON, RTF, HTML, etc.
 */
async function extractDocumentContent(file) {
  return await documentExtractor.extractDocument(file);
}

/**
 * Universal cross-tenant course resolver.
 * Locates the requested course in req.prisma, defaultPrisma, prisma, or any active tenant DB.
 */
export async function findCourseAcrossTenants(req, courseId, includeOptions = {}) {
  const targetPrisma = req?.prisma || prisma;
  let course = null;

  // 1. Try target prisma (the tenant from the user token or req header)
  try {
    course = await targetPrisma.course.findUnique({
      where: { id: courseId },
      ...includeOptions
    });
    if (course) return { course, prismaClient: targetPrisma };
  } catch (_) {}

  // 2. Fallback to default DB if targetPrisma was a tenant
  if (targetPrisma !== defaultPrisma && defaultPrisma) {
    try {
      course = await defaultPrisma.course.findUnique({
        where: { id: courseId },
        ...includeOptions
      });
      if (course) return { course, prismaClient: defaultPrisma };
    } catch (_) {}
  }

  // 3. Fallback to base prisma
  if (targetPrisma !== prisma && prisma) {
    try {
      course = await prisma.course.findUnique({
        where: { id: courseId },
        ...includeOptions
      });
      if (course) return { course, prismaClient: prisma };
    } catch (_) {}
  }

  // 4. Cross-tenant lookup across all registered companies
  try {
    const companies = await masterPrisma.company.findMany({
      select: { code: true }
    }).catch(() => []);

    for (const comp of companies) {
      if (!comp?.code) continue;
      try {
        const compPrisma = await tenantService.getTenantClient(comp.code);
        course = await compPrisma.course.findUnique({
          where: { id: courseId },
          ...includeOptions
        });
        if (course) return { course, prismaClient: compPrisma };
      } catch (_) {}
    }
  } catch (_) {}

  return { course: null, prismaClient: targetPrisma };
}

// 1. Admin: Create course module by uploading a document (PDF, Word, PPTX, TXT, etc.)
router.post('/upload', authenticateToken, requireRole(['ADMIN']), upload.single('file'), async (req, res) => {
  try {
    const { title, description } = req.body;

    if (!title || !description) {
      return res.status(400).json({ error: 'Course title and description/info are required.' });
    }

    if (!req.file) {
      return res.status(400).json({ error: 'Please upload a course document (PDF, DOCX, DOC, PPTX, PPT, TXT, etc.).' });
    }

    console.log(`[Document Extraction] Admin creating course: "${title}", parsing file: ${req.file.originalname}...`);
    const docResult = await extractDocumentContent(req.file);
    const { text: textContent, pages, slides, fileType } = docResult;

    const imageCount = (slides || []).reduce((n, s) => n + ((s.images || []).length), 0) + ((docResult.images || []).length);
    if (!textContent || textContent.length < 30) {
      return res.status(400).json({
        error: imageCount > 0
          ? 'The PowerPoint is image-based, but no readable training text could be found in the slide pictures. Try a PPTX with clearer photos, or add slide titles/notes.'
          : 'Failed to extract text from document. The file might be empty, password-protected, or scanned images only.'
      });
    }

    const segmentsCount = slides.length > 0 ? `${slides.length} slide(s)` : `${pages.length} section(s)/page(s)`;
    console.log(`[Document Extraction] Document extracted successfully. Extracted ${textContent.length} characters across ${segmentsCount} (${fileType.toUpperCase()}).`);

    console.log(`[Curriculum Design] Generating dynamic content-driven curriculum from document...`);
    const generatedCourse = await aiService.generateCourse(req.file.originalname, textContent, docResult);

    // Override the title/description with Admin input if needed, but preserve structure
    generatedCourse.title = title.trim();
    generatedCourse.description = description.trim();
    generatedCourse.fileType = fileType || docResult.fileType;
    generatedCourse.fileName = req.file.originalname;

    // Retain exact original document structure and page/slide visual images in curriculum
    if (Array.isArray(docResult.slides) && docResult.slides.length > 0) {
      generatedCourse.slides = docResult.slides;
    }
    if (Array.isArray(docResult.pages) && docResult.pages.length > 0) {
      generatedCourse.pages = docResult.pages;
    }
    if (Array.isArray(docResult.images) && docResult.images.length > 0) {
      generatedCourse.visuals = docResult.images;
    }

    console.log(`[Curriculum Design] Curriculum structured. Saving course to database...`);
    const targetPrisma = req.prisma || prisma;
    const newCourse = await targetPrisma.course.create({
      data: {
        title: generatedCourse.title,
        description: generatedCourse.description,
        fileName: req.file.originalname,
        fileText: textContent,
        curriculum: generatedCourse,
      },
    });

    // Save original file buffer and individual slide/page images to disk for lightweight streaming
    try {
      const courseUploadDir = path.join(process.cwd(), 'uploads', 'courses', String(newCourse.id));
      const slidesDir = path.join(courseUploadDir, 'slides');
      const pagesDir = path.join(courseUploadDir, 'pages');
      fs.mkdirSync(slidesDir, { recursive: true });
      fs.mkdirSync(pagesDir, { recursive: true });
      fs.writeFileSync(path.join(courseUploadDir, req.file.originalname), req.file.buffer);
      console.log(`[File Storage] Saved original file to ${path.join(courseUploadDir, req.file.originalname)}`);

      // Extract slide images to disk
      if (Array.isArray(generatedCourse.slides)) {
        generatedCourse.slides = generatedCourse.slides.map((s, idx) => {
          const sNum = s.slideNumber || idx + 1;
          const slideFile = path.join(slidesDir, `Slide${sNum}.png`);
          const rawData = s.fullSlideImage?.dataUrl || s.images?.[0]?.dataUrl || s.dataUrl;
          if (rawData && rawData.startsWith('data:image')) {
            const base64Part = rawData.split(',')[1];
            if (base64Part) {
              fs.writeFileSync(slideFile, Buffer.from(base64Part, 'base64'));
            }
          }
          const imageUrl = `/api/v1/courses/${newCourse.id}/slides/${sNum}/image`;
          return {
            ...s,
            slideNumber: sNum,
            image: imageUrl,
            fullSlideImage: {
              id: `full_slide_${sNum}`,
              url: imageUrl,
              slideNumber: sNum,
              fileName: `Slide${sNum}.png`,
              mimetype: 'image/png'
            },
            images: (s.images || []).map((im, imIdx) => ({
              ...im,
              dataUrl: undefined,
              url: `/api/v1/courses/${newCourse.id}/slides/${sNum}/image`
            }))
          };
        });
      }

      // Extract page images to disk
      if (Array.isArray(generatedCourse.pages)) {
        generatedCourse.pages = generatedCourse.pages.map((p, idx) => {
          const pNum = p.page_number || p.pageNumber || idx + 1;
          const pageFile = path.join(pagesDir, `Page${pNum}.png`);
          const rawData = p.pageImage?.dataUrl || p.images?.[0]?.dataUrl || p.dataUrl;
          if (rawData && rawData.startsWith('data:image')) {
            const base64Part = rawData.split(',')[1];
            if (base64Part) {
              fs.writeFileSync(pageFile, Buffer.from(base64Part, 'base64'));
            }
          }
          const imageUrl = `/api/v1/courses/${newCourse.id}/pages/${pNum}/image`;
          return {
            ...p,
            page_number: pNum,
            pageNumber: pNum,
            image: imageUrl,
            pageImage: {
              id: `full_page_${pNum}`,
              url: imageUrl,
              pageNumber: pNum,
              fileName: `Page${pNum}.png`,
              mimetype: 'image/png'
            },
            images: (p.images || []).map(im => ({ ...im, dataUrl: undefined }))
          };
        });
      }

      // Update course in DB with lightweight sanitized curriculum
      await targetPrisma.course.update({
        where: { id: newCourse.id },
        data: { curriculum: generatedCourse }
      });
      console.log(`[File Storage] Saved slide/page PNGs to disk and sanitized course payload in database.`);
    } catch (saveErr) {
      console.warn(`[File Storage] Notice saving course files:`, saveErr.message);
    }

    // Store semantic chunks and generate vector embeddings directly in PostgreSQL pgvector
    try {
      console.log(`[pgvector] Indexing course ${newCourse.id} with semantic comprehension into PostgreSQL pgvector...`);
      await vectorChunkService.indexCourse(targetPrisma, newCourse.id, docResult, generatedCourse);
      console.log(`[pgvector] Course chunks and vector embeddings stored successfully.`);

    } catch (vectorErr) {
      console.warn(`[pgvector] Indexing warning for course ${newCourse.id}:`, vectorErr.message);
    }

    try {
      const summary = generateModulesSummary(generatedCourse);
      await targetPrisma.$executeRaw`
        UPDATE "Course"
        SET "modulesSummary" = ${JSON.stringify(summary)}::jsonb
        WHERE id = ${newCourse.id};
      `;
    } catch (_) { }
    invalidateCoursesCatalogCache(req.tenantCode);

    res.status(201).json({
      message: 'Course published successfully.',
      course: {
        id: newCourse.id,
        title: newCourse.title,
        description: newCourse.description,
        fileName: newCourse.fileName,
        curriculum: newCourse.curriculum,
        createdAt: newCourse.createdAt,
      },
    });
  } catch (error) {
    console.error('[Document Extraction] Failed to publish course:', error);
    res.status(500).json({ error: error.message || 'Failed to process document and publish course.' });
  }
});

// Serve the original uploaded file (PDF, PPTX, DOCX, Image, etc.) for viewing/download
router.get('/:id/file', authenticateToken, async (req, res) => {
  try {
    const targetPrisma = req.prisma || prisma;
    const course = await targetPrisma.course.findUnique({
      where: { id: req.params.id },
      select: { id: true, fileName: true, title: true }
    });
    if (!course) return res.status(404).json({ error: 'Course not found.' });

    const courseUploadDir = getCourseUploadDir(course.id);
    if (!fs.existsSync(courseUploadDir)) {
      return res.status(404).json({ error: 'Original document file not found on server.' });
    }
    const files = fs.readdirSync(courseUploadDir);
    const targetFile = files.find(f => f.toLowerCase() === (course.fileName || '').toLowerCase()) || files.find(f => !fs.statSync(path.join(courseUploadDir, f)).isDirectory()) || files[0];
    if (!targetFile) {
      return res.status(404).json({ error: 'Original document file not found.' });
    }

    const fullPath = path.join(courseUploadDir, targetFile);
    const ext = path.extname(targetFile).toLowerCase();
    const mimeMap = {
      '.pdf': 'application/pdf',
      '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      '.ppt': 'application/vnd.ms-powerpoint',
      '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      '.doc': 'application/msword',
      '.png': 'image/png',
      '.jpg': 'image/jpeg',
      '.jpeg': 'image/jpeg',
      '.webp': 'image/webp',
      '.txt': 'text/plain',
      '.md': 'text/markdown'
    };
    const contentType = mimeMap[ext] || 'application/octet-stream';
    const inline = req.query.download === '1' ? 'attachment' : 'inline';

    res.setHeader('Content-Type', contentType);
    res.setHeader('Content-Disposition', `${inline}; filename="${encodeURIComponent(targetFile)}"`);
    fs.createReadStream(fullPath).pipe(res);
  } catch (err) {
    console.error('Error serving course file:', err);
    res.status(500).json({ error: 'Failed to retrieve original course file.' });
  }
});

// Serve slide image (PNG) for course (PowerPoint slides)
router.get('/:id/slides/:slideNum/image', async (req, res) => {
  try {
    const courseId = req.params.id;
    const slideNum = parseInt(req.params.slideNum, 10);
    if (isNaN(slideNum) || slideNum < 1) {
      return res.status(400).json({ error: 'Invalid slide number.' });
    }

    const courseUploadDir = getCourseUploadDir(courseId);
    const { course } = await findCourseAcrossTenants(req, courseId, {
      select: { id: true, curriculum: true, fileName: true }
    });
    const cur = typeof course?.curriculum === 'string' ? JSON.parse(course.curriculum) : (course?.curriculum || {});
    const esJobId = cur?.explainStudioJobId || courseId;

    const slidesDir = path.join(courseUploadDir, 'slides');
    const targetSlide = (cur.slides || []).find(s => s.slideNumber === slideNum) || cur.slides?.[slideNum - 1];

    const candidates = [
      targetSlide?.imagePath,
      path.join(slidesDir, `Slide${slideNum}.png`),
      path.join(slidesDir, `slide_${slideNum}.png`),
      path.join(slidesDir, `slide${slideNum}.png`),
      path.join(courseUploadDir, `Slide${slideNum}.png`),
      path.join(courseUploadDir, `slide_${slideNum}.png`),
      path.resolve(process.cwd(), 'Backend', 'uploads', 'courses', String(courseId), 'slides', `Slide${slideNum}.png`),
      path.resolve(process.cwd(), 'Backend', 'uploads', 'courses', String(courseId), 'slides', `slide_${slideNum}.png`),
      path.resolve(process.cwd(), 'uploads', 'courses', String(courseId), 'slides', `Slide${slideNum}.png`),
      path.resolve(process.cwd(), 'uploads', 'courses', String(courseId), 'slides', `slide_${slideNum}.png`),
      path.resolve(process.cwd(), '..', 'Backend', 'uploads', 'courses', String(courseId), 'slides', `Slide${slideNum}.png`),
      path.resolve(process.cwd(), '..', 'uploads', 'courses', String(courseId), 'slides', `Slide${slideNum}.png`),
      path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'backend', 'storage', 'courses', String(courseId), 'slides', `slide_${slideNum}.png`),
      path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'backend', 'storage', 'courses', String(esJobId), 'slides', `slide_${slideNum}.png`),
      path.join(process.cwd(), '..', 'explain-studio-local-frontend-server-backend', 'backend', 'storage', 'courses', String(courseId), 'slides', `slide_${slideNum}.png`),
      path.join(process.cwd(), '..', 'explain-studio-local-frontend-server-backend', 'backend', 'storage', 'courses', String(esJobId), 'slides', `slide_${slideNum}.png`),
      path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'explain-studio', 'backend', 'storage', 'courses', String(courseId), 'slides', `slide_${slideNum}.png`),
      path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'explain-studio', 'backend', 'storage', 'courses', String(esJobId), 'slides', `slide_${slideNum}.png`),
      path.join('C:\\Users\\pavankumar\\Music\\Skill-Bridge\\explain-studio-local-frontend-server-backend\\explain-studio\\backend\\storage\\courses', String(esJobId), 'slides', `slide_${slideNum}.png`),
      path.join('C:\\Users\\pavankumar\\Downloads\\explain-studio-local-frontend-server-backend\\explain-studio\\backend\\storage\\courses', String(esJobId), 'slides', `slide_${slideNum}.png`)
    ].filter(Boolean);

    for (const sf of candidates) {
      if (fs.existsSync(sf) && fs.statSync(sf).size > 100) {
        res.setHeader('Content-Type', 'image/png');
        res.setHeader('Cache-Control', 'public, max-age=86400');
        return fs.createReadStream(sf).pipe(res);
      }
    }

    if (!course) return res.status(404).json({ error: 'Course not found.' });

    const rawData = targetSlide?.fullSlideImage?.dataUrl || targetSlide?.images?.[0]?.dataUrl;

    if (rawData && rawData.startsWith('data:image')) {
      const base64Part = rawData.split(',')[1];
      if (base64Part) {
        fs.mkdirSync(slidesDir, { recursive: true });
        const buf = Buffer.from(base64Part, 'base64');
        const slideFile = path.join(slidesDir, `Slide${slideNum}.png`);
        fs.writeFileSync(slideFile, buf);
        res.setHeader('Content-Type', 'image/png');
        res.setHeader('Cache-Control', 'public, max-age=86400');
        return res.end(buf);
      }
    }

    return res.status(404).json({ error: `Slide image ${slideNum} not found.` });
  } catch (err) {
    console.error('Error serving slide image:', err);
    res.status(500).json({ error: 'Failed to retrieve slide image.' });
  }
});

// Serve page image (PNG) for course (PDF / documents)
router.get('/:id/pages/:pageNum/image', async (req, res) => {
  try {
    const courseId = req.params.id;
    const pageNum = parseInt(req.params.pageNum, 10);
    if (isNaN(pageNum) || pageNum < 1) {
      return res.status(400).json({ error: 'Invalid page number.' });
    }

    const courseUploadDir = path.join(process.cwd(), 'uploads', 'courses', String(courseId));
    const pagesDir = path.join(courseUploadDir, 'pages');
    const pageFile = path.join(pagesDir, `Page${pageNum}.png`);

    if (fs.existsSync(pageFile)) {
      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'public, max-age=86400');
      return fs.createReadStream(pageFile).pipe(res);
    }

    const targetPrisma = req.prisma || prisma;
    const course = await targetPrisma.course.findUnique({
      where: { id: courseId },
      select: { id: true, curriculum: true }
    });
    if (!course) return res.status(404).json({ error: 'Course not found.' });

    const cur = typeof course.curriculum === 'string' ? JSON.parse(course.curriculum) : (course.curriculum || {});
    const targetPage = (cur.pages || []).find(p => (p.page_number || p.pageNumber) === pageNum) || cur.pages?.[pageNum - 1];
    const rawData = targetPage?.pageImage?.dataUrl || targetPage?.images?.[0]?.dataUrl;

    if (rawData && rawData.startsWith('data:image')) {
      const base64Part = rawData.split(',')[1];
      if (base64Part) {
        fs.mkdirSync(pagesDir, { recursive: true });
        const buf = Buffer.from(base64Part, 'base64');
        fs.writeFileSync(pageFile, buf);
        res.setHeader('Content-Type', 'image/png');
        res.setHeader('Cache-Control', 'public, max-age=86400');
        return res.end(buf);
      }
    }

    return res.status(404).json({ error: `Page image ${pageNum} not found.` });
  } catch (err) {
    console.error('Error serving page image:', err);
    res.status(500).json({ error: 'Failed to retrieve page image.' });
  }
});

// Serve slide neural narration audio (MP3) with multi-language, dual-gender, and Range header support
router.get('/:id/slides/:slideNum/audio', async (req, res) => {
  try {
    const courseId = req.params.id;
    const slideNum = parseInt(req.params.slideNum, 10);
    const lang = (req.query.lang || 'en').trim();
    let gender = (req.query.gender || '').toLowerCase().trim();
    if (!gender) {
      try {
        const compRows = await masterPrisma.$queryRawUnsafe(
          `SELECT "aiVoiceGender" FROM "Company" WHERE "code" = $1 LIMIT 1`,
          req.tenantCode || 'default'
        ).catch(() => []);
        gender = String(compRows[0]?.aiVoiceGender || 'female').toLowerCase();
      } catch (_) {
        gender = 'female';
      }
    }
    const isFemale = gender === 'female';
    const suffix = isFemale ? '_female' : '';

    if (isNaN(slideNum) || slideNum < 1) {
      return res.status(400).json({ error: 'Invalid slide number.' });
    }

    const courseUploadDir = getCourseUploadDir(courseId);
    const langVariants = (lang.toLowerCase() === 'zh' || lang.toLowerCase() === 'zh-cn') ? ['zh-CN', 'zh'] : [lang];

    // Helper for Range audio streaming
    const streamAudioFile = (filePath) => {
      const stat = fs.statSync(filePath);
      const fileSize = stat.size;
      const range = req.headers.range;

      if (range) {
        const parts = range.replace(/bytes=/, '').split('-');
        const start = parseInt(parts[0], 10);
        const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
        const chunksize = (end - start) + 1;
        const fileStream = fs.createReadStream(filePath, { start, end });
        const head = {
          'Content-Range': `bytes ${start}-${end}/${fileSize}`,
          'Accept-Ranges': 'bytes',
          'Content-Length': chunksize,
          'Content-Type': 'audio/mpeg',
          'Cache-Control': 'public, max-age=86400',
          'Access-Control-Allow-Origin': '*'
        };
        res.writeHead(206, head);
        fileStream.pipe(res);
      } else {
        const head = {
          'Content-Length': fileSize,
          'Content-Type': 'audio/mpeg',
          'Accept-Ranges': 'bytes',
          'Cache-Control': 'public, max-age=86400',
          'Access-Control-Allow-Origin': '*'
        };
        res.writeHead(200, head);
        fs.createReadStream(filePath).pipe(res);
      }
    };

    // Fetch course metadata to retrieve explainStudioJobId if present
    const targetPrisma = req.prisma || prisma;
    let course = await targetPrisma.course.findUnique({
      where: { id: courseId },
      select: { id: true, curriculum: true }
    }).catch(() => null);

    // Cross-tenant fallback: search other tenant databases if not in resolved tenant
    if (!course) {
      try {
        const companies = await masterPrisma.company.findMany({ select: { code: true } }).catch(() => []);
        for (const comp of companies) {
          try {
            const tenantPrisma = await tenantService.getTenantClient(comp.code);
            const found = await tenantPrisma.course.findUnique({
              where: { id: courseId },
              select: { id: true, curriculum: true }
            });
            if (found) {
              course = found;
              break;
            }
          } catch (_) {}
        }
      } catch (_) {}
    }

    const cur = typeof course?.curriculum === 'string' ? JSON.parse(course.curriculum) : (course?.curriculum || {});
    const esJobId = cur?.explainStudioJobId || courseId;

    // 1. Scan candidate paths on disk
    const candidateFiles = [];
    for (const l of langVariants) {
      const dirs = [
        path.join(courseUploadDir, 'audio', l),
        path.join(process.cwd(), 'uploads', 'courses', String(courseId), 'audio', l),
        path.join(process.cwd(), 'Backend', 'uploads', 'courses', String(courseId), 'audio', l),
        path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'backend', 'storage', 'courses', String(courseId), 'audio', l),
        path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'backend', 'storage', 'courses', String(esJobId), 'audio', l),
        path.join(process.cwd(), '..', 'explain-studio-local-frontend-server-backend', 'backend', 'storage', 'courses', String(courseId), 'audio', l),
        path.join(process.cwd(), '..', 'explain-studio-local-frontend-server-backend', 'backend', 'storage', 'courses', String(esJobId), 'audio', l),
        path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'explain-studio', 'backend', 'storage', 'courses', String(courseId), 'audio', l),
        path.join(process.cwd(), 'explain-studio-local-frontend-server-backend', 'explain-studio', 'backend', 'storage', 'courses', String(esJobId), 'audio', l),
        path.join('C:\\Users\\pavankumar\\Music\\Skill-Bridge\\explain-studio-local-frontend-server-backend\\explain-studio\\backend\\storage\\courses', String(esJobId), 'audio', l)
      ];
      for (const d of dirs) {
        candidateFiles.push(
          path.join(d, `Slide${slideNum}${suffix}.mp3`),
          path.join(d, `slide_${slideNum}${suffix}.mp3`),
          path.join(d, `slide_${slideNum}_${gender}.mp3`),
          path.join(d, `Slide${slideNum}_${gender}.mp3`)
        );
        if (!isFemale) {
          candidateFiles.push(
            path.join(d, `Slide${slideNum}.mp3`),
            path.join(d, `slide_${slideNum}.mp3`),
            path.join(d, `slide_${slideNum}_male.mp3`),
            path.join(d, `Slide${slideNum}_male.mp3`)
          );
        }
      }
    }

    for (const cf of candidateFiles) {
      if (fs.existsSync(cf) && fs.statSync(cf).size > 200) {
        return streamAudioFile(cf);
      }
    }

    // 2. Try Explain Studio on-demand synthesis API on port 8000
    try {
      const esBaseUrl = (process.env.PYTHON_SERVICE_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
      const esUrl = `${esBaseUrl}/v1/courses/${esJobId}/audio/${langVariants[0]}/${slideNum}?gender=${gender}`;
      const esResp = await fetch(esUrl);
      if (esResp.ok) {
        const arrayBuf = await esResp.arrayBuffer();
        const buf = Buffer.from(arrayBuf);
        if (buf.length > 200) {
          const destAudioDir = path.join(courseUploadDir, 'audio', langVariants[0]);
          fs.mkdirSync(destAudioDir, { recursive: true });
          const targetFile = path.join(destAudioDir, `Slide${slideNum}${suffix}.mp3`);
          fs.writeFileSync(targetFile, buf);
          try { fs.writeFileSync(path.join(destAudioDir, `slide_${slideNum}${suffix}.mp3`), buf); } catch (_) {}
          return streamAudioFile(targetFile);
        }
      }
    } catch (esErr) {
      console.warn('[Courses] Explain Studio audio proxy notice:', esErr.message);
    }

    // 3. High-Fidelity Edge-TTS Dynamic Synthesis for requested gender (Male or Female)
    const targetSlide = (cur.slides || []).find(s => s.slideNumber === slideNum) || cur.slides?.[slideNum - 1];
    let explanation = targetSlide?.explanations?.[lang] ||
      (lang === 'zh' ? targetSlide?.explanations?.['zh-CN'] : null) ||
      (lang === 'zh-CN' ? targetSlide?.explanations?.['zh'] : null) ||
      targetSlide?.spokenExplanation || targetSlide?.explanation || targetSlide?.title || '';

    // If curriculum is missing slide explanation, check disk slide images/texts
    if (!explanation) {
      explanation = `Slide ${slideNum} of ${course?.title || 'this presentation'}.`;
    }

    try {
      console.log(`[Courses] Dynamically synthesizing ${gender} voice audio for course ${courseId}, slide ${slideNum} (${lang})...`);
      let audioBuf = null;

      // Primary: Fast neural TTS from local Python AI Service on port 8000
      try {
        const pyTtsUrl = `http://127.0.0.1:8000/api/tts/speak?text=${encodeURIComponent(explanation)}&gender=${encodeURIComponent(gender.toUpperCase())}&language=${encodeURIComponent(lang)}`;
        const pyResp = await fetch(pyTtsUrl);
        if (pyResp.ok) {
          const arrBuf = await pyResp.arrayBuffer();
          const b = Buffer.from(arrBuf);
          if (b.length > 200) {
            audioBuf = b;
          }
        }
      } catch (pyErr) {
        console.warn(`[Courses] Python TTS service notice: ${pyErr.message}`);
      }

      // Secondary: Direct InteractiveCourseService synthesis
      if (!audioBuf) {
        audioBuf = await interactiveCourseService.synthesizeSlideAudio(explanation, null, lang, gender);
      }

      if (audioBuf && audioBuf.length > 200) {
        const destDir = path.join(courseUploadDir, 'audio', lang);
        fs.mkdirSync(destDir, { recursive: true });
        const targetFile = path.join(destDir, `Slide${slideNum}${suffix}.mp3`);
        fs.writeFileSync(targetFile, audioBuf);
        try { fs.writeFileSync(path.join(destDir, `slide_${slideNum}${suffix}.mp3`), audioBuf); } catch (_) {}
        console.log(`[Courses] Successfully generated ${gender} audio for slide ${slideNum} (${audioBuf.length} bytes).`);
        return streamAudioFile(targetFile);
      }
    } catch (synthErr) {
      console.warn(`[Courses] Dynamic ${gender} audio synthesis error for slide ${slideNum}:`, synthErr.message);
    }

    // 4. Fallback for female: serve male audio if present
    if (isFemale) {
      for (const l of langVariants) {
        const candidateDirs = [
          path.join(courseUploadDir, 'audio', l),
          path.join(process.cwd(), 'Backend', 'uploads', 'courses', String(courseId), 'audio', l),
          path.join(process.cwd(), 'uploads', 'courses', String(courseId), 'audio', l)
        ];
        for (const cd of candidateDirs) {
          const fallback = path.join(cd, `Slide${slideNum}.mp3`);
          if (fs.existsSync(fallback) && fs.statSync(fallback).size > 200) {
            return streamAudioFile(fallback);
          }
          const fallback2 = path.join(cd, `slide_${slideNum}.mp3`);
          if (fs.existsSync(fallback2) && fs.statSync(fallback2).size > 200) {
            return streamAudioFile(fallback2);
          }
        }
      }
    }

    return res.status(404).json({ error: `Audio for slide ${slideNum} (${lang}, ${gender}) not ready.` });
  } catch (err) {
    console.error('Error serving slide audio:', err);
    res.status(500).json({ error: 'Failed to retrieve slide audio.' });
  }
});

// Check generation status for asynchronous interactive course processing
router.get('/:id/generation-status', async (req, res) => {
  try {
    const courseId = req.params.id;

    // Validate course ID parameter
    if (!courseId || typeof courseId !== 'string') {
      return res.status(400).json({
        courseId,
        status: 'failed',
        stage: 'invalid_id',
        progress: 0,
        error: 'Course ID is required.'
      });
    }

    // 1. Check in-memory active jobs first
    const memoryJob = interactiveCourseService.getJobStatus(courseId);
    if (memoryJob && memoryJob.stage !== 'idle') {
      const statusType = memoryJob.stage === 'completed'
        ? 'completed'
        : (memoryJob.stage === 'error' || memoryJob.stage === 'failed' ? 'failed' : (memoryJob.stage === 'pending' ? 'pending' : 'processing'));

      return res.json({
        courseId: String(courseId),
        status: statusType,
        stage: memoryJob.stage,
        progress: typeof memoryJob.progress === 'number' ? memoryJob.progress : (statusType === 'completed' ? 100 : 0),
        message: memoryJob.message || '',
        error: memoryJob.details?.error || (statusType === 'failed' ? (memoryJob.message || 'Generation failed.') : null),
        details: memoryJob.details || {},
        updatedAt: memoryJob.updatedAt || Date.now()
      });
    }

    // 2. If not active in memory, query database (first targetPrisma, then defaultPrisma, then other tenants)
    let course = null;
    const targetPrisma = req.prisma || prisma;

    try {
      course = await targetPrisma.course.findUnique({
        where: { id: courseId },
        select: { id: true, title: true, updatedAt: true, curriculum: true }
      });
    } catch (err) {
      console.warn(`[GenerationStatus] Error querying targetPrisma for course ${courseId}:`, err.message);
    }

    // If not found in target tenant, try default tenant
    if (!course && defaultPrisma && targetPrisma !== defaultPrisma) {
      try {
        course = await defaultPrisma.course.findUnique({
          where: { id: courseId },
          select: { id: true, title: true, updatedAt: true, curriculum: true }
        });
      } catch (err) {
        console.warn(`[GenerationStatus] Error querying defaultPrisma for course ${courseId}:`, err.message);
      }
    }

    // If still not found, search across known tenants
    if (!course && tenantService?.listCompanies) {
      try {
        const companies = await tenantService.listCompanies();
        for (const comp of companies) {
          if (comp.code && comp.code !== req.tenantCode && comp.code !== 'default') {
            try {
              const client = await tenantService.getTenantClient(comp.code);
              const found = await client.course.findUnique({
                where: { id: courseId },
                select: { id: true, title: true, updatedAt: true, curriculum: true }
              });
              if (found) {
                course = found;
                break;
              }
            } catch (_) { }
          }
        }
      } catch (tenantErr) {
        console.warn(`[GenerationStatus] Error iterating tenants for course ${courseId}:`, tenantErr.message);
      }
    }

    if (course) {
      const cur = typeof course.curriculum === 'string'
        ? JSON.parse(course.curriculum)
        : (course.curriculum || {});

      const curStatus = (cur.status || '').toLowerCase();
      const curStage = (cur.stage || '').toLowerCase();

      // Failed state
      if (curStatus === 'error' || curStatus === 'failed' || curStage === 'error' || curStage === 'failed') {
        return res.json({
          courseId,
          status: 'failed',
          stage: curStage || 'failed',
          progress: cur.progress || 0,
          message: cur.message || cur.error || 'Course generation failed.',
          error: cur.error || cur.message || 'Generation failed',
          details: cur.details || {},
          updatedAt: course.updatedAt
        });
      }

      // Pending state
      if (curStatus === 'pending' || curStage === 'pending') {
        return res.json({
          courseId,
          status: 'pending',
          stage: curStage || 'pending',
          progress: cur.progress || 5,
          message: cur.message || 'Course generation is queued and pending...',
          error: null,
          details: cur.details || {},
          updatedAt: course.updatedAt
        });
      }

      // Processing state
      if (curStatus === 'processing' || curStage === 'processing' || (cur.isInteractivePresentation && (!cur.slides || cur.slides.length === 0) && curStatus !== 'completed')) {
        return res.json({
          courseId,
          status: 'processing',
          stage: curStage || 'processing',
          progress: cur.progress || 20,
          message: cur.message || 'Course generation in progress...',
          error: null,
          details: cur.details || {},
          updatedAt: course.updatedAt
        });
      }

      // Completed state
      const slideCount = cur.totalSlides || (cur.slides || []).length || (cur.lessons || []).length || 0;
      const moduleCount = (cur.modules || cur.curriculum || []).length || 0;

      return res.json({
        courseId,
        status: 'completed',
        stage: 'completed',
        progress: 100,
        message: 'Course generation complete and ready.',
        isInteractive: Boolean(cur.isInteractivePresentation),
        slideCount,
        moduleCount,
        error: null,
        updatedAt: course.updatedAt
      });
    }

    // 3. Not found in memory or database
    return res.status(404).json({
      courseId,
      status: 'failed',
      stage: 'not_found',
      progress: 0,
      error: 'Course not found',
      message: `Course with ID ${courseId} was not found.`
    });
  } catch (err) {
    console.error('Error fetching generation status:', err);
    res.status(500).json({
      courseId: req.params.id,
      status: 'failed',
      stage: 'error',
      progress: 0,
      error: 'Failed to retrieve generation status.',
      details: err.message
    });
  }
});

// Admin: Upload presentation / document to create an AI-powered interactive narrated course
router.post('/upload-interactive', authenticateToken, requireRole(['ADMIN']), upload.single('file'), async (req, res) => {
  try {
    const { title, description } = req.body;

    console.log('[DIAGNOSTIC][Backend] /upload-interactive route received request:', {
      title,
      description,
      filename: req.file?.originalname,
      fileSize: req.file?.size,
      tenant: req.tenantCode || 'default'
    });

    if (!title || !description) {
      return res.status(400).json({ error: 'Course title and description are required.' });
    }

    if (!req.file) {
      return res.status(400).json({ error: 'Please upload a course document (.pptx, .pdf, or .docx).' });
    }

    const filename = req.file.originalname;
    const ext = path.extname(filename).toLowerCase();
    if (!['.pptx', '.ppt', '.pdf', '.docx', '.doc'].includes(ext)) {
      return res.status(400).json({ error: `File format ${ext} is not supported. Please upload PPTX, PDF, or DOCX.` });
    }

    console.log(`[InteractiveCourse] Starting async generation for "${title}" (${filename})...`);
    const targetPrisma = req.prisma || prisma;

    // Create preliminary course record
    const course = await targetPrisma.course.create({
      data: {
        title: title.trim(),
        description: description.trim(),
        fileName: filename,
        fileText: 'Course content processing in background...',
        curriculum: {
          isInteractivePresentation: true,
          status: 'pending',
          stage: 'pending',
          progress: 5,
          message: 'Document uploaded. Initializing interactive course generation...',
          title: title.trim(),
          description: description.trim(),
          fileName: filename,
          curriculum: [],
          lessons: [],
          slides: []
        }
      }
    });

    const courseId = course.id;
    console.log('[DIAGNOSTIC][Backend] Task created in DB with courseId:', courseId);

    // Register initial task immediately in interactiveCourseService before returning response
    interactiveCourseService.setJobStatus(courseId, 'pending', 5, 'Document uploaded. Initializing interactive course generation...');
    console.log('[DIAGNOSTIC][Backend] Initial task registered in activeJobs for courseId:', courseId);

    const courseUploadDir = path.join(process.cwd(), 'uploads', 'courses', String(courseId));
    fs.mkdirSync(courseUploadDir, { recursive: true });
    const originalFilePath = path.join(courseUploadDir, filename);
    fs.writeFileSync(originalFilePath, req.file.buffer);

    // Run the pipeline asynchronously in background
    (async () => {
      try {
        console.log('[DIAGNOSTIC][Backend] Actual AI processing starting for courseId:', courseId);

        try {
          // Primary: High-speed Explain Studio Python engine (3-4 mins, 6 languages, 1080p, 24x concurrent Edge-TTS)
          await interactiveCourseService.generateCourseWithExplainStudio(courseId, originalFilePath, filename, targetPrisma, title, description);
          return;
        } catch (esErr) {
          console.warn('[InteractiveCourse] Explain Studio primary pipeline notice:', esErr.message, 'Falling back to direct execution...');
        }

        // Stage 1: Ingestion & 1080p slide rendering
        const slidesManifest = await interactiveCourseService.renderAndExtractDocument(courseId, originalFilePath, filename);

        // Stage 2: Selective vision analysis (upfront before text model is pinned in VRAM)
        await interactiveCourseService.processSelectiveVision(courseId, slidesManifest);

        // Stage 3: Whole-document AI understanding (qwen2.5:14b pinned in VRAM)
        const docUnderstanding = await interactiveCourseService.analyzeWholeDocument(courseId, filename, slidesManifest);

        // Stage 4: AI curriculum and module planning (2-5 modules, 1:1 slide allocation)
        const modules = await interactiveCourseService.planCurriculumAndModules(courseId, filename, slidesManifest, docUnderstanding);

        // Stage 5: Teaching narration, bullet healing & quality auditing
        await interactiveCourseService.generateTeachingNarration(courseId, slidesManifest, docUnderstanding);

        // Stage 6: Multilingual course generation (EN, ZH, MS, TA, TE, BN)
        const { slidesManifest: translatedManifest, modules: translatedModules } =
          await interactiveCourseService.generateMultilingualTranslations(courseId, slidesManifest, modules, docUnderstanding);

        // Stage 7: Neural TTS & voice narration
        await interactiveCourseService.generateNeuralAudioNarration(courseId, translatedManifest);

        // Stage 8: Finalize interactive course
        const finalCurriculum = interactiveCourseService.finalizeInteractiveCourse(filename, translatedManifest, translatedModules, docUnderstanding, courseId);
        finalCurriculum.title = title.trim();
        finalCurriculum.description = description.trim();
        finalCurriculum.status = 'completed';
        finalCurriculum.stage = 'completed';

        // Resolve complete slide and lesson catalog from translated manifest or final curriculum
        const slidesList = (Array.isArray(translatedManifest?.slides) && translatedManifest.slides.length > 0)
          ? translatedManifest.slides
          : (Array.isArray(finalCurriculum?.slides) && finalCurriculum.slides.length > 0)
            ? finalCurriculum.slides
            : (Array.isArray(finalCurriculum?.lessons) ? finalCurriculum.lessons : []);

        // Compile full text for search / RAG
        const fullText = slidesList.map(s => `Slide ${s.slideNumber || s.id}: ${s.title || ''}\n${s.spokenExplanation || s.explanation || ''}`).join('\n\n');

        await targetPrisma.course.update({
          where: { id: courseId },
          data: {
            title: title.trim(),
            description: description.trim(),
            fileText: fullText,
            curriculum: finalCurriculum
          }
        });

        // Save all generated lessons, slides, and semantic concepts in local PostgreSQL CourseChunk table
        try {
          console.log(`[pgvector] Indexing course ${courseId} into PostgreSQL CourseChunk table (${slidesList.length} slides/lessons)...`);
          const docMetadata = {
            fileName: filename,
            fileType: 'presentation',
            slides: slidesList,
            curriculum: finalCurriculum
          };
          await vectorChunkService.indexCourse(targetPrisma, courseId, { text: fullText, ...docMetadata }, finalCurriculum);
          console.log(`[pgvector] Course ${courseId} chunks and vector embeddings successfully saved in PostgreSQL.`);
        } catch (chunkErr) {
          console.warn('[InteractiveCourse] Vector chunking notice:', chunkErr.message);
        }

        interactiveCourseService._updateJob(courseId, 'completed', 100, 'Interactive course generation successfully completed!');
        console.log('[DIAGNOSTIC][Backend] AI Pipeline successfully completed for courseId:', courseId);
      } catch (pipelineErr) {
        console.error(`[DIAGNOSTIC][Backend] AI Pipeline failed for ${courseId}:`, pipelineErr);
        interactiveCourseService._updateJob(courseId, 'failed', 0, `Generation failed: ${pipelineErr.message}`, { error: pipelineErr.stack });
        try {
          await targetPrisma.course.update({
            where: { id: courseId },
            data: {
              curriculum: {
                isInteractivePresentation: true,
                status: 'failed',
                stage: 'failed',
                progress: 0,
                error: pipelineErr.message
              }
            }
          });
        } catch (dbErr) {
          console.warn('[InteractiveCourse] Failed to update DB on error:', dbErr.message);
        }
      }
    })();

    // Return immediate response with course ID for live tracking
    console.log('[DIAGNOSTIC][Backend] Returning 202 response for courseId:', courseId);
    res.status(202).json({
      success: true,
      courseId,
      message: 'Document uploaded successfully. Interactive course generation started.',
      statusUrl: `/api/v1/courses/${courseId}/generation-status`
    });
  } catch (error) {
    console.error('[DIAGNOSTIC][Backend] Error starting interactive course generation:', error);
    res.status(500).json({ error: error.message || 'Failed to start interactive course generation.' });
  }
});

// 1b. Admin: Manually create course module (without PDF upload)
router.post('/manual', authenticateToken, requireRole(['ADMIN']), async (req, res) => {
  try {
    const { title, description, category, level, curriculum } = req.body;

    if (!title || !title.trim() || !description || !description.trim()) {
      return res.status(400).json({ error: 'Course title and description are required.' });
    }

    const trimmedTitle = title.trim();
    const trimmedDesc = description.trim();

    let curriculumObj = curriculum;
    if (!curriculumObj || typeof curriculumObj !== 'object') {
      curriculumObj = {
        title: trimmedTitle,
        description: trimmedDesc,
        category: category || 'General',
        level: level || 'Intermediate',
        curriculum: [
          { title: `${trimmedTitle} Fundamentals`, lessons: 2, duration: '15m', locked: false },
          { title: `${trimmedTitle} Practical Application`, lessons: 2, duration: '20m', locked: true }
        ],
        lessons: [
          { id: 1, chapterId: 1, chapterTitle: `${trimmedTitle} Fundamentals`, moduleId: 1, moduleTitle: `${trimmedTitle} Fundamentals`, title: `${trimmedTitle} Essentials`, duration: '07:30', status: 'active' },
          { id: 2, chapterId: 1, chapterTitle: `${trimmedTitle} Fundamentals`, moduleId: 1, moduleTitle: `${trimmedTitle} Fundamentals`, title: 'Core Operating Guidelines', duration: '08:15', status: 'locked' },
          { id: 3, chapterId: 2, chapterTitle: `${trimmedTitle} Practical Application`, moduleId: 2, moduleTitle: `${trimmedTitle} Practical Application`, title: 'Practical Execution & Safety Checks', duration: '09:45', status: 'locked' },
          { id: 4, chapterId: 2, chapterTitle: `${trimmedTitle} Practical Application`, moduleId: 2, moduleTitle: `${trimmedTitle} Practical Application`, title: 'Operational Review & Verification', duration: '08:30', status: 'locked' }
        ],
        quiz: [
          {
            question: `What is the primary focus of "${trimmedTitle}"?`,
            options: [
              trimmedDesc.slice(0, 60) + '...',
              'General computing concepts',
              'Advanced theoretical research',
              'System maintenance'
            ],
            answerIndex: 0
          }
        ]
      };
    } else {
      curriculumObj.title = trimmedTitle;
      curriculumObj.description = trimmedDesc;
    }

    const targetPrisma = req.prisma || prisma;
    const newCourse = await targetPrisma.course.create({
      data: {
        title: trimmedTitle,
        description: trimmedDesc,
        fileName: 'Manual Entry',
        fileText: trimmedDesc,
        curriculum: curriculumObj
      }
    });

    try {
      const summary = generateModulesSummary(curriculumObj);
      await targetPrisma.$executeRaw`
        UPDATE "Course"
        SET "modulesSummary" = ${JSON.stringify(summary)}::jsonb
        WHERE id = ${newCourse.id};
      `;
    } catch (_) { }
    invalidateCoursesCatalogCache(req.tenantCode);

    res.status(201).json({
      message: 'Course created successfully.',
      course: newCourse
    });
  } catch (error) {
    console.error('Failed to create manual course:', error);
    res.status(500).json({ error: error.message || 'Failed to create manual course.' });
  }
});

// Helper to collect all potential user identification aliases (UUID, email, FIN)
const resolveUserIdentifiers = async (user, targetPrisma = prisma) => {
  if (!user) return [];
  const directId = user.id || user.userId;
  const email = user.email ? String(user.email).trim().toLowerCase() : null;
  const fin = user.finNumber ? String(user.finNumber).trim() : null;

  const identifiers = new Set();
  if (directId) identifiers.add(String(directId));
  if (email) identifiers.add(email);
  if (fin) identifiers.add(fin);

  // Look up user record in current tenant database to find primary UUID
  try {
    const conditions = [];
    if (directId) conditions.push({ id: directId });
    if (email) conditions.push({ email });
    if (fin) conditions.push({ finNumber: fin });

    if (conditions.length > 0) {
      const dbUser = await targetPrisma.user.findFirst({
        where: { OR: conditions },
        select: { id: true, email: true, finNumber: true }
      });
      if (dbUser) {
        if (dbUser.id) identifiers.add(String(dbUser.id));
        if (dbUser.email) identifiers.add(dbUser.email.toLowerCase());
        if (dbUser.finNumber) identifiers.add(dbUser.finNumber);
      }
    }
  } catch (err) {
    console.warn('[Courses] Identifier resolution warning:', err.message);
  }

  return Array.from(identifiers);
};

// Helper to verify student course assignment access
const checkCourseAccess = async (req, courseId, client = null) => {
  if (!req.user) return false;
  if (req.user?.role === 'ADMIN' || req.user?.role === 'SUPER_ADMIN') return true;
  const targetPrisma = client || req.prisma || prisma;
  const userIds = await resolveUserIdentifiers(req.user, targetPrisma);
  if (userIds.length === 0) return true;

  const assignment = await targetPrisma.userCourseAssignment.findFirst({
    where: {
      userId: { in: userIds },
      courseId,
    },
  }).catch(() => null);
  if (assignment) return true;

  // If the course exists in this tenant or database, allow active learners to study and ask questions
  const courseRecord = await targetPrisma.course.findUnique({
    where: { id: courseId },
    select: { id: true, title: true }
  }).catch(() => null);

  if (courseRecord) return true;
  return false;
};

// Helper to generate fast lightweight modules summary
export function generateModulesSummary(curriculum) {
  if (!curriculum) return {};
  let curr = curriculum;
  if (typeof curr === 'string') {
    try { curr = JSON.parse(curr); } catch (_) { curr = {}; }
  }
  const sanitizedLessons = (curr?.lessons || []).map((l) => ({
    id: l.id,
    title: l.title,
    duration: l.duration,
    moduleId: l.moduleId,
    chapterId: l.chapterId,
    moduleTitle: l.moduleTitle,
    chapterTitle: l.chapterTitle,
  }));

  return {
    title: curr?.title || '',
    description: curr?.description || '',
    curriculum: curr?.curriculum || [],
    lessons: sanitizedLessons,
    quiz: curr?.quiz || [],
    modulesCount: (curr?.curriculum || []).length || sanitizedLessons.length,
    lessonsCount: sanitizedLessons.length,
  };
}

// In-memory catalog cache keyed by tenantCode to ensure strict tenant isolation
const catalogCacheByTenant = new Map();
const CATALOG_CACHE_TTL = 30 * 1000; // 30 seconds TTL

export function invalidateCoursesCatalogCache(tenantCode) {
  if (tenantCode) {
    catalogCacheByTenant.delete(tenantCode);
  } else {
    catalogCacheByTenant.clear();
  }
}

// Helper to format lightweight course object for catalog/listing without massive base64 payloads
function formatCourseForCatalog(course) {
  let curriculumSummary = course.curriculum;
  if (curriculumSummary) {
    if (typeof curriculumSummary === 'string') {
      try {
        curriculumSummary = JSON.parse(curriculumSummary);
      } catch (e) {
        curriculumSummary = {};
      }
    }

    const isInteractive = Boolean(curriculumSummary.isInteractivePresentation || curriculumSummary.isInteractive);

    // If lessons array is empty, synthesize clean lessons from modules/slides or course.chunks
    let rawLessons = (Array.isArray(curriculumSummary.lessons) && curriculumSummary.lessons.length > 0)
      ? curriculumSummary.lessons
      : [];

    if (rawLessons.length === 0 && Array.isArray(curriculumSummary.modules) && curriculumSummary.modules.length > 0) {
      for (const m of curriculumSummary.modules) {
        for (const s of (m.slides || [])) {
          const sNum = s.slideNumber || s.slide_number;
          rawLessons.push({
            id: sNum,
            slideNumber: sNum,
            title: cleanUnsupportedWords(s.title || `Slide ${sNum}`),
            moduleId: m.moduleId || m.module_id || m.id,
            moduleTitle: cleanUnsupportedWords(m.title || course.title),
            chapterTitle: cleanUnsupportedWords(m.title || course.title),
            duration: s.durationsSeconds?.en ? `${Math.floor(s.durationsSeconds.en / 60).toString().padStart(2, '0')}:${Math.floor(s.durationsSeconds.en % 60).toString().padStart(2, '0')}` : '01:30',
            sourceSlides: [sNum],
            explanation: s.spokenExplanation || s.explanations?.en || '',
            spoken_explanation: s.spokenExplanation || s.explanations?.en || '',
            explanations: s.explanations || {},
            image: `/api/v1/courses/${course.id}/slides/${sNum}/image`,
            audioUrls: s.audioUrls || s.audio_urls || {}
          });
        }
      }
    } else if (rawLessons.length === 0 && Array.isArray(course.chunks) && course.chunks.length > 0) {
      rawLessons = course.chunks.map((c, cIdx) => ({
        id: cIdx + 1,
        title: cleanUnsupportedWords(c.concept || c.topic || `Lesson ${cIdx + 1}`),
        duration: '02:30',
        moduleId: `mod_${Math.floor(cIdx / 4) + 1}`,
        moduleTitle: cleanUnsupportedWords(c.topic || course.title),
        chapterTitle: cleanUnsupportedWords(c.topic || course.title),
        explanation: c.chunkText || c.semanticSummary || '',
        sourceSlides: Array.isArray(c.sourceSlides) && c.sourceSlides.length > 0 ? c.sourceSlides : [c.sourcePage || cIdx + 1]
      }));
    }

    // Strip heavy base64 visuals and raw slide data from lessons for fast catalog listing
    const sanitizedLessons = rawLessons.map((l, lIdx) => ({
      id: l.id || lIdx + 1,
      title: cleanUnsupportedWords(l.title, `Lesson ${lIdx + 1}`),
      duration: l.duration || '02:30',
      moduleId: l.moduleId || `mod_${Math.floor(lIdx / 4) + 1}`,
      chapterId: l.chapterId,
      moduleTitle: cleanUnsupportedWords(l.moduleTitle || l.chapterTitle, course.title),
      chapterTitle: cleanUnsupportedWords(l.chapterTitle || l.moduleTitle, course.title),
      explanation: l.explanation,
      spoken_explanation: l.spoken_explanation || l.explanation,
      explanations: l.explanations || (l.explanation ? { en: l.explanation } : {}),
      audioUrls: l.audioUrls || {},
      image: (l.image && !l.image.includes('__COURSE_ID__')) ? l.image : `/api/v1/courses/${course.id}/slides/${l.sourceSlides?.[0] || l.id || (lIdx + 1)}/image`,
      sourceSlides: l.sourceSlides,
      keyTakeaways: l.keyTakeaways || [],
    }));

    const fileType = (curriculumSummary.fileType || course.fileName?.split('.').pop() || '').toLowerCase();
    const slideSummaries = Array.isArray(curriculumSummary.slides)
      ? curriculumSummary.slides.map((s, idx) => ({
        slideNumber: s.slideNumber || idx + 1,
        title: cleanUnsupportedWords(s.title || `Slide ${idx + 1}`),
        type: 'slide',
        image: `/api/v1/courses/${course.id}/slides/${s.slideNumber || idx + 1}/image`,
        bulletPoints: (s.bulletPoints || []).map(bp => cleanUnsupportedWords(bp)),
        headings: (s.headings || []).map(h => cleanUnsupportedWords(h)),
        paragraphs: s.paragraphs || [],
        explanations: s.explanations || {},
        audioUrls: s.audioUrls || {}
      }))
      : [];

    const pageSummaries = Array.isArray(curriculumSummary.pages)
      ? curriculumSummary.pages.map((p, idx) => ({
        page_number: p.page_number || p.pageNumber || idx + 1,
        title: cleanUnsupportedWords(p.title || `Page ${idx + 1}`),
        type: 'page',
        image: `/api/v1/courses/${course.id}/pages/${p.page_number || p.pageNumber || idx + 1}/image`
      }))
      : [];

    curriculumSummary = {
      title: cleanUnsupportedWords(curriculumSummary.title || course.title),
      description: cleanUnsupportedWords(curriculumSummary.description || course.description),
      fileType,
      fileName: course.fileName,
      slides: slideSummaries,
      pages: pageSummaries,
      includes: curriculumSummary.includes,
      learning: curriculumSummary.learning,
      curriculum: curriculumSummary.curriculum || curriculumSummary.modules || [],
      modules: curriculumSummary.modules || curriculumSummary.curriculum || [],
      lessons: sanitizedLessons,
      quiz: curriculumSummary.quiz,
      isInteractivePresentation: isInteractive,
      glossary: curriculumSummary.glossary || {}
    };
  }

  const detectedFileType = (curriculumSummary?.fileType || course.fileName?.split('.').pop() || '').toLowerCase();
  const isInteractive = Boolean(curriculumSummary?.isInteractivePresentation);

  return {
    id: course.id,
    isInteractivePresentation: isInteractive,
    title: cleanUnsupportedWords(course.title),
    description: cleanUnsupportedWords(course.description),
    fileName: course.fileName,
    fileType: detectedFileType,
    slides: curriculumSummary?.slides || [],
    pages: curriculumSummary?.pages || [],
    modules: curriculumSummary?.modules || curriculumSummary?.curriculum || [],
    curriculum: curriculumSummary,
    lessons: curriculumSummary?.lessons || [],
    chunks: course.chunks || [],
    createdAt: course.createdAt,
    updatedAt: course.updatedAt,
    assignedUsersCount: course.assignedUsersCount ?? course._count?.assignments ?? 0,
  };
}

// 2. Get published RAG courses (Admin gets all with counts; Students get ONLY assigned courses)
router.get('/', authenticateToken, async (req, res) => {
  try {
    const targetPrisma = req.prisma || prisma;

    const tenantKey = req.tenantCode || 'default';

    if (req.user?.role === 'ADMIN') {
      const now = Date.now();
      const cached = catalogCacheByTenant.get(tenantKey);
      if (cached && (now - cached.time) < CATALOG_CACHE_TTL) {
        return res.json(cached.data);
      }

      const courses = await targetPrisma.$queryRaw`
        SELECT
          c.id,
          c.title,
          c.description,
          c."fileName",
          c."createdAt",
          c."updatedAt",
          (
            SELECT COUNT(*)::int
            FROM "UserCourseAssignment" uca
            WHERE uca."courseId" = c.id
          ) AS "assignedUsersCount",
          c.curriculum AS curriculum
        FROM "Course" c
        ORDER BY c."createdAt" DESC;
      `;
      const formatted = courses.map(formatCourseForCatalog);
      catalogCacheByTenant.set(tenantKey, { data: formatted, time: now });
      return res.json(formatted);
    }

    // For STUDENT role: return ONLY assigned courses with user progress in 1 query
    const userIds = await resolveUserIdentifiers(req.user, targetPrisma);
    if (userIds.length === 0) {
      return res.json([]);
    }

    const assignments = await targetPrisma.userCourseAssignment.findMany({
      where: {
        userId: { in: userIds }
      },
      select: { courseId: true },
    });
    const assignedIds = assignments.map((a) => a.courseId);

    if (assignedIds.length === 0) {
      return res.json([]);
    }

    const [courses, progressRecords] = await Promise.all([
      targetPrisma.$queryRaw`
        SELECT
          c.id,
          c.title,
          c.description,
          c."fileName",
          c."createdAt",
          c."updatedAt",
          (
            SELECT COUNT(*)::int
            FROM "UserCourseAssignment" uca
            WHERE uca."courseId" = c.id
          ) AS "assignedUsersCount",
          c.curriculum AS curriculum
        FROM "Course" c
        WHERE c.id = ANY(${assignedIds}::text[])
        ORDER BY c."createdAt" DESC;
      `,
      targetPrisma.courseProgress.findMany({
        where: {
          userId: { in: userIds },
          courseId: { in: assignedIds }
        }
      })
    ]);

    const progressByCourse = {};
    for (const pr of progressRecords) {
      let lessonIds = pr.completedLessonIds;
      if (typeof lessonIds === 'string') {
        try { lessonIds = JSON.parse(lessonIds); } catch (_) { lessonIds = []; }
      }
      if (!Array.isArray(lessonIds)) lessonIds = [];
      progressByCourse[pr.courseId] = {
        progress: Math.min(100, Math.max(0, pr.progress || 0)),
        completed: Boolean(pr.completed),
        completedLessonIds: Array.from(new Set(lessonIds.filter(id => id !== null && id !== undefined).map(String)))
      };
    }

    const formatted = courses.map(c => {
      const formattedCourse = formatCourseForCatalog(c);
      formattedCourse.userProgress = progressByCourse[c.id] || {
        progress: 0,
        completed: false,
        completedLessonIds: []
      };
      return formattedCourse;
    });

    return res.json(formatted);
  } catch (error) {
    console.error('Failed to retrieve RAG courses:', error);
    res.status(500).json({ error: 'Failed to retrieve courses catalog.' });
  }
});

// 3. Get specific course details (verifying assignment for students)
router.get('/:id', authenticateToken, async (req, res) => {
  try {
    const { course, prismaClient } = await findCourseAcrossTenants(req, req.params.id, {
      include: {
        chunks: {
          orderBy: { chunkIndex: 'asc' },
          select: {
            id: true,
            chunkIndex: true,
            chunkText: true,
            topic: true,
            subtopic: true,
            concept: true,
            chunkType: true,
            sourcePage: true,
            sourceSlides: true,
            sourceSection: true,
            keyPoints: true,
            relatedConcepts: true,
            semanticSummary: true,
            metadata: true
          }
        },
        _count: {
          select: { assignments: true },
        },
      },
    });

    if (!course) {
      return res.status(404).json({ error: 'Course not found.' });
    }

    const hasAccess = await checkCourseAccess(req, course.id, prismaClient);
    if (!hasAccess) {
      return res.status(403).json({ error: 'Access denied. You are not assigned to this course.' });
    }

    let curriculumData = typeof course.curriculum === 'string'
      ? JSON.parse(course.curriculum)
      : (course.curriculum || {});

    // Parse slide blocks from fileText if available for rich fallback
    const slideBlockMap = new Map();
    if (course.fileText) {
      const blocks = course.fileText.split(/(?=--- Slide \d+:)/);
      for (const b of blocks) {
        const match = b.match(/--- Slide (\d+):\s*(.*?)\s*---/);
        if (match) {
          const sNum = parseInt(match[1], 10);
          const sTitle = match[2]?.trim() || `Slide ${sNum}`;
          const lines = b.replace(/--- Slide \d+:[^\n]+\n/, '').split('\n').map(l => l.trim()).filter(Boolean);
          const bullets = lines.filter(l => l.startsWith('- ') || l.startsWith('* ') || /^\d+\.\s/.test(l));
          const tables = lines.filter(l => l.includes('|'));
          const paragraphs = lines.filter(l => !l.startsWith('- ') && !l.startsWith('* ') && !/^\d+\.\s/.test(l) && !l.includes('|') && !l.startsWith('#') && !l.startsWith('['));
          slideBlockMap.set(sNum, {
            slideNumber: sNum,
            title: sTitle,
            bulletPoints: bullets,
            tables,
            paragraphs,
            fullText: b.trim()
          });
        }
      }
    }

    // If curriculumData.lessons is empty but modules/slides are present, synthesize 1:1 lessons
    if (!Array.isArray(curriculumData.lessons) || curriculumData.lessons.length === 0) {
      if (Array.isArray(curriculumData.modules) && curriculumData.modules.length > 0) {
        curriculumData.lessons = [];
        for (const m of curriculumData.modules) {
          for (const s of (m.slides || [])) {
            const sNum = s.slideNumber || s.slide_number;
            curriculumData.lessons.push({
              id: sNum,
              slideNumber: sNum,
              title: s.title || `Slide ${sNum}`,
              moduleId: m.moduleId || m.module_id || m.id,
              moduleTitle: m.title,
              chapterTitle: m.title,
              duration: s.durationsSeconds?.en ? `${Math.floor(s.durationsSeconds.en / 60).toString().padStart(2, '0')}:${Math.floor(s.durationsSeconds.en % 60).toString().padStart(2, '0')}` : '01:30',
              sourceSlides: [sNum],
              explanation: s.spokenExplanation || s.explanations?.en || '',
              spokenExplanation: s.spokenExplanation || s.explanations?.en || '',
              spoken_explanation: s.spokenExplanation || s.explanations?.en || '',
              explanations: s.explanations || {},
              keyPoints: s.keyPoints || s.key_points || [],
              image: `/api/v1/courses/${course.id}/slides/${sNum}/image`,
              audioUrls: s.audioUrls || s.audio_urls || {},
              status: 'active'
            });
          }
        }
      }
    }

    // Enrich lessons with authentic slide / chunk data
    if (Array.isArray(curriculumData.lessons)) {
      curriculumData.lessons = curriculumData.lessons.map((lesson, idx) => {
        const sNums = Array.isArray(lesson.sourceSlides) ? lesson.sourceSlides : [idx + 1];
        const sNum = sNums[0] || (idx + 1);

        // Find matching chunk from pgvector CourseChunk table
        const matchedChunk = (course.chunks || []).find(c => {
          if (Array.isArray(c.sourceSlides) && c.sourceSlides.includes(sNum)) return true;
          if (c.sourcePage === sNum) return true;
          return false;
        }) || (course.chunks && course.chunks[idx]);

        const slideBlock = slideBlockMap.get(sNum);

        let slideData = lesson.slideData;
        if (!slideData) {
          if (slideBlock) {
            slideData = {
              slideNumber: slideBlock.slideNumber,
              title: slideBlock.title || lesson.title,
              headings: [],
              bulletPoints: slideBlock.bulletPoints || [],
              paragraphs: slideBlock.paragraphs || [],
              tables: slideBlock.tables || [],
              speakerNotes: '',
              fullText: slideBlock.fullText || '',
              images: lesson.visuals || []
            };
          } else if (matchedChunk) {
            const lines = (matchedChunk.chunkText || '').split('\n').map(l => l.trim()).filter(Boolean);
            const bullets = lines.filter(l => l.startsWith('- ') || l.startsWith('* ') || /^\d+\.\s/.test(l));
            const tables = lines.filter(l => l.includes('|'));
            const paragraphs = lines.filter(l => !l.startsWith('- ') && !l.startsWith('* ') && !/^\d+\.\s/.test(l) && !l.includes('|') && !l.startsWith('#'));
            slideData = {
              slideNumber: matchedChunk.sourcePage || sNum,
              title: matchedChunk.concept || lesson.title,
              headings: [matchedChunk.topic].filter(Boolean),
              bulletPoints: bullets.length > 0 ? bullets : (matchedChunk.keyPoints || []),
              paragraphs,
              tables,
              speakerNotes: '',
              fullText: matchedChunk.chunkText || '',
              images: lesson.visuals || (matchedChunk.metadata?.visuals || [])
            };
          }
        }

        const cleanLessonVisuals = Array.isArray(lesson.visuals)
          ? lesson.visuals.map(v => ({
            ...v,
            dataUrl: undefined,
            url: v.sourceSlide ? `/api/v1/courses/${course.id}/slides/${v.sourceSlide}/image` : v.url
          }))
          : [];

        if (slideData && Array.isArray(slideData.images)) {
          slideData.images = slideData.images.map(img => ({
            ...img,
            dataUrl: undefined,
            url: img.sourceSlide ? `/api/v1/courses/${course.id}/slides/${img.sourceSlide}/image` : img.url
          }));
        }

        const cleanExplanation = lesson.explanation ? cleanUnsupportedWords(stripSlidePlaceholders(lesson.explanation)) : '';
        const cleanSpokenExplanation = lesson.spoken_explanation ? cleanUnsupportedWords(stripSlidePlaceholders(lesson.spoken_explanation)) : '';
        const cleanTitle = stripSlidePlaceholders(lesson.title) || lesson.title;

        const cleanExplanations = {};
        if (lesson.explanations && typeof lesson.explanations === 'object') {
          for (const [lKey, lVal] of Object.entries(lesson.explanations)) {
            cleanExplanations[lKey] = typeof lVal === 'string' ? cleanUnsupportedWords(stripSlidePlaceholders(lVal)) : lVal;
          }
        }

        const resolvedImage = (lesson.image && !lesson.image.includes('__COURSE_ID__'))
          ? lesson.image
          : `/api/v1/courses/${course.id}/slides/${sNums[0] || lesson.id || 1}/image`;

        return {
          ...lesson,
          title: cleanTitle,
          image: resolvedImage,
          explanation: cleanExplanation || lesson.explanation,
          spoken_explanation: cleanSpokenExplanation || cleanExplanation || lesson.spoken_explanation,
          explanations: Object.keys(cleanExplanations).length > 0 ? cleanExplanations : lesson.explanations,
          sourceSlides: sNums,
          visuals: cleanLessonVisuals,
          slideData,
          chunkData: matchedChunk ? {
            chunkIndex: matchedChunk.chunkIndex,
            title: matchedChunk.concept || cleanTitle,
            text: stripSlidePlaceholders(matchedChunk.chunkText || ''),
            bulletPoints: Array.isArray(matchedChunk.keyPoints) ? matchedChunk.keyPoints.map(stripSlidePlaceholders) : []
          } : null
        };
      });
    }

    // Sanitize heavy base64 strings from curriculumData.slides and curriculumData.pages
    if (Array.isArray(curriculumData.slides)) {
      curriculumData.slides = curriculumData.slides.map((s, idx) => {
        const sNum = s.slideNumber || (idx + 1);
        const imageUrl = `/api/v1/courses/${course.id}/slides/${sNum}/image`;
        return {
          ...s,
          slideNumber: sNum,
          image: imageUrl,
          fullSlideImage: {
            id: `full_slide_${sNum}`,
            url: imageUrl,
            slideNumber: sNum,
            fileName: `Slide${sNum}.png`,
            mimetype: 'image/png'
          },
          images: Array.isArray(s.images)
            ? s.images.map(img => {
              if (String(img.id).startsWith('full_slide') || img.slideNumber === sNum) {
                return { ...img, url: imageUrl, dataUrl: undefined };
              }
              return { ...img, dataUrl: undefined };
            })
            : []
        };
      });
    }

    if (Array.isArray(curriculumData.pages)) {
      curriculumData.pages = curriculumData.pages.map((p, idx) => {
        const pNum = p.page_number || p.pageNumber || (idx + 1);
        const imageUrl = `/api/v1/courses/${course.id}/pages/${pNum}/image`;
        return {
          ...p,
          page_number: pNum,
          pageNumber: pNum,
          image: imageUrl,
          pageImage: {
            id: `full_page_${pNum}`,
            url: imageUrl,
            pageNumber: pNum,
            fileName: `Page${pNum}.png`,
            mimetype: 'image/png'
          },
          images: []
        };
      });
    }

    if (Array.isArray(curriculumData.visuals)) {
      curriculumData.visuals = curriculumData.visuals.map(v => ({
        ...v,
        dataUrl: undefined,
        url: v.sourceSlide ? `/api/v1/courses/${course.id}/slides/${v.sourceSlide}/image` : v.url
      }));
    }

    const cleanChunks = (course.chunks || []).map(c => ({
      ...c,
      topic: cleanUnsupportedWords(c.topic),
      subtopic: cleanUnsupportedWords(c.subtopic),
      concept: cleanUnsupportedWords(c.concept),
      keyPoints: Array.isArray(c.keyPoints) ? c.keyPoints.map(kp => cleanUnsupportedWords(kp)) : [],
      semanticSummary: cleanUnsupportedWords(c.semanticSummary)
    }));

    const detectedFileType = (curriculumData?.fileType || course.fileName?.split('.').pop() || '').toLowerCase();

    // If curriculumData.lessons is empty or sparse, populate from clean chunks so all concepts are divided into lessons
    if ((!Array.isArray(curriculumData.lessons) || curriculumData.lessons.length === 0) && cleanChunks.length > 0) {
      curriculumData.lessons = cleanChunks.map((c, idx) => ({
        id: idx + 1,
        title: cleanUnsupportedWords(c.concept || c.topic || `Lesson ${idx + 1}`),
        duration: '02:30',
        moduleTitle: cleanUnsupportedWords(c.topic || course.title),
        chapterTitle: cleanUnsupportedWords(c.topic || course.title),
        sourceSlides: Array.isArray(c.sourceSlides) && c.sourceSlides.length > 0 ? c.sourceSlides : [c.sourcePage || idx + 1],
        status: idx === 0 ? 'active' : 'locked',
        explanation: c.chunkText || c.semanticSummary || '',
        bulletPoints: (c.keyPoints || []).map(kp => cleanUnsupportedWords(kp))
      }));
    } else if (Array.isArray(curriculumData.lessons)) {
      curriculumData.lessons = curriculumData.lessons.map(l => ({
        ...l,
        title: cleanUnsupportedWords(l.title),
        moduleTitle: cleanUnsupportedWords(l.moduleTitle || l.chapterTitle, course.title),
        chapterTitle: cleanUnsupportedWords(l.chapterTitle || l.moduleTitle, course.title)
      }));
    }

    const isInteractive = Boolean(curriculumData?.isInteractivePresentation);

    res.json({
      ...course,
      isInteractivePresentation: isInteractive,
      title: cleanUnsupportedWords(course.title),
      description: cleanUnsupportedWords(course.description),
      chunks: cleanChunks,
      fileType: detectedFileType,
      slides: curriculumData.slides || [],
      pages: curriculumData.pages || [],
      visuals: curriculumData.visuals || [],
      modules: curriculumData.modules || curriculumData.curriculum || [],
      curriculum: curriculumData,
      lessons: curriculumData.lessons || [],
      assignedUsersCount: course._count?.assignments || 0,
    });
  } catch (error) {
    console.error('Failed to retrieve course details:', error);
    res.status(500).json({ error: 'Failed to retrieve course details.' });
  }
});


function synthesizeLessonFromDbChunks(lessonTitle, chunks, courseTitle = '', matchingLesson = null) {
  // Find the most relevant chunk for this specific lesson
  let targetChunk = null;
  if (Array.isArray(chunks) && chunks.length > 0) {
    targetChunk = chunks.find(c => {
      const cConcept = (c.concept || '').toLowerCase();
      const lTitle = (lessonTitle || '').toLowerCase();
      return cConcept === lTitle || lTitle.includes(cConcept) || cConcept.includes(lTitle);
    }) || chunks[0];
  }

  if (!targetChunk) {
    return `### 💡 ${lessonTitle}\nIn this lesson, we study the core operational standards, protocols, and safety practices for ${lessonTitle}.\n\n#### 🎯 Key Protocols\n- Follow all standard operational procedures.\n- Verify all safety requirements before beginning tasks.\n\n#### 📌 Key Takeaways\n- Strict adherence to guidelines prevents errors and ensures safety.`;
  }

  const concept = targetChunk.concept || lessonTitle;
  const topic = targetChunk.topic || matchingLesson?.chapterTitle || matchingLesson?.moduleTitle || courseTitle;

  let rawText = stripSlidePlaceholders(targetChunk.text || targetChunk.chunkText || '')
    .replace(/---+\s*Slide\s*\d+[^-\n]*---*/gi, '')
    .replace(/Slide\s*\d+:?\s*---*/gi, '')
    .replace(/Slide\s*\d+:\s*/gi, '')
    .replace(/^###[^\n]+\n/g, '')
    .replace(/^\*\*Topic:[^\n]+\n/g, '')
    .replace(/\*?\(Source:[^\)]*\)\*?/gi, '')
    .replace(/\*?\(Reference:[^\)]*\)\*?/gi, '')
    .trim();

  // Rejoin fragmented lines from PowerPoint line wraps into coherent sentences
  const rawLines = rawText.split('\n').map(l => stripSlidePlaceholders(l.trim())).filter(Boolean);
  const cleanBlocks = [];
  let buffer = '';
  for (const line of rawLines) {
    const isBullet = /^[-*•➔\d\.]+\s+/.test(line);
    const cleanLine = stripSlidePlaceholders(line.replace(/^[-*•➔\d\.]+\s+/, '').trim());
    if (!cleanLine || isBinaryNoiseText(cleanLine)) continue;
    if (isBullet) {
      if (buffer) { cleanBlocks.push(buffer); buffer = ''; }
      buffer = cleanLine;
    } else if (buffer && !/[.!?]$/.test(buffer)) {
      buffer += ' ' + cleanLine;
    } else {
      if (buffer) cleanBlocks.push(buffer);
      buffer = cleanLine;
    }
  }
  if (buffer && !isBinaryNoiseText(buffer)) cleanBlocks.push(buffer);
  const cleanContentText = cleanUnsupportedWords(cleanBlocks.join('\n\n'));

  let keyPoints = targetChunk.key_points || targetChunk.keyPoints || [];
  if (typeof keyPoints === 'string') {
    try { keyPoints = JSON.parse(keyPoints); } catch (e) { keyPoints = []; }
  }
  keyPoints = (keyPoints || [])
    .map(kp => cleanUnsupportedWords(stripSlidePlaceholders(String(kp).replace(/---+\s*Slide\s*\d+[^-\n]*---*/gi, '').replace(/Slide\s*\d+:\s*/gi, '').trim())))
    .filter(kp => kp.length > 5 && !isBinaryNoiseText(kp));

  const sections = [];
  let summary = cleanUnsupportedWords(stripSlidePlaceholders(targetChunk.semanticSummary || `In this lesson, we study the core standards, operational requirements, and protocols for ${concept}.`))
    .replace(/---+\s*Slide\s*\d+[^-\n]*---*/gi, '')
    .replace(/Slide\s*\d+:\s*/gi, '')
    .replace(/\s+/g, ' ')
    .trim();

  sections.push(`### 💡 ${concept}\n${summary}`);

  if (cleanContentText && cleanContentText.length > 15) {
    sections.push(`#### 🎯 Operational Principles & Standards\n${cleanContentText}`);
  }

  if (Array.isArray(keyPoints) && keyPoints.length > 0) {
    sections.push(`#### 📋 Key Action Items & Protocols\n${keyPoints.map(kp => `- ${kp}`).join('\n')}`);
  }

  sections.push(`#### ⚠️ Safety & Operational Compliance\n- Apply verified protocols for ${concept} systematically in your daily tasks.\n- Adhere strictly to equipment operation limits and safety boundaries.\n- Report any non-conformances, hazards, or equipment defects immediately.`);

  sections.push(`#### 📌 Key Takeaways\n- **Safety & Discipline:** Consistent execution of ${concept} standards prevents incidents and ensures reliable operations.\n- **Quality:** Maintaining verified practices eliminates defects and ensures operational excellence.`);

  return sections.join('\n\n');
}

// 4. Generate AI concept explanation for a specific lesson/chapter using RAG Vector DB with Database Persistence Cache
router.post('/:id/lessons/:lessonId/explain', authenticateToken, async (req, res) => {
  const fetchStartTime = Date.now();
  try {
    const { course, prismaClient } = await findCourseAcrossTenants(req, req.params.id);

    if (!course) {
      return res.status(404).json({ error: 'Course not found.' });
    }

    const hasAccess = await checkCourseAccess(req, course.id, prismaClient);
    if (!hasAccess) {
      return res.status(403).json({ error: 'Access denied. You are not assigned to this course.' });
    }

    const { lessonTitle, language } = req.body;
    if (!lessonTitle) {
      return res.status(400).json({ error: 'Lesson title is required.' });
    }

    const targetLangInfo = resolveLanguageInfo(language || req.user?.preferredLanguage);
    const targetLanguage = targetLangInfo.name;
    const lessonIdStr = String(req.params.lessonId);

    console.log(`[PERF:LESSON_FETCH_START] Course: ${course.id}, Lesson: ${lessonIdStr} ("${lessonTitle}"), Lang: "${targetLanguage}" (${targetLangInfo.code})`);

    // 1. FAST PATH 1: Check if lesson explanation is already stored in Course.curriculum database record
    let curriculum = course.curriculum;
    if (typeof curriculum === 'string') {
      try { curriculum = JSON.parse(curriculum); } catch { curriculum = {}; }
    }
    if (!curriculum || typeof curriculum !== 'object') curriculum = {};

    const isInteractive = Boolean(curriculum.isInteractivePresentation);

    if (Array.isArray(curriculum.lessons)) {
      const existingLesson = curriculum.lessons.find(
        l => String(l.id) === lessonIdStr || l.title === lessonTitle || (Array.isArray(l.sourceSlides) && l.sourceSlides.includes(parseInt(lessonIdStr, 10)))
      );

      if (existingLesson) {
        const cachedExplanation = existingLesson.explanations?.[targetLangInfo.code] ||
          (targetLangInfo.code === 'en' ? (existingLesson.explanation || existingLesson.spoken_explanation) : null) ||
          existingLesson.explanations?.en ||
          existingLesson.explanation ||
          existingLesson.spoken_explanation;

        if (cachedExplanation && typeof cachedExplanation === 'string' && cachedExplanation.trim().length > 10) {
          const fetchDuration = Date.now() - fetchStartTime;
          const cleanCached = cleanUnsupportedWords(stripSlidePlaceholders(cachedExplanation));
          console.log(`[PERF:LESSON_FETCH_END] Served lesson "${lessonTitle}" directly from PostgreSQL DB cache in ${fetchDuration}ms`);
          return res.json({
            explanation: cleanCached,
            spoken_explanation: cleanCached,
            title: stripSlidePlaceholders(lessonTitle) || lessonTitle,
            page_or_slide_number: existingLesson.sourceSlides?.[0] || existingLesson.id || 1,
            key_points: (existingLesson.key_points || existingLesson.keyTakeaways || []).map(stripSlidePlaceholders),
            safety_warnings: (existingLesson.safety_warnings || []).map(stripSlidePlaceholders),
            suggested_next_transition: 'Proceed to the next section when ready.',
            visuals: existingLesson.visuals || [],
            sourceSlides: existingLesson.sourceSlides || [],
            language: targetLangInfo,
            cached: true,
            fetchDurationMs: fetchDuration
          });
        }
      }
    }

    // For interactive courses: if lesson wasn't directly found by id/title, look up slide
    if (isInteractive && Array.isArray(curriculum.slides)) {
      const slideNum = parseInt(lessonIdStr, 10) || 1;
      const targetSlide = curriculum.slides.find(s => s.slideNumber === slideNum) || curriculum.slides[slideNum - 1] || curriculum.slides[0];
      if (targetSlide) {
        const exp = targetSlide.explanations?.[targetLangInfo.code] || targetSlide.spokenExplanation || targetSlide.explanation || targetSlide.title;
        const cleanExp = cleanUnsupportedWords(stripSlidePlaceholders(exp || ''));
        return res.json({
          explanation: cleanExp,
          spoken_explanation: cleanExp,
          title: stripSlidePlaceholders(targetSlide.title) || lessonTitle,
          page_or_slide_number: targetSlide.slideNumber || slideNum,
          key_points: (targetSlide.keyPoints || targetSlide.bulletPoints || []).map(stripSlidePlaceholders),
          safety_warnings: [],
          suggested_next_transition: 'Proceed to the next section when ready.',
          visuals: [],
          sourceSlides: [targetSlide.slideNumber || slideNum],
          language: targetLangInfo,
          cached: true,
          fetchDurationMs: Date.now() - fetchStartTime
        });
      }
    }

    // 2. FAST PATH 2: Instant Database Retrieval from CourseChunk table (0 slow external LLM calls)
    const ragStartTime = Date.now();
    console.log(`[PERF:RAG_START] Fetching semantic knowledge chunks directly from PostgreSQL for lesson "${lessonTitle}" (Course ID: ${course.id})`);

    let chunks = [];
    try {
      chunks = await vectorChunkService.querySimilarChunks(prisma, course.id, lessonTitle, 6, {
        includeNeighbors: true,
        lessonTitle,
        topicFilter: lessonTitle
      });
      const ragDuration = Date.now() - ragStartTime;
      console.log(`[PERF:RAG_END] Retrieved ${chunks.length} chunks directly from pgvector in ${ragDuration}ms.`);
    } catch (e) {
      const ragDuration = Date.now() - ragStartTime;
      console.warn(`[PERF:RAG_END] pgvector query notice (${ragDuration}ms):`, e.message);
    }

    // Collect visuals from matched chunks or curriculum
    let matchingLesson = null;
    if (Array.isArray(curriculum?.lessons)) {
      matchingLesson = curriculum.lessons.find(l => String(l.id) === lessonIdStr || l.title === lessonTitle);
    }
    const retrievedVisuals = matchingLesson?.visuals || chunks.flatMap(c => c.metadata?.visuals || c.visuals || []);
    const uniqueVisuals = [];
    for (const v of retrievedVisuals) {
      if (v && !uniqueVisuals.some(uv => uv.id === v.id || (uv.dataUrl && uv.dataUrl === v.dataUrl))) {
        uniqueVisuals.push(v);
      }
    }

    // Synthesize structured blackboard lesson notes in the selected language only.
    // Source chunks (OCR / PPT) may be Bangla even when the student chose English.
    let explanation;
    const sourceScript = synthesizeLessonFromDbChunks(lessonTitle, chunks, course.title, matchingLesson);
    const sourceAlreadyInTarget = textMatchesLanguage(sourceScript, targetLangInfo.code);

    if (targetLangInfo.code === 'en' && sourceAlreadyInTarget) {
      explanation = sourceScript;
    } else {
      try {
        console.log(`[PERF:LLM_START] Generating lesson explanation in ${targetLanguage} (${targetLangInfo.code})...`);
        explanation = await aiService.explainLesson(course.title, lessonTitle, sourceScript, targetLanguage);
        console.log(`[PERF:LLM_END] Multilingual explanation generated in ${targetLanguage}`);
      } catch (err) {
        console.warn(`[PERF:LLM_END] AI multilingual fallback triggered for ${targetLanguage}:`, err.message);
        explanation = aiService._getMockExplanation(lessonTitle, targetLanguage);
      }
    }

    if (explanation && !textMatchesLanguage(explanation, targetLangInfo.code)) {
      try {
        console.log(`[Language Lock] Regenerating lesson "${lessonTitle}" because output was not ${targetLanguage}`);
        explanation = await aiService.explainLesson(course.title, lessonTitle, sourceScript, targetLanguage);
      } catch (err) {
        explanation = aiService._getMockExplanation(lessonTitle, targetLanguage);
      }
    }

    if (!explanation || explanation.length < 50) {
      explanation = aiService._getMockExplanation(lessonTitle, targetLanguage);
    }

    // 3. PERSISTENCE: Save synthesized explanation into PostgreSQL Course.curriculum for instant future opens
    try {
      if (Array.isArray(curriculum.lessons)) {
        const targetLesson = curriculum.lessons.find(
          l => String(l.id) === lessonIdStr || l.title === lessonTitle
        );
        if (targetLesson) {
          if (!targetLesson.explanations) targetLesson.explanations = {};
          if (textMatchesLanguage(explanation, targetLangInfo.code)) {
            targetLesson.explanations[targetLangInfo.code] = explanation;
            if (targetLangInfo.code === 'en') {
              targetLesson.explanation = explanation;
            }
          }
          await prisma.course.update({
            where: { id: course.id },
            data: { curriculum: curriculum }
          });
          console.log(`[Persisted Cache] Successfully persisted explanation for lesson "${lessonTitle}" (${targetLangInfo.code}) into Course.curriculum`);
        }
      }
    } catch (persistErr) {
      console.warn(`[Persisted Cache] Notice on persisting lesson explanation:`, persistErr.message);
    }

    // Generate structured Qwen AI Tutor guidance (spoken_explanation, key_points, safety_warnings, suggested_next_transition)
    let structuredTutor = null;
    try {
      structuredTutor = await aiService.explainVisibleContent({
        pageNumber: matchingLesson?.sourceSlides?.[0] || matchingLesson?.id || 1,
        title: lessonTitle,
        text: explanation,
        visuals: uniqueVisuals
      }, course.title, targetLanguage);
    } catch (_) { }

    const totalDuration = Date.now() - fetchStartTime;
    console.log(`[PERF:LESSON_FETCH_END] Total instant lesson fetch completed in ${totalDuration}ms (0 slow external LLM delay)`);

    res.json({
      explanation: structuredTutor?.spoken_explanation || explanation,
      spoken_explanation: structuredTutor?.spoken_explanation || explanation,
      title: structuredTutor?.title || lessonTitle,
      page_or_slide_number: structuredTutor?.page_or_slide_number || matchingLesson?.sourceSlides?.[0] || 1,
      key_points: structuredTutor?.key_points || [],
      safety_warnings: structuredTutor?.safety_warnings || [],
      suggested_next_transition: structuredTutor?.suggested_next_transition || 'Proceed to the next section when ready.',
      visuals: uniqueVisuals,
      sourceSlides: matchingLesson?.sourceSlides || [],
      language: targetLangInfo,
      cached: false,
      fetchDurationMs: totalDuration,
      ragDurationMs: Date.now() - ragStartTime
    });
  } catch (error) {
    console.error('Failed to generate dynamic lesson guide:', error);
    res.status(500).json({ error: 'Failed to generate custom lesson guide.' });
  }
});

// 4c. Generate structured Qwen explanation for a specific page/slide in the universal document viewer
router.post('/:id/page/:pageNum/explain', authenticateToken, async (req, res) => {
  try {
    const course = await prisma.course.findUnique({
      where: { id: req.params.id }
    });
    if (!course) return res.status(404).json({ error: 'Course not found.' });

    const hasAccess = await checkCourseAccess(req, course.id);
    if (!hasAccess) return res.status(403).json({ error: 'Access denied.' });

    const pageNum = parseInt(req.params.pageNum, 10) || 1;
    const { language } = req.body;
    const targetLangInfo = resolveLanguageInfo(language || req.user?.preferredLanguage);

    let curriculum = course.curriculum;
    if (typeof curriculum === 'string') {
      try { curriculum = JSON.parse(curriculum); } catch { curriculum = {}; }
    }

    const slides = curriculum?.slides || [];
    const pages = curriculum?.pages || [];
    const targetSlide = slides.find(s => s.slideNumber === pageNum || s.sourceSlide === pageNum);
    const targetPage = pages.find(p => p.page_number === pageNum || p.pageNumber === pageNum);

    const title = targetSlide?.title || targetPage?.title || `Slide ${pageNum}`;
    const rawTeachingText = targetSlide
      ? (aiService._slideTeachingText(targetSlide) || targetSlide.fullText || targetSlide.title || '')
      : (targetPage?.text || targetPage?.title || '');

    // Search for a pre-computed semantic knowledge chunk for this specific slide
    let chunkContext = '';
    try {
      const targetPrisma = req.prisma || prisma;
      const matchingChunk = await targetPrisma.courseChunk.findFirst({
        where: {
          courseId: course.id,
          OR: [
            { sourcePage: pageNum },
            { sourceSection: { contains: `${pageNum}` } }
          ]
        }
      });
      if (matchingChunk?.chunkText) {
        chunkContext = matchingChunk.chunkText;
      }
    } catch (_) { }

    const fullSlideContent = [chunkContext, rawTeachingText].filter(Boolean).join('\n\n');
    const visuals = targetSlide?.images || targetPage?.images || (targetPage?.pageImage ? [targetPage.pageImage] : []);
    const cleanBullets = (targetSlide?.bulletPoints || [])
      .map(b => stripSlidePlaceholders(b))
      .filter(b => b && !isBinaryNoiseText(b));

    const cleanFullContent = cleanUnsupportedWords(stripSlidePlaceholders(fullSlideContent));

    const structured = await aiService.explainVisibleContent({
      pageNumber: pageNum,
      title: stripSlidePlaceholders(title) || `Slide ${pageNum}`,
      text: cleanFullContent,
      bulletPoints: cleanBullets,
      visuals
    }, course.title, targetLangInfo.name);

    res.json({
      ...structured,
      visuals,
      language: targetLangInfo
    });
  } catch (err) {
    console.error('Failed to explain page content:', err);
    res.status(500).json({ error: 'Failed to explain page content.' });
  }
});

// Multilingual Quiz Templates for instant, high-quality question generation across all 6 supported languages
const MULTILINGUAL_QUIZ_TEMPLATES = {
  en: {
    questionPrefix: (topic) => `According to "${topic}", which of the following is correct?`,
    generalQuestions: [
      {
        question: (t) => `What is the primary operational objective emphasized in "${t}"?`,
        correctOption: `Strict compliance with verified operating standards and hazard prevention protocols`,
        distractors: [
          `Ignoring pre-operational checklists to save time`,
          `Operating equipment without mandatory personal protective equipment (PPE)`,
          `Delegating safety accountability solely to external supervisors`
        ],
        explanation: (t) => `Strict adherence to verified standards and hazard prevention protocols ensures worker safety in ${t}.`
      },
      {
        question: (t) => `How should safety checklists and procedures in "${t}" be executed?`,
        correctOption: `Systematically verified item-by-item prior to commencing any task`,
        distractors: [
          `Completed at the end of the shift from memory`,
          `Skipped entirely if the equipment looks intact`,
          `Only performed during annual audit inspections`
        ],
        explanation: (t) => `Pre-task verification confirms that equipment and surroundings meet all safety requirements before operation starts.`
      },
      {
        question: (t) => `What is the mandatory action if an abnormality or safety hazard is identified in "${t}"?`,
        correctOption: `Immediately stop work, notify the supervisor, and apply hazard control protocols`,
        distractors: [
          `Continue work cautiously without informing anyone`,
          `Attempt unauthorized modifications to bypass the issue`,
          `Wait until the next scheduled maintenance period to report`
        ],
        explanation: (t) => `Immediate hazard reporting and work cessation prevent minor defects from escalating into severe incidents.`
      },
      {
        question: (t) => `Who holds direct responsibility for maintaining safety standards in "${t}"?`,
        correctOption: `Every operator, driver, and team member on duty`,
        distractors: [
          `Only the plant general manager`,
          `Exclusively external safety auditors`,
          `Only newly hired trainees during probation`
        ],
        explanation: (t) => `Safety is a collective, non-delegable responsibility shared by all active personnel.`
      },
      {
        question: (t) => `What is the primary benefit of consistent operational discipline in "${t}"?`,
        correctOption: `Minimized accident risk, higher operational efficiency, and a safe workplace`,
        distractors: [
          `Increased downtime and reduced productivity`,
          `Elimination of the need for future equipment maintenance`,
          `Permission to bypass legal regulatory requirements`
        ],
        explanation: (t) => `Consistent operational discipline and standard compliance create a productive, incident-free environment.`
      },
      {
        question: (t) => `What is required before operating machinery or vehicles in "${t}"?`,
        correctOption: `Complete mandatory walk-around visual inspection and verify critical safety controls`,
        distractors: [
          `Commence full-speed operation immediately to meet targets`,
          `Rely solely on previous operator's verbal feedback without checking`,
          `Assume all safety systems are active without testing`
        ],
        explanation: (t) => `Conducting a pre-start walk-around inspection verifies that no blind-spot hazards or mechanical faults exist.`
      },
      {
        question: (t) => `When personal protective equipment (PPE) is designated in "${t}", what is the rule?`,
        correctOption: `Mandatory at all times within designated operational zones without exception`,
        distractors: [
          `Optional if the operator feels experienced enough`,
          `Only needed when managers or auditors are present`,
          `Can be removed if working conditions feel warm or inconvenient`
        ],
        explanation: (t) => `Approved PPE provides critical defense against severe injuries and is non-negotiable in operational zones.`
      },
      {
        question: (t) => `How should emergency communication channels be utilized during an incident in "${t}"?`,
        correctOption: `Alert team members immediately via designated radios/alarms and follow evacuation procedures`,
        distractors: [
          `Wait for third-party confirmation before signaling emergencies`,
          `Post on social media or send informal text messages`,
          `Ignore the warning unless alarms sound continuously for ten minutes`
        ],
        explanation: (t) => `Rapid, clear communication through established channels saves lives during emergencies.`
      },
      {
        question: (t) => `What documentation is mandatory following incident or near-miss observations in "${t}"?`,
        correctOption: `Accurate, transparent logging in the safety reporting system for root-cause analysis`,
        distractors: [
          `Informal verbal notes that are discarded after the shift`,
          `No documentation if no physical property damage occurred`,
          `Altering report details to avoid team penalties`
        ],
        explanation: (t) => `Transparent near-miss reporting identifies systemic vulnerabilities before they result in injuries.`
      },
      {
        question: (t) => `What is the standard procedure when environmental or weather conditions compromise visibility in "${t}"?`,
        correctOption: `Reduce speed, increase following distances, activate warning lights, or suspend operation until safe`,
        distractors: [
          `Accelerate to complete the route before conditions worsen`,
          `Switch off warning lights to reduce glare`,
          `Disregard environmental guidelines if deadlines are pressing`
        ],
        explanation: (t) => `Adjusting operations to environmental hazards guarantees vehicle control and pedestrian safety.`
      }
    ],
    distractors: [
      `Disregard all requirements to expedite the work schedule`,
      `Bypass verification checks unless an explicit incident occurs`,
      `Assume standard operating conditions without performing required inspections`,
      `Delay reporting hazards until the end of the shift`,
      `Delegate safety accountability solely to external supervisors`,
      `Operate equipment without mandatory personal protective equipment (PPE)`,
      `Modify equipment or procedures without authorization`
    ]
  },
  zh: {
    questionPrefix: (topic) => `根据“${topic}”，以下哪项表述是正确的？`,
    generalQuestions: [
      {
        question: (t) => `“${t}”中强调的首要操作目标是什么？`,
        correctOption: `严格遵守经过验证的操作标准和隐患预防规程`,
        distractors: [
          `忽视操作前检查清单以节省时间`,
          `在未佩戴强制性个人防护装备 (PPE) 的情况下操作设备`,
          `将安全责任完全推给外部主管`
        ],
        explanation: (t) => `严格遵守标准规程可确保人员安全和操作合规。`
      },
      {
        question: (t) => `在“${t}”中应如何执行安全检查清单和规程？`,
        correctOption: `在开始任何任务之前按清单逐项系统核实`,
        distractors: [
          `班次结束时仅凭记忆填写检查清单`,
          `如果设备外观完好则完全跳过检查`,
          `仅在年度审核检查时执行相关规程`
        ],
        explanation: (t) => `作业前核实可确认设备和作业环境符合所有安全要求。`
      },
      {
        question: (t) => `如果在“${t}”操作中发现异常或安全隐患，必须采取什么行动？`,
        correctOption: `立即停止工作，通知主管，并落实隐患控制规程`,
        distractors: [
          `不通知任何人而谨慎继续作业`,
          `尝试未经授权的改装以绕过问题`,
          `等到下一次定期维护时再报告`
        ],
        explanation: (t) => `立即报告隐患并停工可防止小缺陷演变成重大安全事故。`
      },
      {
        question: (t) => `谁对维护“${t}”中的安全标准负有直接责任？`,
        correctOption: `在岗的每位操作员、驾驶员和团队成员`,
        distractors: [
          `仅工厂总经理负责`,
          `仅外部安全审核员负责`,
          `仅试用期新员工负责`
        ],
        explanation: (t) => `安全是所有在岗人员共同承担、不可推卸的责任。`
      },
      {
        question: (t) => `在“${t}”中保持一致的操作纪律和安全标准的主要益处是什么？`,
        correctOption: `最大限度减少事故风险，提高运营效率，保障作业安全`,
        distractors: [
          `增加停机时间并降低整体生产力`,
          `消除未来设备维护的所有需求`,
          `获得绕过法定法规要求的许可`
        ],
        explanation: (t) => `严谨一致的操作纪律可创造零事故的高效工作环境。`
      },
      {
        question: (t) => `在“${t}”中操作机械或车辆前，必须完成什么？`,
        correctOption: `完成强制性环车外观检查并测试关键安全控制装置`,
        distractors: [
          `立即全速运转以赶上工作进度`,
          `仅依赖上一班操作员的口头告知而不作检查`,
          `不经测试即假定所有安全装置均已生效`
        ],
        explanation: (t) => `环车检查可确认无盲区危险或机械故障。`
      },
      {
        question: (t) => `在“${t}”规定的作业区域内，对个人防护装备 (PPE) 的要求是什么？`,
        correctOption: `在指定作业区域内必须全程佩戴，绝无例外`,
        distractors: [
          `经验丰富的操作员可自选佩戴`,
          `仅在管理人员或审核员在场时佩戴`,
          `天气炎热或感到不便时可随意取下`
        ],
        explanation: (t) => `认证的个人防护装备是防范伤害的关键防线，在作业区内必须全程佩戴。`
      },
      {
        question: (t) => `在“${t}”发生突发事件时应如何使用应急通信渠道？`,
        correctOption: `立即通过对讲机或警报通知团队成员，并遵循疏散程序`,
        distractors: [
          `等待第三方确认后再发出应急信号`,
          `在社交媒体发布信息或发送私人短信`,
          `除非警报持续十分钟否则不予理会`
        ],
        explanation: (t) => `通过既定渠道快速、清晰地沟通是应急响应的关键。`
      },
      {
        question: (t) => `在“${t}”中发现险肇事件（未遂事故）后必须进行什么记录？`,
        correctOption: `在安全报告系统中准确、透明地记录，以便进行根本原因分析`,
        distractors: [
          `仅做口头记录并在交接班后遗弃`,
          `若未造成财物损坏则无需记录`,
          `为避免团队受罚而修改事实详情`
        ],
        explanation: (t) => `透明报告险肇事件有助于在发生伤亡之前消除系统性隐患。`
      },
      {
        question: (t) => `当恶劣天气或环境条件导致“${t}”视线受阻时，标准程序是什么？`,
        correctOption: `降低车速、拉大安全车距、开启警示灯，必要时暂停作业直至安全`,
        distractors: [
          `加速行驶以便在情况恶化前完成任务`,
          `关闭警示灯以减少眩光影响`,
          `若工期紧张则无需顾及环境指南`
        ],
        explanation: (t) => `根据环境风险调整操作可确保车辆受控和人员安全。`
      }
    ],
    distractors: [
      `忽视所有要求以加快工作进度`,
      `除非发生明显事故，否则跳过核实检查`,
      `在不执行所需检查的情况下假定工作条件正常`,
      `将隐患报告推迟到班次结束或每周例会`,
      `将安全责任完全推给外部主管`,
      `在未佩戴强制性个人防护装备 (PPE) 的情况下操作设备`,
      `擅自更改设备或操作规程`
    ]
  },
  ms: {
    questionPrefix: (topic) => `Berdasarkan "${topic}", manakah antara berikut adalah betul?`,
    generalQuestions: [
      {
        question: (t) => `Apakah objektif operasi utama yang ditekankan dalam "${t}"?`,
        correctOption: `Pematuhan ketat kepada piawaian operasi yang disahkan dan protokol pencegahan bahaya`,
        distractors: [
          `Mengabaikan senarai semak sebelum operasi untuk menjimatkan masa`,
          `Mengendalikan peralatan tanpa peralatan perlindungan diri (PPE) mandatori`,
          `Menyerahkan akauntabiliti keselamatan hanya kepada penyelia luar`
        ],
        explanation: (t) => `Pematuhan kepada piawaian yang disahkan memastikan keselamatan pekerja dalam ${t}.`
      },
      {
        question: (t) => `Bagaimanakah senarai semak dan prosedur keselamatan dalam "${t}" harus dilaksanakan?`,
        correctOption: `Disahkan secara sistematik item demi item sebelum memulakan sebarang tugas`,
        distractors: [
          `Diselesaikan pada akhir syif daripada ingatan semata-mata`,
          `Dilangkau sepenuhnya jika peralatan kelihatan utuh`,
          `Hanya dilakukan semasa pemeriksaan audit tahunan`
        ],
        explanation: (t) => `Pengesahan pra-tugas mengesahkan peralatan dan persekitaran memenuhi semua syarat keselamatan.`
      },
      {
        question: (t) => `Apakah tindakan mandatori jika keabnormalan atau bahaya keselamatan dikesan dalam "${t}"?`,
        correctOption: `Hentikan kerja serta-merta, maklumkan penyelia, dan laksanakan kawalan bahaya`,
        distractors: [
          `Teruskan kerja dengan berhati-hati tanpa memaklumkan sesiapa`,
          `Cuba pengubahsuaian tanpa kebenaran untuk memintas isu tersebut`,
          `Tunggu sehingga tempoh penyelenggaraan berjadual seterusnya`
        ],
        explanation: (t) => `Pelaporan bahaya serta-merta mengelakkan kerosakan kecil menjadi insiden parah.`
      },
      {
        question: (t) => `Siapakah yang memegang tanggungjawab langsung untuk mengekalkan piawaian keselamatan dalam "${t}"?`,
        correctOption: `Setiap operator, pemandu, dan ahli pasukan yang bertugas`,
        distractors: [
          `Hanya pengurus besar kilang`,
          `Hanya juruaudit keselamatan luar`,
          `Hanya pelatih baharu semasa tempoh percubaan`
        ],
        explanation: (t) => `Keselamatan adalah tanggungjawab bersama yang tidak boleh diagihkan.`
      },
      {
        question: (t) => `Apakah faedah utama disiplin operasi yang konsisten dalam "${t}"?`,
        correctOption: `Meminimumkan risiko kemalangan, kecekapan operasi lebih tinggi, dan tempat kerja selamat`,
        distractors: [
          `Peningkatan masa henti dan pengurangan produktiviti`,
          `Penghapusan keperluan untuk penyelenggaraan peralatan pada masa hadapan`,
          `Kebenaran untuk memintas keperluan undang-undang kawal selia`
        ],
        explanation: (t) => `Disiplin operasi yang konsisten mewujudkan persekitaran kerja tanpa kemalangan.`
      },
      {
        question: (t) => `Apakah yang wajib dilakukan sebelum mengendalikan jentera atau kenderaan dalam "${t}"?`,
        correctOption: `Lengkapkan pemeriksaan visual keliling kenderaan dan uji kawalan keselamatan kritikal`,
        distractors: [
          `Mulakan operasi kelajuan penuh segera untuk mengejar sasaran`,
          `Hanya bergantung pada kata-kata operator sebelum ini tanpa memeriksa`,
          `Anggap semua sistem keselamatan berfungsi tanpa mengujinya`
        ],
        explanation: (t) => `Pemeriksaan keliling mengesahkan tiada bahaya titik buta atau kerosakan mekanikal.`
      },
      {
        question: (t) => `Apabila peralatan perlindungan diri (PPE) ditetapkan dalam "${t}", apakah peraturannya?`,
        correctOption: `Wajib dipakai pada setiap masa di zon operasi yang ditetapkan tanpa pengecualian`,
        distractors: [
          `Pilihan jika operator berasa sudah berpengalaman`,
          `Hanya perlu apabila pengurus atau juruaudit hadir`,
          `Boleh ditanggalkan jika cuaca panas atau tidak selesa`
        ],
        explanation: (t) => `PPE yang diluluskan memberikan pertahanan kritikal terhadap kecederaan parah.`
      },
      {
        question: (t) => `Bagaimanakah saluran komunikasi kecemasan harus digunakan semasa insiden dalam "${t}"?`,
        correctOption: `Maklumkan ahli pasukan serta-merta melalui radio/penggera yang ditetapkan dan patuhi pemindahan`,
        distractors: [
          `Tunggu pengesahan pihak ketiga sebelum memberi amaran kecemasan`,
          `Kemas kini media sosial atau hantar mesej teks tidak rasmi`,
          `Abaikan amaran melainkan penggera berbunyi selama sepuluh minit`
        ],
        explanation: (t) => `Komunikasi pantas dan jelas melalui saluran yang ditetapkan menyelamatkan nyawa.`
      },
      {
        question: (t) => `Apakah dokumentasi wajib berikutan insiden atau pemerhatian hampir-nahas dalam "${t}"?`,
        correctOption: `Catatan yang tepat dan telus dalam sistem laporan keselamatan untuk analisis punca`,
        distractors: [
          `Nota lisan tidak rasmi yang dibuang selepas syif tamat`,
          `Tiada dokumentasi diperlukan jika tiada kerosakan harta benda fizikal`,
          `Mengubah butiran laporan untuk mengelakkan penalti pasukan`
        ],
        explanation: (t) => `Pelaporan hampir-nahas yang telus mengenal pasti kelemahan sistemik sebelum kecederaan berlaku.`
      },
      {
        question: (t) => `Apakah prosedur standard apabila cuaca atau persekitaran menjejaskan penglihatan dalam "${t}"?`,
        correctOption: `Kurangkan kelajuan, jarakkan kenderaan, nyalakan lampu amaran, atau hentikan operasi sehingga selamat`,
        distractors: [
          `Memecut laju untuk menamatkan perjalanan sebelum cuaca bertambah buruk`,
          `Matikan lampu amaran untuk mengurangkan silau`,
          `Abaikan garis panduan cuaca jika tarikh akhir mendesak`
        ],
        explanation: (t) => `Menyesuaikan operasi kepada bahaya persekitaran menjamin kawalan kenderaan dan keselamatan pejalan kaki.`
      }
    ],
    distractors: [
      `Mengabaikan semua keperluan untuk mempercepatkan jadual kerja`,
      `Melangkau pemeriksaan pengesahan melainkan insiden nyata berlaku`,
      `Menganggap keadaan operasi standard tanpa melakukan pemeriksaan yang diperlukan`,
      `Menangguhkan pelaporan bahaya sehingga akhir syif`,
      `Menyerahkan akauntabiliti keselamatan hanya kepada penyelia luar`,
      `Mengendalikan peralatan tanpa peralatan perlindungan diri (PPE) mandatori`,
      `Mengubah suai peralatan atau prosedur tanpa kebenaran sah`
    ]
  },
  ta: {
    questionPrefix: (topic) => `"${topic}" இன் படி, பின்வருவனவற்றில் எது சரியானது?`,
    generalQuestions: [
      {
        question: (t) => `"${t}" இல் வலியுறுத்தப்படும் முதன்மை செயல்பாட்டு நோக்கம் என்ன?`,
        correctOption: `சரிபார்க்கப்பட்ட இயக்கத் தரநிலைகள் மற்றும் அபாயத் தடுப்பு நெறிமுறைகளுக்குக் கண்டிப்பாகக் கட்டுப்படுதல்`,
        distractors: [
          `நேரத்தைச் சேமிக்க முன்-செயல்பாட்டு சரிபார்ப்புப் பட்டியல்களைப் புறக்கணித்தல்`,
          `கட்டாய தனிநபர் பாதுகாப்பு உபகரணங்கள் (PPE) இல்லாமல் உபகரணங்களை இயக்குதல்`,
          `பாதுகாப்புப் பொறுப்பை வெளிப்புற மேற்பார்வையாளர்களுக்கு மட்டுமே மாற்றுதல்`
        ],
        explanation: (t) => `சரிபார்க்கப்பட்ட தரநிலைகளைப் பின்பற்றுவது பணியாளர் பாதுகாப்பை உறுதி செய்கிறது.`
      },
      {
        question: (t) => `"${t}" இல் பாதுகாப்பு சரிபார்ப்புப் பட்டியல்கள் மற்றும் நடைமுறைகள் எவ்வாறு செயல்படுத்தப்பட வேண்டும்?`,
        correctOption: `எந்தவொரு பணியையும் தொடங்குவதற்கு முன் உருப்படி வாரியாக முறையாக சரிபார்த்தல்`,
        distractors: [
          `பணி நேரம் முடியும் போது நினைவிலிருந்து மட்டும் முடித்தல்`,
          `உபகரணங்கள் சேதமடையாமல் இருப்பதாகத் தோன்றினால் முற்றிலும் தவிர்த்தல்`,
          `வருடாந்திர தணிக்கை ஆய்வுகளின் போது மட்டுமே செய்தல்`
        ],
        explanation: (t) => `பணிக்கு முந்தைய சரிபார்ப்பு உபகரணங்கள் பாதுகாப்புத் தேவைகளைப் பூர்த்தி செய்வதை உறுதி செய்கிறது.`
      },
      {
        question: (t) => `"${t}" இல் ஏதேனும் அசாதாரணம் அல்லது பாதுகாப்பு ஆபத்து கண்டறியப்பட்டால் கட்டாய நடவடிக்கை என்ன?`,
        correctOption: `உடனடியாக வேலையை நிறுத்துங்கள், மேற்பார்வையாளருக்குத் தெரிவிக்கவும், ஆபத்துக் கட்டுப்பாட்டைப் பயன்படுத்தவும்`,
        distractors: [
          `யாருக்கும் தெரிவிக்காமல் எச்சரிக்கையுடன் தொடர்ந்து பணியாற்றுங்கள்`,
          `சிக்கலைத் தவிர்க்க அங்கீகரிக்கப்படாத மாற்றங்களைச் செய்ய முயற்சிப்பது`,
          `அடுத்த திட்டமிடப்பட்ட பராமரிப்பு காலம் வரை காத்திருந்து புகாரளிப்பது`
        ],
        explanation: (t) => `உடனடி ஆபத்து அறிக்கை கடுமையான விபத்துக்களாக மாறுவதைத் தடுக்கிறது.`
      },
      {
        question: (t) => `"${t}" இல் பாதுகாப்பு தரநிலைகளை பராமரிப்பதில் நேரடிப் பொறுப்பு யாருக்கு உள்ளது?`,
        correctOption: `பணியில் உள்ள ஒவ்வொரு ஆபரேட்டர், ஓட்டுநர் மற்றும் குழு உறுப்பினர்`,
        distractors: [
          `தொழிற்சாலை பொது மேலாளர் மட்டுமே`,
          `வெளிப்புற பாதுகாப்பு தணிக்கையாளர்கள் மட்டுமே`,
          `பயிற்சி காலத்தில் உள்ள புதிய பணியாளர்கள் மட்டுமே`
        ],
        explanation: (t) => `பாதுகாப்பு என்பது பணியில் உள்ள அனைவரும் கூட்டாகப் பகிர்ந்து கொள்ளும் பொறுப்பாகும்.`
      },
      {
        question: (t) => `"${t}" இல் நிலையான செயல்பாட்டு ஒழுக்கம் மற்றும் பாதுகாப்பைக் கடைப்பிடிப்பதன் முதன்மை நன்மை என்ன?`,
        correctOption: `விபத்து ஆபத்தைக் குறைத்தல், அதிக செயல்பாட்டுத் திறன் மற்றும் பாதுகாப்பான பணியிடம்`,
        distractors: [
          `அதிகரித்த பணி தாமதம் மற்றும் குறைந்த உற்பத்தித்திறன்`,
          `எதிர்கால உபகரண பராமரிப்புக்கான தேவையை நீக்குதல்`,
          `சட்டப்பூர்வ ஒழுங்குமுறைத் தேவைகளைத் தவிர்ப்பதற்கான அனுமதி`
        ],
        explanation: (t) => `நிலையான செயல்பாட்டு ஒழுக்கம் விபத்தில்லாத பாதுகாப்பான சூழலை உருவாக்குகிறது.`
      },
      {
        question: (t) => `"${t}" இல் இயந்திரங்கள் அல்லது வாகனங்களை இயக்குவதற்கு முன் என்ன தேவை?`,
        correctOption: `கட்டாய பார்வை ஆய்வை முடித்து முக்கியமான கட்டுப்பாடுகளைச் சோதிக்கவும்`,
        distractors: [
          `இலக்குகளை அடைய உடனடியாக முழு வேகத்தில் இயக்கத் தொடங்குங்கள்`,
          `முந்தைய ஆபரேட்டரின் வாய்மொழி கருத்தை மட்டுமே நம்பி ஆய்வு செய்யாமல் இருப்பது`,
          `அனைத்து பாதுகாப்பு அமைப்புகளும் சோதிக்கப்படாமல் செயல்படுவதாகக் கருதுதல்`
        ],
        explanation: (t) => `முன்-தொடக்க ஆய்வு இயந்திரக் கோளாறுகள் அல்லது பார்வைக் குறைபாடுகள் இல்லை என்பதை உறுதிப்படுத்துகிறது.`
      },
      {
        question: (t) => `"${t}" இல் தனிநபர் பாதுகாப்பு உபகரணங்கள் (PPE) குறித்த விதி என்ன?`,
        correctOption: `குறிப்பிடப்பட்ட செயல்பாட்டு மண்டலங்களுக்குள் எப்போதும் விதிவிலக்கு இல்லாமல் கட்டாயமாகும்`,
        distractors: [
          `ஆபரேட்டர் அனுபவமுள்ளவராக உணர்ந்தால் விருப்பமானது`,
          `மேலாளர்கள் அல்லது தணிக்கையாளர்கள் இருக்கும்போது மட்டுமே தேவை`,
          `வெப்பமாக உணர்ந்தால் அல்லது அசௌகரியமாக இருந்தால் அகற்றலாம்`
        ],
        explanation: (t) => `அங்கீகரிக்கப்பட்ட PPE கடுமையான காயங்களுக்கு எதிராக முக்கியமான பாதுகாப்பை வழங்குகிறது.`
      },
      {
        question: (t) => `"${t}" இல் ஒரு சம்பவத்தின் போது அவசரகால தகவல்தொடர்பு எவ்வாறு பயன்படுத்தப்பட வேண்டும்?`,
        correctOption: `ரேடியோக்கள்/அலாரங்கள் மூலம் குழு உறுப்பினர்களை உடனடியாக எச்சரித்து வெளியேறவும்`,
        distractors: [
          `அவசரநிலையை அறிவிப்பதற்கு முன் மூன்றாம் தரப்பு உறுதிப்படுத்தலுக்காக காத்திருங்கள்`,
          `சமூக ஊடகங்களில் பதிவிடவும் அல்லது முறைசாரா உரைச் செய்திகளை அனுப்பவும்`,
          `அலாரங்கள் பத்து நிமிடங்கள் ஒலிக்கும் வரை எச்சரிக்கையைப் புறக்கணிக்கவும்`
        ],
        explanation: (t) => `நிறுவப்பட்ட சேனல்கள் மூலம் வேகமான, தெளிவான தகவல் தொடர்பு உயிர்களைக் காப்பாற்றுகிறது.`
      },
      {
        question: (t) => `"${t}" இல் விபத்து அல்லது விபத்து போன்ற சூழ்நிலைகளுக்குப் பின் என்ன ஆவணப்படுத்தல் கட்டாயமாகும்?`,
        correctOption: `மூல காரண பகுப்பாய்விற்காக பாதுகாப்பு அறிக்கையிடல் அமைப்பில் துல்லியமான பதிவு`,
        distractors: [
          `பணி முடிந்த பிறகு நிராகரிக்கப்படும் முறைசாரா வாய்மொழி குறிப்புகள்`,
          `உடல் சேதம் ஏற்படவில்லை என்றால் எந்த ஆவணமும் தேவையில்லை`,
          `அபராதங்களைத் தவிர்க்க அறிக்கையின் விவரங்களை மாற்றுதல்`
        ],
        explanation: (t) => `துல்லியமான அறிக்கையிடல் காயங்கள் ஏற்படுவதற்கு முன்பே பலவீனங்களை அடையாளம் காட்டுகிறது.`
      },
      {
        question: (t) => `"${t}" இல் வானிலை அல்லது சுற்றுச்சூழல் பார்வைத்திறனைப் பாதிக்கும் போது நிலையான நடைமுறை என்ன?`,
        correctOption: `வேகத்தைக் குறைத்து, இடைவெளியை அதிகரித்து, எச்சரிக்கை விளக்குகளை இயக்கவும்`,
        distractors: [
          `நிலைமை மோசமடைவதற்கு முன் வழியை முடிக்க வேகத்தை அதிகரிக்கவும்`,
          `பார்வை கூச்சத்தைக் குறைக்க எச்சரிக்கை விளக்குகளை அணைக்கவும்`,
          `காலக்கெடு நெருங்கினால் சுற்றுச்சூழல் வழிகாட்டுதல்களைப் புறக்கணிக்கவும்`
        ],
        explanation: (t) => `சுற்றுச்சூழல் அபாயங்களுக்கு ஏற்ப செயல்பாடுகளை சரிசெய்வது வாகனக் கட்டுப்பாட்டை உறுதி செய்கிறது.`
      }
    ],
    distractors: [
      `வேலை அட்டவணையை விரைவுபடுத்த அனைத்து தேவைகளையும் புறக்கணித்தல்`,
      `வெளிப்படையான விபத்து நிகழும் வரை சரிபார்ப்பு சோதனைகளைத் தவிர்த்தல்`,
      `தேவையான ஆய்வுகளைச் செய்யாமல் இயக்க நிலைமைகள் சாதாரணமாக இருப்பதாகக் கருதுதல்`,
      `ஆபத்துகளைப் புகாரளிப்பதை பணி முடியும் வரை தாமதப்படுத்துதல்`,
      `பாதுகாப்புப் பொறுப்பை வெளிப்புற மேற்பார்வையாளர்களுக்கு மட்டுமே மாற்றுதல்`,
      `கட்டாய தனிநபர் பாதுகாப்பு உபகரணங்கள் (PPE) இல்லாமல் உபகரணங்களை இயக்குதல்`,
      `அங்கீகரிக்கப்படாத உபகரணங்கள் அல்லது நடைமுறை மாற்றங்களைச் செய்தல்`
    ]
  },
  te: {
    questionPrefix: (topic) => `"${topic}" ప్రకారం, క్రింది వాటిలో ఏది సరైనది?`,
    generalQuestions: [
      {
        question: (t) => `"${t}" లో నొక్కిచెప్పబడిన ప్రాథమిక కార్యాచరణ లక్ష్యం ఏమిటి?`,
        correctOption: `ధృవీకరించబడిన ఆపరేటింగ్ ప్రమాణాలు మరియు ప్రమాద నివారణ ప్రోటోకాల్‌లకు ఖచ్చితంగా కట్టుబడి ఉండటం`,
        distractors: [
          `సమయాన్ని ఆదా చేయడానికి ఆపరేషన్‌కు ముందు చెక్‌లిస్టులను విస్మరించడం`,
          `తప్పనిసరి వ్యక్తిగత రక్షణ పరికరాలు (PPE) లేకుండా పరికరాలను ఆపరేట్ చేయడం`,
          `భద్రతా జవాబుదారీతనాన్ని బాహ్య పర్యవేక్షకులకు మాత్రమే అప్పగించడం`
        ],
        explanation: (t) => `ధృవీకరించబడిన ప్రమాణాలను పాటించడం కార్మికుల భద్రతను నిర్ధారిస్తుంది.`
      },
      {
        question: (t) => `"${t}" లో భద్రతా చెక్‌లిస్టులు మరియు విధానాలను ఎలా అమలు చేయాలి?`,
        correctOption: `ఏదైనా పనిని ప్రారంభించే ముందు అంశాల వారీగా క్రమపద్ధతిలో ధృవీకరించడం`,
        distractors: [
          `షిఫ్ట్ ముగింపులో జ్ఞాపకశక్తి నుండి మాత్రమే పూరించడం`,
          `పరికరాలు బాగానే కనిపిస్తే తనిఖీని పూర్తిగా దాటవేయడం`,
          `వార్షిక ఆడిట్ తనిఖీల సమయంలో మాత్రమే నిర్వహించడం`
        ],
        explanation: (t) => `పని ప్రారంభానికి ముందు ధృవీకరణ పరికరాలు మరియు పరిసరాలు భద్రతా ప్రమాణాలకు అనుగుణంగా ఉన్నాయని నిర్ధారిస్తుంది.`
      },
      {
        question: (t) => `"${t}" లో ఏదైనా అసాధారణత లేదా భద్రతా ప్రమాదం గుర్తించబడితే తప్పనిసరి చర్య ఏమిటి?`,
        correctOption: `వెంటనే పనిని నిలిపివేయండి, పర్యవేక్షకుడికి తెలియజేయండి మరియు ప్రమాద నియంత్రణ ప్రోటోకాల్‌లను వర్తింపజేయండి`,
        distractors: [
          `ఎవరికీ చెప్పకుండా జాగ్రత్తగా పనిని కొనసాగించడం`,
          `సమస్యను అధిగమించడానికి అనధికారిక మార్పులను ప్రయత్నించడం`,
          `తదుపరి షెడ్యూల్ చేసిన నిర్వహణ కాలం వరకు వేచి ఉండి నివేదించడం`
        ],
        explanation: (t) => `తక్షణ ప్రమాద నివేదన చిన్న లోపాలు తీవ్రమైన ప్రమాదాలుగా మారకుండా నిరోధిస్తుంది.`
      },
      {
        question: (t) => `"${t}" లో భద్రతా ప్రమాణాలను నిర్వహించడానికి ప్రత్యక్ష బాధ్యత ఎవరికి ఉంటుంది?`,
        correctOption: `విధి నిర్వహణలో ఉన్న ప్రతి ఆపరేటర్, డ్రైవర్ మరియు బృంద సభ్యుడు`,
        distractors: [
          `ప్లాంట్ జనరల్ మేనేజర్ మాత్రమే`,
          `ప్రత్యేకంగా బాహ్య భద్రతా ఆడిటర్లు మాత్రమే`,
          `ప్రొబేషన్ సమయంలో కొత్తగా చేరిన ట్రైనీలు మాత్రమే`
        ],
        explanation: (t) => `భద్రత అనేది విధుల్లో ఉన్న ప్రతి ఒక్కరి సామూహిక బాధ్యత.`
      },
      {
        question: (t) => `"${t}" లో స్థిరమైన కార్యాచరణ క్రమశిక్షణ పాటించడం వల్ల కలిగే ప్రధాన ప్రయోజనం ఏమిటి?`,
        correctOption: `ప్రమాద ప్రమాదాన్ని తగ్గించడం, అధిక కార్యాచరణ సామర్థ్యం మరియు సురక్షితమైన కార్యాలయం`,
        distractors: [
          `పని వేళల్లో అంతరాయం పెరగడం మరియు ఉత్పాదకత తగ్గడం`,
          `భవిష్యత్ పరికరాల నిర్వహణ అవసరాన్ని తొలగించడం`,
          `చట్టపరమైన నియంత్రణ అవసరాలను దాటవేయడానికి అనుమతి`
        ],
        explanation: (t) => `స్థిరమైన క్రమశిక్షణ ప్రమాద రహిత సురక్షిత పని వాతావరణాన్ని సృష్టిస్తుంది.`
      },
      {
        question: (t) => `"${t}" లో యంత్రాలు లేదా వాహనాలను నడపడానికి ముందు ఏమి అవసరం?`,
        correctOption: `వాహనం చుట్టూ నడిచి విజువల్ ఇన్‌స్పెక్షన్ పూర్తి చేయడం మరియు భద్రతా నియంత్రణలను ధృవీకరించడం`,
        distractors: [
          `టార్గెట్‌లను చేరుకోవడానికి వెంటనే పూర్తి వేగంతో ఆపరేషన్ ప్రారంభించడం`,
          `తనిఖీ చేయకుండా మునుపటి ఆపరేటర్ మాటలపై మాత్రమే ఆధారపడటం`,
          `పరీక్షించకుండానే అన్ని భద్రతా వ్యవస్థలు పనిచేస్తున్నాయని భావించడం`
        ],
        explanation: (t) => `ముందస్తు తనిఖీ ద్వారా అంధ ప్రదేశాల ప్రమాదాలు లేదా యాంత్రిక లోపాలు లేవని నిర్ధారిస్తుంది.`
      },
      {
        question: (t) => `"${t}" లో వ్యక్తిగత రక్షణ పరికరాల (PPE) నియమం ఏమిటి?`,
        correctOption: `నిర్దేశిత ప్రాంతాలలో ఎటువంటి మినహాయింపు లేకుండా ఎల్లవేళలా తప్పనిసరి`,
        distractors: [
          `ఆపరేటర్ అనుభవజ్ఞుడిగా భావిస్తే ఐచ్ఛికం`,
          `మేనేజర్లు లేదా ఆడిటర్లు ఉన్నప్పుడు మాత్రమే అవసరం`,
          `వేడిగా లేదా అసౌకర్యంగా అనిపిస్తే తీసివేయవచ్చు`
        ],
        explanation: (t) => `ఆమోదించబడిన PPE తీవ్రమైన గాయాల నుండి రక్షణ కల్పిస్తుంది.`
      },
      {
        question: (t) => `"${t}" లో అత్యవసర పరిస్థితుల్లో కమ్యూనికేషన్ ఎలా ఉండాలి?`,
        correctOption: `రేడియోలు/అలారాల ద్వారా బృంద సభ్యులను వెంటనే అప్రమత్తం చేసి ఖాళీ చేసే విధానాలను అనుసరించండి`,
        distractors: [
          `అత్యవసర పరిస్థితిని ప్రకటించే ముందు మూడవ పక్ష నిర్ధారణ కోసం వేచి ఉండండి`,
          `సోషల్ మీడియాలో పోస్ట్ చేయండి లేదా అనధికారిక సందేశాలు పంపండి`,
          `అలారాలు పది నిమిషాలు మోగే వరకు హెచ్చరికను విస్మరించండి`
        ],
        explanation: (t) => `ఏర్పాటు చేసిన ఛానెల్‌ల ద్వారా వేగవంతమైన, స్పష్టమైన సమాచారం ప్రాణాలను కాపాడుతుంది.`
      },
      {
        question: (t) => `"${t}" లో ప్రమాదం లేదా ప్రమాదం తప్పిన సంఘటన తర్వాత ఏ డాక్యుమెంటేషన్ తప్పనిసరి?`,
        correctOption: `భద్రతా రిపోర్టింగ్ సిస్టమ్‌లో మూల కారణ విశ్లేషణ కోసం ఖచ్చితమైన డాక్యుమెంటేషన్`,
        distractors: [
          `షిఫ్ట్ ముగిసిన తర్వాత విస్మరించబడే అనధికారిక మౌఖిక గమనికలు`,
          `భౌతిక ఆస్తి నష్టం జరగకపోతే డాక్యుమెంటేషన్ అవసరం లేదు`,
          `జరిమానాలను నివారించడానికి నివేదిక వివరాలను మార్చడం`
        ],
        explanation: (t) => `ఖచ్చితమైన రిపోర్టింగ్ గాయాలు సంభవించే ముందే లోపాలను సరిదిద్దడానికి సహాయపడుతుంది.`
      },
      {
        question: (t) => `వాతావరణం లేదా పర్యావరణ పరిస్థితులు "${t}" లో దృశ్యమానతను ప్రభావితం చేసినప్పుడు ప్రామాణిక విధానం ఏమిటి?`,
        correctOption: `వేగాన్ని తగ్గించండి, వాహనాల మధ్య దూరాన్ని పెంచండి, హెచ్చరిక లైట్లను ఆన్ చేయండి`,
        distractors: [
          `పరిస్థితులు విషమించేలోపు మార్గాన్ని పూర్తి చేయడానికి వేగాన్ని పెంచడం`,
          `కాంతిని తగ్గించడానికి హెచ్చరిక లైట్లను ఆపివేయడం`,
          `గడువు దగ్గరపడితే పర్యావరణ మార్గదర్శకాలను విస్మరించడం`
        ],
        explanation: (t) => `ప్రమాదకర పరిస్థితులకు అనుగుణంగా ఆపరేషన్‌ను మార్చడం ద్వారా వాహనంపై నియంత్రణ మరియు భద్రత చేకూరుతాయి.`
      }
    ],
    distractors: [
      `పని షెడ్యూల్‌ను వేగవంతం చేయడానికి అన్ని అవసరాలను విస్మరించడం`,
      `స్పష్టమైన సంఘటన జరిగే వరకు ధృవీకరణ తనిఖీలను దాటవేయడం`,
      `అవసరమైన తనిఖీలు చేయకుండా ఆపరేటింగ్ పరిస్థితులు సాధారణంగా ఉన్నాయని భావించడం`,
      `ప్రమాదాలను నివేదించడాన్ని షిఫ్ట్ ముగిసే వరకు ఆలస్యం చేయడం`,
      `భద్రతా జవాబుదారీతనాన్ని బాహ్య పర్యవేక్షకులకు మాత్రమే అప్పగించడం`,
      `తప్పనిసరి వ్యక్తిగత రక్షణ పరికరాలు (PPE) లేకుండా పరికరాలను ఆపరేట్ చేయడం`,
      `అనధికారికంగా పరికరాలు లేదా విధానాలలో మార్పులు చేయడం`
    ]
  },
  bn: {
    questionPrefix: (topic) => `"${topic}" অনুসারে, নিচের কোনটি সঠিক?`,
    generalQuestions: [
      {
        question: (t) => `"${t}"-এ প্রধান অপারেশনাল উদ্দেশ্য কী তুলে ধরা হয়েছে?`,
        correctOption: `যাচাইকৃত অপারেটিং মান এবং বিপদ প্রতিরোধ প্রোটোকল কঠোরভাবে মেনে চলা`,
        distractors: [
          `সময় বাঁচাতে অপারেশন পূর্ববর্তী চেকলিস্ট উপেক্ষা করা`,
          `বাধ্যতামূলক ব্যক্তিগত সুরক্ষা সরঞ্জাম (PPE) ছাড়া সরঞ্জাম পরিচালনা করা`,
          `সুরক্ষার দায়িত্ব কেবল বহিরাগত সুপারভাইজারদের উপর অর্পণ করা`
        ],
        explanation: (t) => `যাচাইকৃত অপারেটিং মান মেনে চলা কর্মীদের সুরক্ষা নিশ্চিত করে।`
      },
      {
        question: (t) => `"${t}"-এ সুরক্ষা চেকলিস্ট এবং পদ্ধতিগুলি কীভাবে সম্পাদন করা উচিত?`,
        correctOption: `যেকোনো কাজ শুরু করার আগে প্রতিটি আইটেম পদ্ধতিগতভাবে যাচাই করা`,
        distractors: [
          `শিফটের শেষে কেবল স্মৃতি থেকে পূরণ করা`,
          `সরঞ্জাম অক্ষত দেখা দিলে সম্পূর্ণ বাদ দেওয়া`,
          `কেবলমাত্র বার্ষিক অডিট পরিদর্শনের সময় করা`
        ],
        explanation: (t) => `কাজের পূর্ববর্তী যাচাইকরণ নিশ্চিত করে যে সরঞ্জাম এবং পরিবেশ সমস্ত প্রয়োজনীয়তা পূরণ করে।`
      },
      {
        question: (t) => `"${t}"-এ কোনো অস্বাভাবিকতা বা সুরক্ষার ঝুঁকি দেখা দিলে বাধ্যতামূলক পদক্ষেপ কী?`,
        correctOption: `অবিলম্বে কাজ বন্ধ করুন, সুপারভাইজারকে জানান এবং বিপদ নিয়ন্ত্রণ প্রোটোকল প্রয়োগ করুন`,
        distractors: [
          `কাউকে না জানিয়ে সতর্কতার সাথে কাজ চালিয়ে যাওয়া`,
          `সমস্যাটি এড়িয়ে যাওয়ার জন্য অননুমোদিত পরিবর্তনের চেষ্টা করা`,
          `পরবর্তী নির্ধারিত রক্ষণাবেক্ষণের সময় পর্যন্ত অপেক্ষা করা`
        ],
        explanation: (t) => `অবিলম্বে বিপদের রিপোর্ট করা ছোটখাটো ত্রুটিকে বড় দুর্ঘটনায় রূপ নিতে বাধা দেয়।`
      },
      {
        question: (t) => `"${t}"-এ সুরক্ষার মান বজায় রাখার সরাসরি দায়িত্ব কার?`,
        correctOption: `দায়িত্বরত প্রতিটি অপারেটর, ড্রাইভার এবং দলের সদস্য`,
        distractors: [
          `কেবল কারখানার জেনারেল ম্যানেজার`,
          `কেবল বহিরাগত সুরক্ষা নিরীক্ষক`,
          `প্রবেশনারি সময়ের নতুন প্রশিক্ষণার্থী`
        ],
        explanation: (t) => `সুরক্ষা একটি যৌথ দায়িত্ব যা প্রত্যেক সক্রিয় সদস্য বহন করে।`
      },
      {
        question: (t) => `"${t}"-এ ধারাবাহিক অপারেশনাল শৃঙ্খলা ও সুরক্ষা মেনে চলার প্রধান সুবিধা কী?`,
        correctOption: `দুর্ঘটনার ঝুঁকি হ্রাস, উচ্চতর অপারেশনাল দক্ষতা এবং একটি নিরাপদ কর্মক্ষেত্র`,
        distractors: [
          `কাজের বিলম্ব বৃদ্ধি এবং উত্পাদনশীলতা হ্রাস`,
          `ভবিষ্যতের সরঞ্জাম রক্ষণাবেক্ষণের প্রয়োজনীয়তা দূর করা`,
          `আইনগত নিয়ন্ত্রক প্রয়োজনীয়তা এড়িয়ে যাওয়ার অনুমতি`
        ],
        explanation: (t) => `ধারাবাহিক শৃঙ্খলা একটি নিরাপদ এবং দুর্ঘটনা-মুক্ত কর্মক্ষেত্র গড়ে তোলে।`
      },
      {
        question: (t) => `"${t}"-এ যন্ত্রপাতি বা যানবাহন পরিচালনার আগে কী প্রয়োজন?`,
        correctOption: `বাধ্যতামূলক ভিজ্যুয়াল পরিদর্শন সম্পন্ন করা এবং গুরুত্বপূর্ণ সুরক্ষা নিয়ন্ত্রণ পরীক্ষা করা`,
        distractors: [
          `লক্ষ্য অর্জনের জন্য অবিলম্বে পূর্ণ গতিতে পরিচালনা শুরু করা`,
          `যাচাই না করে কেবল পূর্ববর্তী অপারেটরের মৌখিক প্রতিক্রিয়ার উপর নির্ভর করা`,
          `পরীক্ষা না করেই ধরে নেওয়া যে সমস্ত সুরক্ষা ব্যবস্থা কার্যকর আছে`
        ],
        explanation: (t) => `পূর্ববর্তী পরিদর্শন নিশ্চিত করে যে কোনো যান্ত্রিক ত্রুটি বা লুকানো ঝুঁকি নেই।`
      },
      {
        question: (t) => `"${t}"-এ ব্যক্তিগত সুরক্ষা সরঞ্জাম (PPE) ব্যবহারের নিয়ম কী?`,
        correctOption: `নির্দিষ্ট কার্যক্ষেত্রে কোনো ব্যতিক্রম ছাড়াই সর্বদা বাধ্যতামূলক`,
        distractors: [
          `অপারেটর অভিজ্ঞ মনে করলে ঐচ্ছিক`,
          `কেবলমাত্র ম্যানেজার বা নিরীক্ষকদের উপস্থিতিতে প্রয়োজন`,
          `গরম বা অস্বস্তি বোধ হলে খুলে ফেলা যায়`
        ],
        explanation: (t) => `অনুমোদিত PPE গুরুতর আঘাতের বিরুদ্ধে সমালোচনামূলক সুরক্ষা প্রদান করে।`
      },
      {
        question: (t) => `"${t}"-এ কোনো ঘটনার সময় জরুরি যোগাযোগ ব্যবস্থা কীভাবে ব্যবহার করা উচিত?`,
        correctOption: `নির্দিষ্ট রেডিও/অ্যালার্মের মাধ্যমে দলের সদস্যদের অবিলম্বে সতর্ক করুন এবং নির্দেশিকা অনুসরণ করুন`,
        distractors: [
          `জরুরি অবস্থা ঘোষণা করার আগে তৃতীয় পক্ষের নিশ্চিতকরণের জন্য অপেক্ষা করুন`,
          `সোশ্যাল মিডিয়ায় পোস্ট করুন বা অনানুষ্ঠানিক বার্তা পাঠান`,
          `অ্যালার্ম দশ মিনিট ধরে না বাজা পর্যন্ত সতর্কতা উপেক্ষা করুন`
        ],
        explanation: (t) => `নির্ধারিত চ্যানেলের মাধ্যমে দ্রুত যোগাযোগ জীবন বাঁচায়।`
      },
      {
        question: (t) => `"${t}"-এ কোনো দুর্ঘটনা বা সম্ভাব্য দুর্ঘটনার পর কী নথিপত্র বাধ্যতামূলক?`,
        correctOption: `মূল কারণ বিশ্লেষণের জন্য সুরক্ষা রিপোর্টিং সিস্টেমে সঠিক এবং স্বচ্ছ নথিপত্র`,
        distractors: [
          `অনানুষ্ঠানিক মৌখিক নোট যা শিফট শেষে বাতিল করা হয়`,
          `শারীরিক ক্ষতি না হলে কোনো নথিপত্রের প্রয়োজন নেই`,
          `শাস্তি এড়াতে প্রতিবেদনের বিবরণ পরিবর্তন করা`
        ],
        explanation: (t) => `স্বচ্ছ রিপোর্টিং গুরুতর দুর্ঘটনার আগেই দুর্বলতা চিহ্নিত করে।`
      },
      {
        question: (t) => `"${t}"-এ আবহাওয়া বা পরিবেশগত কারণে দৃষ্টিসীমা কমে গেলে আদর্শ পদ্ধতি কী?`,
        correctOption: `গতি কমান, দূরত্ব বাড়ান, সতর্কবার্তা লাইট চালু করুন এবং প্রয়োজনে কাজ স্থগিত রাখুন`,
        distractors: [
          `পরিস্থিতি খারাপ হওয়ার আগেই দ্রুত পৌঁছানোর চেষ্টা করা`,
          `ঝলকানি কমাতে সতর্কবার্তা লাইট বন্ধ রাখা`,
          `সময়সীমা থাকলে নির্দেশিকা উপেক্ষা করা`
        ],
        explanation: (t) => `পরিবেশগত ঝুঁকির সাথে সামঞ্জস্য রেখে কাজ করা যানবাহনের নিয়ন্ত্রণ ও নিরাপত্তা নিশ্চিত করে।`
      }
    ],
    distractors: [
      `কাজের সময়সূচী ত্বরান্বিত করতে সমস্ত প্রয়োজনীয়তা উপেক্ষা করা`,
      `স্পষ্ট কোনো দুর্ঘটনা না ঘটা পর্যন্ত যাচাইকরণ পরীক্ষা বাদ দেওয়া`,
      `প্রয়োজনীয় পরিদর্শন না করেই স্ট্যান্ডার্ড অপারেটিং পরিস্থিতি ধরে নেওয়া`,
      `ঝুঁকির রিপোর্ট করা শিফটের শেষ পর্যন্ত বিলম্বিত করা`,
      `সুরক্ষার দায়িত্ব কেবল বহিরাগত সুপারভাইজারদের উপর অর্পণ করা`,
      `বাধ্যতামূলক ব্যক্তিগত সুরক্ষা সরঞ্জাম (PPE) ছাড়া সরঞ্জাম পরিচালনা করা`,
      `অনুমোদন ছাড়া সরঞ্জাম বা পদ্ধতিতে পরিবর্তন আনা`
    ]
  }
};

export function generateSlideGroundedQuiz(course, targetLesson, matchingSlides = [], targetLanguage = 'English', isFinalAssessment = false, requestedCount = 5) {
  const targetLangInfo = resolveLanguageInfo(targetLanguage);
  let langCode = targetLangInfo?.code || 'en';
  if (langCode === 'zh-CN') langCode = 'zh';

  const tpl = MULTILINGUAL_QUIZ_TEMPLATES[langCode] || MULTILINGUAL_QUIZ_TEMPLATES.en;
  const enTpl = MULTILINGUAL_QUIZ_TEMPLATES.en;

  const count = isFinalAssessment ? Math.max(10, requestedCount || 10) : 5;
  const slides = Array.isArray(matchingSlides) && matchingSlides.length > 0 ? matchingSlides : [];

  // Parse curriculum lessons for rich titles across languages
  let cur = course?.curriculum;
  if (typeof cur === 'string') {
    try { cur = JSON.parse(cur); } catch (_) { cur = {}; }
  }
  const allLessons = Array.isArray(cur?.lessons) ? cur.lessons : (Array.isArray(course?.lessons) ? course.lessons : []);

  const localizedLessonTitle = targetLesson?.titles?.[langCode] || targetLesson?.title || course?.title || 'Operational Standard';

  // 1. Gather all slide key points, bullets, and explanations in the target language
  const points = [];
  slides.forEach((s, idx) => {
    const sTitle = s.titles?.[langCode] || s.title || `Slide ${s.slideNumber || idx + 1}`;
    const slideExp = s.explanations?.[langCode] ||
      (langCode === 'zh' ? s.explanations?.['zh-CN'] : null) ||
      s.spokenExplanation || s.explanation || '';
    
    // Check localized key points array
    const rawBullets = s.keyPointsByLang?.[langCode] || s.bulletPoints || s.bullets || s.keyPoints || [];
    if (Array.isArray(rawBullets) && rawBullets.length > 0) {
      rawBullets.forEach(b => {
        const clean = String(b).replace(/[•\-*]/g, '').trim();
        if (clean.length > 6) points.push({ title: sTitle, text: clean, explanation: slideExp || clean });
      });
    }

    if (slideExp && slideExp.length > 25) {
      // Extract first meaningful sentence as point
      const firstSentence = slideExp.split(/[.!?。！？\n]/).filter(p => p.trim().length > 10)[0] || slideExp.slice(0, 100);
      points.push({ title: sTitle, text: firstSentence.trim(), explanation: slideExp });
    }
  });

  // If points are still few, also harvest from all lessons in course curriculum
  if (points.length < count && allLessons.length > 0) {
    allLessons.forEach(l => {
      const lTitle = l.titles?.[langCode] || l.title;
      const lExp = l.explanations?.[langCode] || l.explanation || '';
      const lPoints = l.keyPointsByLang?.[langCode] || l.keyTakeaways || [];
      if (Array.isArray(lPoints)) {
        lPoints.forEach(p => {
          const clean = String(p).replace(/[•\-*]/g, '').trim();
          if (clean.length > 6) points.push({ title: lTitle, text: clean, explanation: lExp || clean });
        });
      }
      if (lExp && lExp.length > 25) {
        const first = lExp.split(/[.!?。！？\n]/).filter(p => p.trim().length > 10)[0] || lExp.slice(0, 100);
        points.push({ title: lTitle, text: first.trim(), explanation: lExp });
      }
    });
  }

  const rawQuestions = [];
  const distractorPool = tpl.distractors && tpl.distractors.length > 0 ? tpl.distractors : enTpl.distractors;

  // Build questions directly grounded in the content
  points.slice(0, count).forEach((p, qIdx) => {
    // Pick 3 unique distractors randomly
    const shuffledDistractors = [...distractorPool].sort(() => 0.5 - Math.random()).slice(0, 3);
    rawQuestions.push({
      question: tpl.questionPrefix(p.title),
      correctOption: p.text,
      distractors: shuffledDistractors,
      explanation: p.explanation || (tpl.generalQuestions[0]?.explanation ? tpl.generalQuestions[0].explanation(p.title) : `Following ${p.title} ensures strict compliance.`)
    });
  });

  // Fill remainder up to target count (at least 10 for final assessment, 5 for regular)
  let generalIdx = 0;
  const questionsList = tpl.generalQuestions || enTpl.generalQuestions;
  while (rawQuestions.length < count) {
    const gen = questionsList[generalIdx % questionsList.length];
    // Spread title references across lessons if available
    const refLesson = allLessons[rawQuestions.length % Math.max(1, allLessons.length)];
    const refTitle = refLesson?.titles?.[langCode] || refLesson?.title || localizedLessonTitle;

    rawQuestions.push({
      question: gen.question(refTitle),
      correctOption: gen.correctOption,
      distractors: gen.distractors,
      explanation: gen.explanation(refTitle)
    });
    generalIdx++;
  }

  // Shuffle options for each question using Fisher-Yates and assign randomized answerIndex (0, 1, 2, 3)
  return rawQuestions.slice(0, count).map(q => {
    const opts = [q.correctOption, ...q.distractors];
    for (let i = opts.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [opts[i], opts[j]] = [opts[j], opts[i]];
    }
    const ansIdx = opts.indexOf(q.correctOption);
    return {
      question: q.question,
      options: opts,
      answerIndex: ansIdx >= 0 ? ansIdx : 0,
      explanation: q.explanation
    };
  });
}

// 4b. Generate AI Assessment Quiz for a specific lesson/chapter or Comprehensive Final Exam
router.post('/:id/lessons/:lessonId/quiz', authenticateToken, async (req, res) => {
  try {
    const { course, prismaClient } = await findCourseAcrossTenants(req, req.params.id);

    if (!course) {
      return res.status(404).json({ error: 'Course not found.' });
    }

    const hasAccess = await checkCourseAccess(req, course.id, prismaClient);
    if (!hasAccess) {
      return res.status(403).json({ error: 'Access denied. You are not assigned to this course.' });
    }

    const { lessonTitle, language, isFinalAssessment: reqIsFinal } = req.body;
    if (!lessonTitle && !reqIsFinal) {
      return res.status(400).json({ error: 'Lesson title is required.' });
    }

    const targetLangInfo = resolveLanguageInfo(language || req.user?.preferredLanguage);
    const targetLanguage = targetLangInfo.name;
    const lessonIdStr = String(req.params.lessonId);

    // Parse curriculum to find active lesson and matching slides
    let cur = course.curriculum;
    if (typeof cur === 'string') {
      try { cur = JSON.parse(cur); } catch (_) { cur = {}; }
    }
    const lessons = Array.isArray(cur?.lessons) ? cur.lessons : (Array.isArray(course.lessons) ? course.lessons : []);
    const targetLesson = lessons.find(l => String(l.id) === lessonIdStr || l.title === lessonTitle) || lessons[0];

    // Determine if this is the comprehensive final assessment across all modules
    const isFinalAssessment = Boolean(
      reqIsFinal ||
      req.query.isFinal === 'true' ||
      lessonIdStr === 'final' ||
      (lessons.length > 0 && String(targetLesson?.id) === String(lessons[lessons.length - 1]?.id))
    );

    const requiredQuestionCount = isFinalAssessment ? 10 : 5;
    const cacheKey = `${course.id}_${lessonIdStr}_${targetLangInfo.code}${isFinalAssessment ? '_final10' : ''}`;

    // Instant cache return (0ms)
    if (quizCache.has(cacheKey)) {
      console.log(`[Quiz Cache HIT] Returning cached quiz for lesson "${lessonTitle || 'Final'}" (${targetLangInfo.code}, final=${isFinalAssessment})`);
      return res.json({ quiz: quizCache.get(cacheKey), language: targetLangInfo, cached: true, isFinalAssessment });
    }

    // In-flight deduplication: await existing promise if another simultaneous request is generating
    if (inFlightQuizzes.has(cacheKey)) {
      console.log(`[Quiz IN-FLIGHT] Reusing in-flight quiz promise for lesson "${lessonTitle || 'Final'}" (${targetLangInfo.code})`);
      const existing = await inFlightQuizzes.get(cacheKey);
      return res.json({ quiz: existing, language: targetLangInfo, cached: true, isFinalAssessment });
    }

    console.log(`[Fast Quiz Gen] Language: "${targetLanguage}" (${targetLangInfo.code}), Lesson: "${lessonTitle || 'Final Exam'}", Final: ${isFinalAssessment}, Target Questions: ${requiredQuestionCount}`);

    const slides = Array.isArray(cur?.slides) ? cur.slides : [];
    const matchingSlides = isFinalAssessment ? slides : slides.filter(s => {
      if (targetLesson?.sourceSlides && Array.isArray(targetLesson.sourceSlides)) {
        return targetLesson.sourceSlides.includes(s.slideNumber);
      }
      return String(s.slideNumber) === String(targetLesson?.slideNumber) || s.moduleId === targetLesson?.moduleId;
    });

    let lessonContextText = '';
    if (matchingSlides.length > 0) {
      lessonContextText = matchingSlides.map(s => {
        const parts = [
          `Slide ${s.slideNumber}: ${s.title || ''}`,
          Array.isArray(s.bullets) ? s.bullets.join('\n') : '',
          s.explanation ? `Explanation: ${s.explanation}` : ''
        ].filter(Boolean);
        return parts.join('\n');
      }).join('\n\n');
    }
    if (!lessonContextText && targetLesson) {
      lessonContextText = `Lesson: ${targetLesson.title}\nDescription: ${targetLesson.description || ''}\n${targetLesson.content || ''}`;
    }

    // Truly randomize option positions with Fisher-Yates shuffle
    const randomizeOptions = (list) => {
      if (!Array.isArray(list)) return list;
      return list.map(q => {
        const opts = Array.isArray(q.options) ? [...q.options] : [];
        if (opts.length < 2) return q;
        const curIdx = q.answerIndex !== undefined ? q.answerIndex : (q.answer || 0);
        const correctVal = opts[curIdx] || opts[0];
        for (let i = opts.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [opts[i], opts[j]] = [opts[j], opts[i]];
        }
        const newIdx = opts.indexOf(correctVal);
        return {
          ...q,
          options: opts,
          answerIndex: newIdx >= 0 ? newIdx : 0
        };
      });
    };

    // Execute quiz generation with instant slide-grounded engine (racing AI Gateway with 3s ceiling)
    const quizPromise = (async () => {
      let quiz = null;
      try {
        if (!isFinalAssessment) {
          // For single lesson quiz, try AI Gateway if it responds within 3 seconds
          quiz = await Promise.race([
            aiService.generateLessonQuiz(course.title, lessonTitle, lessonContextText, targetLanguage),
            new Promise((_, reject) => setTimeout(() => reject(new Error('QUIZ_FAST_FALLBACK')), 3000))
          ]);
        }
      } catch (err) {
        // Fast fallback to instant slide-grounded multilingual generator
      }

      if (!quiz || !Array.isArray(quiz) || quiz.length < requiredQuestionCount) {
        quiz = generateSlideGroundedQuiz(course, targetLesson, matchingSlides, targetLanguage, isFinalAssessment, requiredQuestionCount);
      }

      quiz = randomizeOptions(quiz);
      quizCache.set(cacheKey, quiz);
      return quiz;
    })();

    inFlightQuizzes.set(cacheKey, quizPromise);
    const finalQuiz = await quizPromise;
    inFlightQuizzes.delete(cacheKey);

    res.json({ quiz: finalQuiz, language: targetLangInfo, isFinalAssessment, questionCount: finalQuiz.length });
  } catch (error) {
    console.error('Failed to generate assessment quiz:', error);
    res.status(500).json({ error: 'Failed to generate assessment quiz.' });
  }
});

// 4c. Get Student Course Progress
router.get('/:id/progress', authenticateToken, async (req, res) => {
  try {
    const userId = req.user.id || req.user.userId;
    const courseId = req.params.id;

    const hasAccess = await checkCourseAccess(req, courseId);
    if (!hasAccess) {
      return res.status(403).json({ error: 'Access denied. You are not assigned to this course.' });
    }

    const targetPrisma = req.prisma || prisma;
    const progressRecord = await targetPrisma.courseProgress.findFirst({
      where: { userId, courseId }
    });

    if (!progressRecord) {
      return res.json({
        progress: 0,
        completed: false,
        completedLessonIds: []
      });
    }

    let lessonIds = progressRecord.completedLessonIds;
    if (typeof lessonIds === 'string') {
      try { lessonIds = JSON.parse(lessonIds); } catch (e) { lessonIds = []; }
    }
    if (!Array.isArray(lessonIds)) lessonIds = [];

    // Deduplicate and normalize to strings
    const uniqueLessonIds = Array.from(new Set(lessonIds.filter(id => id !== null && id !== undefined).map(String)));

    res.json({
      progress: Math.min(100, Math.max(0, progressRecord.progress || 0)),
      completed: progressRecord.completed,
      completedLessonIds: uniqueLessonIds,
      updatedAt: progressRecord.updatedAt,
      startedAt: progressRecord.startedAt
    });
  } catch (error) {
    console.error('Failed to fetch course progress:', error);
    res.status(500).json({ error: 'Failed to retrieve course progress.' });
  }
});

// 4d. Save/Update Student Course Progress
router.post('/:id/progress', authenticateToken, async (req, res) => {
  try {
    const userId = req.user.id || req.user.userId;
    const courseId = req.params.id;

    const hasAccess = await checkCourseAccess(req, courseId);
    if (!hasAccess) {
      return res.status(403).json({ error: 'Access denied. You are not assigned to this course.' });
    }

    const { completedLessonId, completedLessonIds, progress, completed } = req.body;

    const targetPrisma = req.prisma || prisma;
    const existing = await targetPrisma.courseProgress.findFirst({
      where: { userId, courseId }
    });

    let currentCompleted = [];
    if (existing?.completedLessonIds) {
      try {
        const parsed = typeof existing.completedLessonIds === 'string'
          ? JSON.parse(existing.completedLessonIds)
          : existing.completedLessonIds;
        if (Array.isArray(parsed)) {
          currentCompleted = parsed.filter(id => id !== null && id !== undefined).map(String);
        }
      } catch (e) {
        currentCompleted = [];
      }
    }

    const completedSet = new Set(currentCompleted);
    if (completedLessonId !== undefined && completedLessonId !== null) {
      completedSet.add(String(completedLessonId));
    }
    if (Array.isArray(completedLessonIds)) {
      completedLessonIds.forEach(id => {
        if (id !== null && id !== undefined) {
          completedSet.add(String(id));
        }
      });
    }

    const finalCompleted = Array.from(completedSet);

    // Get total lessons count for this course to calculate accurate progress
    let totalLessons = 5;
    try {
      const { course: courseRecord } = await findCourseAcrossTenants(req, courseId);
      if (courseRecord && courseRecord.curriculum) {
        const curr = typeof courseRecord.curriculum === 'string' ? JSON.parse(courseRecord.curriculum) : courseRecord.curriculum;
        if (Array.isArray(curr.lessons) && curr.lessons.length > 0) {
          totalLessons = curr.lessons.length;
        } else if (Array.isArray(curr.curriculum) && curr.curriculum.length > 0) {
          totalLessons = curr.curriculum.length;
        }
      }
    } catch (e) { }

    const calcProgress = Math.min(100, Math.max(0, progress !== undefined ? progress : Math.round((finalCompleted.length / totalLessons) * 100)));
    const isCompleted = completed !== undefined ? completed : (calcProgress >= 100 || finalCompleted.length >= totalLessons);
    const jsonCompleted = JSON.stringify(finalCompleted);

    let record;
    try {
      record = await targetPrisma.courseProgress.upsert({
        where: {
          userId_courseId: { userId, courseId }
        },
        update: {
          progress: calcProgress,
          completed: isCompleted,
          completedLessonIds: jsonCompleted
        },
        create: {
          userId,
          courseId,
          progress: calcProgress,
          completed: isCompleted,
          completedLessonIds: jsonCompleted
        }
      });
    } catch (upsertErr) {
      const retryExisting = existing || await targetPrisma.courseProgress.findFirst({ where: { userId, courseId } });
      if (retryExisting) {
        record = await targetPrisma.courseProgress.update({
          where: { id: retryExisting.id },
          data: {
            progress: calcProgress,
            completed: isCompleted,
            completedLessonIds: jsonCompleted
          }
        });
      } else {
        throw upsertErr;
      }
    }

    res.json({
      message: 'Course progress saved successfully.',
      progress: record.progress,
      completed: record.completed,
      completedLessonIds: finalCompleted
    });
  } catch (error) {
    console.error('Failed to save course progress:', error);
    res.status(500).json({ error: 'Failed to save course progress.' });
  }
});

// 5. Chat with AI Tutor (ARIA) in context of this RAG course using semantic query search
router.post('/:id/chat', authenticateToken, async (req, res) => {
  try {
    const { course, prismaClient } = await findCourseAcrossTenants(req, req.params.id);

    if (!course) {
      return res.status(404).json({ error: 'Course not found.' });
    }

    const hasAccess = await checkCourseAccess(req, course.id, prismaClient);
    if (!hasAccess) {
      return res.status(403).json({ error: 'Access denied. You are not assigned to this course.' });
    }

    const { message, lessonTitle, currentConcept, language, conversationHistory } = req.body;
    if (!message) {
      return res.status(400).json({ error: 'Message is required.' });
    }

    // Resolve dynamic assistant name for the current tenant or request
    let aiTutorName = (req.body.aiName || req.body.assistantName || '').trim();
    if (!aiTutorName) {
      try {
        const rows = await masterPrisma.$queryRawUnsafe(
          `SELECT "aiName" FROM "Company" WHERE "code" = $1 LIMIT 1`,
          req.tenantCode || 'default'
        ).catch(() => []);
        aiTutorName = (rows[0]?.aiName || 'ARIA').trim();
      } catch (_) {
        aiTutorName = 'ARIA';
      }
    }
    if (!aiTutorName) aiTutorName = 'ARIA';

    const targetLangInfo = resolveLanguageInfo(language || req.user?.preferredLanguage);
    const targetLanguage = targetLangInfo.name;
    console.log(`[RAG Chat Query] Assistant: "${aiTutorName}", Target Language: "${targetLanguage}" (${targetLangInfo.code}), Query: "${message}" (Course ID: ${course.id}${currentConcept ? `, Concept: "${currentConcept.slice(0, 40)}..."` : ''})`);

    // Handle direct assistant identity / name query
    const isIdentityQuestion = /^(what('?s| is) your name|who are you|what should i call you|tell me your name|introduce yourself|who are u)/i.test(
      message.replace(/[?!.]/g, '').trim()
    );
    if (isIdentityQuestion) {
      const intro = `I am ${aiTutorName}, your AI voice tutor for this course. How can I help you understand this lesson?`;
      return res.json({
        reply: intro,
        text: intro,
        assistantName: aiTutorName,
        language: targetLangInfo
      });
    }

    const normalizeForEcho = (value) => String(value || '')
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const questionNorm = normalizeForEcho(message);
    const conceptNorm = normalizeForEcho(currentConcept);
    const isExplicitQuestion = /\b(what|why|how|when|where|who|which|can|could|would|should|is|are|do|does|did|tell|explain|clarify|mean|help|define|elaborate)\b/i.test(message)
      || message.includes('?')
      || /(?:什么是|为什么|怎么|如何|请问|apa|mengapa|bagaimana|bila|என்ன|ஏன்|எப்படி|কী|কেন|কিভাবে|ఎందుకు|ఏమిటి|ఎలా)/iu.test(message);

    // Only reject as an acoustic self-echo if there are NO question words AND it is a verbatim 85%+ replica of the active concept
    if (!isExplicitQuestion && conceptNorm.length > 50 && questionNorm.length > 50) {
      const qWords = questionNorm.split(' ').filter(w => w.length > 3);
      const cSet = new Set(conceptNorm.split(' ').filter(w => w.length > 3));
      const overlap = qWords.length ? qWords.filter(w => cSet.has(w)).length / qWords.length : 0;
      if (overlap >= 0.88 && conceptNorm.includes(questionNorm)) {
        console.log(`[RAG Chat] Suppressed acoustic self-echo (${Math.round(overlap * 100)}% overlap with active concept): "${message.slice(0, 50)}..."`);
        return res.json({
          reply: 'I did not catch a question. Please ask me something about this lesson.',
          text: 'I did not catch a question. Please ask me something about this lesson.',
          language: targetLangInfo
        });
      }
    }

    // Parse curriculum slides
    let cur = course.curriculum;
    if (typeof cur === 'string') {
      try { cur = JSON.parse(cur); } catch (_) { cur = {}; }
    }
    const slides = Array.isArray(cur?.slides) ? cur.slides : [];
    const slideContextText = slides.map(s => {
      const parts = [
        s.title ? `Topic: ${s.title}` : '',
        Array.isArray(s.bullets) ? s.bullets.join('. ') : '',
        s.explanation ? `Explanation: ${s.explanation}` : ''
      ].filter(Boolean);
      return parts.join('\n');
    }).filter(Boolean).join('\n\n');

    // Retrieve semantic context matching student query from PostgreSQL pgvector
    const ragChatStartTime = Date.now();
    console.log(`[PERF:RAG_START] Starting question vector query for message: "${message.slice(0, 50)}..." (Course ID: ${course.id})`);
    let chunks = [];
    try {
      chunks = await vectorChunkService.querySimilarChunks(prismaClient, course.id, message, 5, {
        lessonTitle,
        topicFilter: lessonTitle
      });
      const ragChatDuration = Date.now() - ragChatStartTime;
      console.log(`[PERF:RAG_END] Retrieved ${chunks.length} chunks from pgvector in ${ragChatDuration}ms.`);
    } catch (e) {
      const ragChatDuration = Date.now() - ragChatStartTime;
      console.warn(`[PERF:RAG_END] pgvector RAG query notice (${ragChatDuration}ms):`, e.message);
    }

    const MAX_CONTEXT_CHARS = 1200;
    let contextText = chunks.length > 0
      ? chunks.slice(0, 3).map(c => (c.text || '').trim()).filter(Boolean).join('\n\n')
      : (slideContextText || (course.fileText || '').slice(0, MAX_CONTEXT_CHARS));
    if (contextText.length > MAX_CONTEXT_CHARS) {
      contextText = contextText.slice(0, MAX_CONTEXT_CHARS);
    }
    const hasCourseContext = !!(contextText && contextText.trim());

    const systemPrompt = `You are ${aiTutorName}, a patient and accurate AI Tutor for SkillBridge.
You explain concepts clearly, use appropriate examples, and adapt explanations to the learner's level.
You are actively teaching the course: "${course.title}" (Current Lesson: "${lessonTitle || 'Overview'}").
${currentConcept ? `The student is currently viewing / listening to this lesson concept: "${currentConcept}".` : ''}
Selected Teaching Language: ${targetLanguage}.
Your name is strictly ${aiTutorName}. If asked who you are or what your name is, state that you are ${aiTutorName}.

CRITICAL CONCISE & UNDERSTANDABLE TUTORING RULES:
0. ANSWER DIRECTLY WITHOUT CITING SLIDES: Address the student's question directly with helpful, accurate information. NEVER cite slide numbers, slide titles, or say "From Slide X", "In Slide 8", "According to Slide 9", "As per page 2". Explain the concept naturally and directly.
1. SHORT & DIRECT (MAX 2-3 SENTENCES): Keep your answer SHORT, CONCISE, and TO THE POINT (around 35-60 words).
2. SIMPLE & INTUITIVE: Explain the concept in plain, simple, easily digestible words that any beginner can understand immediately.
3. VOICE & AUDIO FRIENDLY: Your answer will be spoken aloud to the student in real-time via text-to-speech.
4. TARGET TEACHING LANGUAGE: Generate your ENTIRE answer in ${targetLanguage} only. Do not mix languages.
5. TECHNICAL TERMS: Keep essential technical keywords in standard form alongside the explanation in ${targetLanguage}.
6. GROUNDING & DOMAIN KNOWLEDGE: Use the provided course excerpts and active lesson concept when relevant.
7. FRIENDLY TONE: Warm, natural, supportive, and direct.`;

    const userPrompt = `${hasCourseContext ? `Course Context:\n${contextText}\n\n` : ''}${currentConcept ? `Active Lesson Concept:\n${currentConcept}\n\n` : ''}Student Question:\n${message}\n\nSelected Response Language:\n${targetLanguage}`;

    const llmChatStartTime = Date.now();
    console.log(`[PERF:LLM_START] Generating tutor answer via local LLM gateway in ${targetLanguage} (${chunks.length} RAG chunks)...`);

    let reply;
    try {
      reply = await Promise.race([
        aiService._callLLM(systemPrompt, userPrompt, false, conversationHistory || []),
        new Promise((_, reject) => setTimeout(() => reject(new Error('TUTOR_TIMEOUT')), 35000))
      ]);
      const llmChatDuration = Date.now() - llmChatStartTime;
      console.log(`[PERF:LLM_END] Generated tutor answer via local LLM gateway in ${llmChatDuration}ms.`);
    } catch (e) {
      const llmChatDuration = Date.now() - llmChatStartTime;
      console.warn(`[PERF:LLM_END] Local LLM gateway notice (${llmChatDuration}ms): ${e.message}. Attempting intelligent knowledge fallback...`);

      // 1. Safe domain-grounded conversational answers first for high accuracy
      const lowerQ = message.toLowerCase();
      let safeAns = '';
      if (lowerQ.includes('5s') || lowerQ.includes('five s')) {
        safeAns = '5S is a systematic workplace organization method consisting of five steps: Sort, Set in Order, Shine, Standardize, and Sustain. It creates a clean, safe, and organized work environment.';
      } else if (lowerQ.includes('speed') || lowerQ.includes('limit') || lowerQ.includes('km')) {
        safeAns = 'The standard speed limit within the premises is 20 km/h. Drivers must always obey posted speed signs and exercise extreme caution near pedestrian walkways.';
      } else if (lowerQ.includes('ppe') || lowerQ.includes('protective') || lowerQ.includes('helmet') || lowerQ.includes('vest') || lowerQ.includes('shoe')) {
        safeAns = 'Personal Protective Equipment (PPE) including safety helmets, high-visibility vests, and safety shoes must be worn at all times in operational areas.';
      } else if (lowerQ.includes('pedestrian') || lowerQ.includes('walkway') || lowerQ.includes('zebra')) {
        safeAns = 'Always give way to pedestrians at designated walkways. Maintain awareness of moving equipment and obey all safety barriers.';
      }

      if (safeAns) {
        return res.json({ reply: safeAns, text: safeAns, language: targetLangInfo, fallback: true });
      }

      // 2. Intelligent high-availability fallback: answer directly from course slides
      if (slides.length > 0) {
        const queryWords = lowerQ.split(/\s+/).filter(w => w.length >= 2);
        const matched = slides.find(s => {
          const text = `${s.title || ''} ${Array.isArray(s.bullets) ? s.bullets.join(' ') : ''} ${s.explanation || ''}`.toLowerCase();
          return queryWords.some(w => text.includes(w));
        });

        if (matched) {
          const fallbackAns = matched.explanation || (Array.isArray(matched.bullets) && matched.bullets.length ? matched.bullets.slice(0, 3).join('. ') : `In this section on ${matched.title || 'this topic'}, remember to follow all verified operational procedures.`);
          return res.json({ reply: fallbackAns, text: fallbackAns, language: targetLangInfo, fallback: true });
        }
      }

      // 3. Fallback to clean excerpt from course text/context
      if (hasCourseContext && contextText) {
        const cleanContext = contextText.replace(/\s+/g, ' ').trim();
        const sentences = cleanContext.split(/(?<=[.?!])\s+/).filter(s => s.length > 15);
        const topAnswer = sentences.slice(0, 3).join(' ');
        if (topAnswer && topAnswer.length > 25) {
          reply = topAnswer;
          return res.json({ reply, text: reply, language: targetLangInfo, fallback: true });
        }
      }

      // 4. Context-grounded default response
      const defaultAns = currentConcept
        ? `In this lesson on ${course.title}, ${currentConcept} covers key operating procedures and safety standards.`
        : `In this section of ${course.title}, we emphasize practical workplace safety, standard operating procedures, and situational awareness.`;
      return res.json({ reply: defaultAns, text: defaultAns, language: targetLangInfo, fallback: true });
    }

    // Clean any accidental slide references from LLM reply (e.g. "From Slide 9...", "In slide 8, ...")
    if (reply && typeof reply === 'string') {
      reply = reply
        .replace(/^(?:from|in|according to|as per|on)\s+(?:slides?|pages?)\s+\d+[,.:\s-]*/i, '')
        .replace(/\b(?:from|in|according to|as per|on)\s+(?:slides?|pages?)\s+\d+[,.:\s-]*/gi, '')
        .replace(/\((?:slides?|pages?)\s+[\d,\s-]+\)/gi, '')
        .replace(/\s+/g, ' ')
        .trim();
    }

    res.json({ reply, text: reply, language: targetLangInfo });
  } catch (error) {
    console.error('Tutoring chat failure:', error);
    // Never fail with 500 when learner is talking to ARIA
    const friendlyFallback = 'In this course, we focus on verified operational safety, standard procedures, and compliance guidelines. How can I help you with this section?';
    res.json({ reply: friendlyFallback, text: friendlyFallback });
  }
});

// 6. Admin: Update a course module details (and optionally replace document)
router.put('/:id', authenticateToken, requireRole(['ADMIN']), upload.single('file'), async (req, res) => {
  try {
    const courseId = req.params.id;
    const { title, description, curriculum } = req.body;
    const targetPrisma = req.prisma || prisma;

    const existingCourse = await targetPrisma.course.findUnique({
      where: { id: courseId }
    });

    if (!existingCourse) {
      return res.status(404).json({ error: 'Course not found.' });
    }

    if (title !== undefined && (!title || !title.trim())) {
      return res.status(400).json({ error: 'Course title cannot be empty.' });
    }
    if (description !== undefined && (!description || !description.trim())) {
      return res.status(400).json({ error: 'Course description cannot be empty.' });
    }

    const updateData = {};
    const finalTitle = title ? title.trim() : existingCourse.title;
    const finalDescription = description ? description.trim() : existingCourse.description;

    updateData.title = finalTitle;
    updateData.description = finalDescription;

    if (req.file) {
      console.log(`[Document Extraction] Admin updating course "${finalTitle}" with new file: ${req.file.originalname}...`);
      const docResult = await extractDocumentContent(req.file);
      const { text: textContent, pages, slides, fileType } = docResult;

      const imageCount = (slides || []).reduce((n, s) => n + ((s.images || []).length), 0) + ((docResult.images || []).length);
      if (!textContent || textContent.length < 30) {
        return res.status(400).json({
          error: imageCount > 0
            ? 'The PowerPoint is image-based, but no readable training text could be found in the slide pictures. Try a PPTX with clearer photos, or add slide titles/notes.'
            : 'Failed to extract text from new document. File may be empty or unreadable.'
        });
      }

      const segmentsCount = slides.length > 0 ? `${slides.length} slide(s)` : `${pages.length} section(s)/page(s)`;
      console.log(`[Document Extraction] Extracted ${textContent.length} characters across ${segmentsCount} (${fileType.toUpperCase()}). Generating updated dynamic curriculum...`);
      const generatedCourse = await aiService.generateCourse(req.file.originalname, textContent, docResult);
      generatedCourse.title = finalTitle;
      generatedCourse.description = finalDescription;
      generatedCourse.fileType = fileType || docResult.fileType;
      generatedCourse.fileName = req.file.originalname;

      if (Array.isArray(docResult.slides) && docResult.slides.length > 0) {
        generatedCourse.slides = docResult.slides;
      }
      if (Array.isArray(docResult.pages) && docResult.pages.length > 0) {
        generatedCourse.pages = docResult.pages;
      }
      if (Array.isArray(docResult.images) && docResult.images.length > 0) {
        generatedCourse.visuals = docResult.images;
      }

      updateData.fileName = req.file.originalname;
      updateData.fileText = textContent;
      updateData.curriculum = generatedCourse;

      // Save updated original file buffer to disk
      try {
        const courseUploadDir = path.join(process.cwd(), 'uploads', 'courses', String(courseId));
        fs.mkdirSync(courseUploadDir, { recursive: true });
        fs.writeFileSync(path.join(courseUploadDir, req.file.originalname), req.file.buffer);
      } catch (fErr) {
        console.warn(`[File Storage] Notice updating course file on disk:`, fErr.message);
      }

      // Re-index vector chunks and embeddings for the new document
      try {
        console.log(`[pgvector] Re-indexing course ${courseId} with semantic comprehension...`);
        await vectorChunkService.indexCourse(targetPrisma, courseId, docResult, generatedCourse);
        console.log(`[pgvector] Vector embeddings updated successfully.`);
      } catch (vectorErr) {
        console.warn(`[pgvector] Re-indexing warning for course ${courseId}:`, vectorErr.message);
      }
    } else {
      let curriculumObj = curriculum !== undefined ? curriculum : existingCourse.curriculum;
      if (typeof curriculumObj === 'string') {
        try { curriculumObj = JSON.parse(curriculumObj); } catch (e) { }
      }
      if (curriculumObj && typeof curriculumObj === 'object') {
        curriculumObj.title = finalTitle;
        curriculumObj.description = finalDescription;
        updateData.curriculum = curriculumObj;
      }
    }

    const updatedCourse = await targetPrisma.course.update({
      where: { id: courseId },
      data: updateData
    });

    try {
      const summary = generateModulesSummary(updateData.curriculum || existingCourse.curriculum);
      await targetPrisma.$executeRaw`
        UPDATE "Course"
        SET "modulesSummary" = ${JSON.stringify(summary)}::jsonb
        WHERE id = ${courseId};
      `;
    } catch (_) { }
    invalidateCoursesCatalogCache(req.tenantCode);

    res.json({
      message: 'Course updated successfully.',
      course: updatedCourse
    });
  } catch (error) {
    console.error('Failed to update course:', error);
    res.status(500).json({ error: error.message || 'Failed to update course module.' });
  }
});

// 6b. Admin: Delete a course module
router.delete('/:id', authenticateToken, requireRole(['ADMIN']), async (req, res) => {
  try {
    const courseId = req.params.id;
    console.log(`[Courses] Admin deleting course ID: ${courseId}`);

    const targetPrisma = req.prisma || prisma;

    // Helper to safely clean up all relations and the course record from a Prisma client
    const purgeCourse = async (client) => {
      if (!client) return;
      try {
        await client.userCourseAssignment.deleteMany({ where: { courseId } });
      } catch (e) {
        console.warn('[Courses] userCourseAssignment cleanup notice:', e.message);
      }

      try {
        await client.courseProgress.deleteMany({ where: { courseId } });
      } catch (e) {
        console.warn('[Courses] courseProgress cleanup notice:', e.message);
      }

      try {
        await client.courseChunk.deleteMany({ where: { courseId } });
      } catch (e) {
        console.warn('[Courses] courseChunk cleanup notice:', e.message);
      }

      try {
        await client.course.delete({ where: { id: courseId } });
      } catch (e) {
        if (!e.message?.includes('Record to delete does not exist') && !e.code?.includes('P2025')) {
          console.warn('[Courses] course delete notice:', e.message);
        }
      }
    };

    // 1. Purge from active tenant database only (strict tenant isolation)
    await purgeCourse(targetPrisma);

    // 2. Remove uploaded original document files from disk
    try {
      const courseUploadDir = path.join(process.cwd(), 'uploads', 'courses', String(courseId));
      if (fs.existsSync(courseUploadDir)) {
        fs.rmSync(courseUploadDir, { recursive: true, force: true });
        console.log(`[Courses] Removed uploaded files for course ${courseId}`);
      }
    } catch (fsErr) {
      console.warn('[Courses] Notice removing course files:', fsErr.message);
    }

    invalidateCoursesCatalogCache(req.tenantCode);

    res.json({ message: 'Course module deleted successfully.' });
  } catch (error) {
    console.error('Failed to delete course:', error);
    res.status(500).json({ error: error.message || 'Failed to delete course module.' });
  }
});

// 7. Transcribe student audio speech to text using Python AI Service (faster-whisper) or fallbacks
router.post('/transcribe', authenticateToken, upload.single('file'), async (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: 'Audio file is required for transcription.' });
    }

    const requestedLang = req.body.language || req.query.language || '';
    const resolveLanguageCode = (lang) => {
      if (!lang) return null;
      const l = String(lang).toLowerCase().trim();
      if (l === 'zh' || l.startsWith('zh-') || l.includes('chinese') || l.includes('中文')) return 'zh';
      if (l === 'ms' || l.startsWith('ms-') || l.includes('malay') || l.includes('melayu')) return 'ms';
      if (l === 'ta' || l.startsWith('ta-') || l.includes('tamil') || l.includes('தமிழ்')) return 'ta';
      if (l === 'bn' || l.startsWith('bn-') || l.includes('bangla') || l.includes('bengali') || l.includes('বাংলা')) return 'bn';
      if (l === 'te' || l.startsWith('te-') || l.includes('telugu') || l.includes('తెలుగు')) return 'te';
      if (l === 'en' || l.startsWith('en-') || l.includes('english')) return 'en';
      return null;
    };

    const targetLangCode = resolveLanguageCode(requestedLang);

    const PYTHON_SERVICE_URL = process.env.PYTHON_SERVICE_URL || 'http://localhost:8000';
    const WHISPER_SERVER_URL = process.env.WHISPER_SERVER_URL || 'http://localhost:9000';
    console.log(`[Whisper Flow] Transcribing audio file: ${req.file.originalname || 'voice.webm'} (${req.file.size} bytes, targetLang: ${targetLangCode || 'auto'})...`);

    // 0. Primary: Central AI Gateway (POST /api/v1/stt/transcribe)
    try {
      const gwResult = await aiGatewayClient.transcribeAudio(
        req.file.buffer,
        req.file.originalname || 'voice.webm',
        targetLangCode
      );
      if (gwResult && typeof gwResult.text === 'string' && gwResult.text.trim()) {
        const text = gwResult.text.trim();
        console.log(`[Whisper Flow Central AI Gateway Result]: "${text}"`);
        return res.json({ text, detectedLanguage: targetLangCode || 'en' });
      }
    } catch (gwErr) {
      console.warn(`[Whisper Flow] Central AI Gateway transcribe notice (${gwErr.code || gwErr.message}). Trying secondary services...`);
    }

    // 1. Secondary: Python AI Microservice (faster-whisper on port 8000)
    try {
      const fd = new FormData();
      const blob = new Blob([req.file.buffer], { type: req.file.mimetype || 'audio/webm' });
      fd.append('file', blob, req.file.originalname || 'voice.webm');
      if (targetLangCode) {
        fd.append('language', targetLangCode);
      }

      const pyResponse = await axios.post(`${PYTHON_SERVICE_URL}/api/v1/transcribe`, fd, {
        timeout: 45000
      });

      if (pyResponse.data && typeof pyResponse.data.text === 'string') {
        const text = pyResponse.data.text.trim();
        const detectedLanguage = pyResponse.data.detected_language || targetLangCode || 'en';
        console.log(`[Whisper Flow Python AI Service Result]: "${text}" (detected: ${detectedLanguage})`);
        return res.json({ text, detectedLanguage });
      }
    } catch (pyErr) {
      console.warn(`[Whisper Flow] Python AI Service at ${PYTHON_SERVICE_URL} offline or unreachable: ${pyErr.message}. Trying Docker container...`);
    }

    // 2. Try Docker Whisper Container (onerahmet/openai-whisper-asr-webservice) on port 9000
    try {
      const form = new FormData();
      form.append('audio_file', req.file.buffer, {
        filename: req.file.originalname || 'voice.webm',
        contentType: req.file.mimetype || 'audio/webm'
      });

      const langQuery = targetLangCode ? `&language=${targetLangCode}` : '';
      const dockerResponse = await axios.post(`${WHISPER_SERVER_URL}/asr?output=json${langQuery}`, form, {
        headers: form.getHeaders(),
        timeout: 30000
      });

      let transcriptionText = '';
      if (typeof dockerResponse.data === 'string') {
        transcriptionText = dockerResponse.data.trim();
      } else if (dockerResponse.data && dockerResponse.data.text) {
        transcriptionText = dockerResponse.data.text.trim();
      }

      if (transcriptionText) {
        console.log(`[Whisper Flow Docker Result]: "${transcriptionText}"`);
        return res.json({ text: transcriptionText, detectedLanguage: targetLangCode || 'en' });
      }
    } catch (dockerErr) {
      console.warn(`[Whisper Flow] Docker container at ${WHISPER_SERVER_URL} offline or unreachable: ${dockerErr.message}. Trying primary LLM server...`);
    }

    // 3. Fallback to Primary OpenAI Audio Transcription Service
    const OPENAI_BASE_URL = process.env.OPENAI_BASE_URL || process.env.AI_BASE_URL || 'https://api.openai.com/v1';
    const OPENAI_API_KEY = process.env.OPENAI_API_KEY || process.env.AI_API_KEY || process.env.SKILLBRIDGE_LLM_GATEWAY_API_KEY || '';

    const form = new FormData();
    form.append('file', req.file.buffer, {
      filename: req.file.originalname || 'voice.webm',
      contentType: req.file.mimetype || 'audio/webm'
    });
    form.append('model', 'whisper-1');
    if (targetLangCode) {
      form.append('language', targetLangCode);
    }

    const response = await axios.post(`${OPENAI_BASE_URL}/audio/transcriptions`, form, {
      headers: {
        'Authorization': `Bearer ${OPENAI_API_KEY}`,
        ...form.getHeaders()
      },
      maxBodyLength: Infinity,
      maxContentLength: Infinity,
      timeout: 30000
    });

    const transcriptionText = response.data?.text || '';
    console.log(`[Whisper Flow OpenAI Result]: "${transcriptionText}"`);
    res.json({ text: transcriptionText, detectedLanguage: targetLangCode || 'en' });
  } catch (error) {
    console.warn('Whisper transcription warning: All Whisper providers unavailable:', error.message);
    res.json({
      text: '',
      detectedLanguage: 'en',
      error: 'Whisper service is currently unavailable. Please type your message.'
    });
  }
});


export default router;
