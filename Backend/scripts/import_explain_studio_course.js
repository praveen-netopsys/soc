import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import prisma from '../config/prisma.js';
import vectorChunkService from '../services/vectorChunkService.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function importExplainStudioCourse(jobId = '0bccab1c-9748-4dc0-bf13-d1592681eb68') {
  console.log(`[Import] Starting import for Explain Studio job: ${jobId}`);

  const explainDir = path.join('C:\\Users\\pavankumar\\Downloads\\explain-studio-local-frontend-server-backend\\explain-studio\\backend\\storage\\courses', jobId);
  const metaPath = path.join(explainDir, 'course_metadata.json');

  if (!fs.existsSync(metaPath)) {
    throw new Error(`course_metadata.json not found at ${metaPath}`);
  }

  const rawMeta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
  const courseTitle = 'NatSteel Driver Training Program';
  const courseDescription = 'Comprehensive interactive driver training program covering road safety, PPE requirements, daily pre-operation inspections, load securement, and incident prevention.';

  // Prepare destination directories
  const destDirs = [
    path.join(process.cwd(), 'uploads', 'courses', jobId),
    path.join(process.cwd(), 'Backend', 'uploads', 'courses', jobId)
  ];

  for (const baseDest of destDirs) {
    const slidesDest = path.join(baseDest, 'slides');
    const audioDest = path.join(baseDest, 'audio');
    fs.mkdirSync(slidesDest, { recursive: true });
    fs.mkdirSync(audioDest, { recursive: true });

    // Copy slide images
    const srcSlidesDir = path.join(explainDir, 'slides');
    if (fs.existsSync(srcSlidesDir)) {
      const slideFiles = fs.readdirSync(srcSlidesDir);
      for (const sf of slideFiles) {
        fs.copyFileSync(path.join(srcSlidesDir, sf), path.join(slidesDest, sf));
        // Also copy with Slide{N}.png naming for backward compatibility
        const match = sf.match(/slide_(\d+)\.png/i);
        if (match) {
          fs.copyFileSync(path.join(srcSlidesDir, sf), path.join(slidesDest, `Slide${match[1]}.png`));
        }
      }
      console.log(`[Import] Copied ${slideFiles.length} slide images to ${slidesDest}`);
    }

    // Copy audio files
    const srcAudioDir = path.join(explainDir, 'audio');
    if (fs.existsSync(srcAudioDir)) {
      const langDirs = fs.readdirSync(srcAudioDir);
      for (const lDir of langDirs) {
        const srcLangDir = path.join(srcAudioDir, lDir);
        if (!fs.statSync(srcLangDir).isDirectory()) continue;

        const destLangDir = path.join(audioDest, lDir);
        fs.mkdirSync(destLangDir, { recursive: true });

        const audioFiles = fs.readdirSync(srcLangDir);
        for (const af of audioFiles) {
          fs.copyFileSync(path.join(srcLangDir, af), path.join(destLangDir, af));
          // Also copy with Slide{N}.mp3 naming
          const match = af.match(/slide_(\d+)(.*)\.mp3/i);
          if (match) {
            fs.copyFileSync(path.join(srcLangDir, af), path.join(destLangDir, `Slide${match[1]}${match[2]}.mp3`));
          }
        }

        // Alias zh-CN to zh
        if (lDir === 'zh-CN') {
          const destZhDir = path.join(audioDest, 'zh');
          fs.mkdirSync(destZhDir, { recursive: true });
          for (const af of audioFiles) {
            fs.copyFileSync(path.join(srcLangDir, af), path.join(destZhDir, af));
            const match = af.match(/slide_(\d+)(.*)\.mp3/i);
            if (match) {
              fs.copyFileSync(path.join(srcLangDir, af), path.join(destZhDir, `Slide${match[1]}${match[2]}.mp3`));
            }
          }
        }
      }
      console.log(`[Import] Copied audio directories across languages to ${audioDest}`);
    }
  }

  // Format modules, slides, and lessons
  const totalSlides = rawMeta.total_slides || 26;
  const modules = [];
  const lessons = [];
  const allSlides = [];

  for (const m of (rawMeta.modules || [])) {
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
      const imageUrl = `/api/v1/courses/${jobId}/slides/${sNum}/image`;

      const audioUrls = {};
      const explanations = s.explanations || {};
      for (const [langKey, expText] of Object.entries(explanations)) {
        audioUrls[langKey] = `/api/v1/courses/${jobId}/slides/${sNum}/audio?lang=${langKey}`;
      }
      if (explanations['zh-CN'] && !audioUrls['zh']) {
        audioUrls['zh'] = `/api/v1/courses/${jobId}/slides/${sNum}/audio?lang=zh`;
      }

      const slideObj = {
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
      };

      allSlides.push(slideObj);

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
    languages: rawMeta.languages || ['en', 'zh-CN', 'ms', 'ta', 'bn', 'te'],
    modules,
    slides: allSlides,
    lessons,
    explainStudioJobId: jobId
  };

  const fullText = allSlides.map(s => `Slide ${s.slideNumber}: ${s.title}\n${s.spokenExplanation}`).join('\n\n');

  // Upsert in PostgreSQL
  console.log(`[Import] Upserting course ${jobId} in PostgreSQL...`);
  const courseRecord = await prisma.course.upsert({
    where: { id: jobId },
    update: {
      title: courseTitle,
      description: courseDescription,
      fileName: 'NatSteel_Driver_Training.pptx',
      fileText: fullText,
      curriculum: finalCurriculum
    },
    create: {
      id: jobId,
      title: courseTitle,
      description: courseDescription,
      fileName: 'NatSteel_Driver_Training.pptx',
      fileText: fullText,
      curriculum: finalCurriculum
    }
  });

  console.log(`[Import] Course record saved in PostgreSQL: ${courseRecord.id} (${courseRecord.title})`);

  // Assign course to all users so they can access it immediately
  const users = await prisma.user.findMany({ select: { id: true, email: true, role: true } });
  console.log(`[Import] Assigning course to ${users.length} users...`);
  for (const u of users) {
    await prisma.userCourseAssignment.upsert({
      where: {
        userId_courseId: {
          userId: u.id,
          courseId: jobId
        }
      },
      update: {},
      create: {
        userId: u.id,
        courseId: jobId
      }
    }).catch(() => {});
  }
  console.log(`[Import] Course assignments created.`);

  // Index into PostgreSQL pgvector CourseChunk table
  try {
    console.log(`[Import] Indexing course into PostgreSQL pgvector CourseChunk table...`);
    const docMeta = {
      fileName: 'NatSteel_Driver_Training.pptx',
      fileType: 'presentation',
      slides: allSlides
    };
    await vectorChunkService.indexCourse(prisma, jobId, { text: fullText, ...docMeta }, finalCurriculum);
    console.log(`[Import] pgvector indexing complete!`);
  } catch (vecErr) {
    console.warn(`[Import] pgvector notice:`, vecErr.message);
  }

  console.log(`[Import] SUCCESS! Course ${jobId} is fully integrated into SkillBridge!`);
}

importExplainStudioCourse()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error('[Import] Error:', err);
    process.exit(1);
  });
