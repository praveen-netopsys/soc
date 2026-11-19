import fs from 'fs';
import path from 'path';
import { masterPrisma } from '../config/masterPrisma.js';
import { tenantService } from '../services/tenantService.js';
import { defaultPrisma } from '../config/prisma.js';

const langs = ['en', 'zh', 'ms', 'ta', 'te', 'bn'];
const CONCURRENCY = 6;

async function getAllCourses() {
  const courses = [];
  try {
    const def = await defaultPrisma.course.findMany({ select: { id: true, title: true, curriculum: true } });
    def.forEach(c => courses.push({ ...c, tenant: 'default' }));
  } catch (e) {
    console.warn('Error fetching default courses:', e.message);
  }

  try {
    const companies = await masterPrisma.company.findMany({ select: { code: true } });
    for (const comp of companies) {
      try {
        const client = await tenantService.getTenantClient(comp.code);
        const cList = await client.course.findMany({ select: { id: true, title: true, curriculum: true } });
        cList.forEach(c => {
          if (!courses.some(x => x.id === c.id)) {
            courses.push({ ...c, tenant: comp.code });
          }
        });
      } catch (_) {}
    }
  } catch (e) {
    console.warn('Error fetching tenant courses:', e.message);
  }
  return courses;
}

async function synthTask(task) {
  const { url, f1, f2, b1, b2, label } = task;
  try {
    const resp = await fetch(url);
    if (resp.ok) {
      const buf = Buffer.from(await resp.arrayBuffer());
      if (buf.length > 200) {
        fs.writeFileSync(f1, buf);
        fs.writeFileSync(f2, buf);
        fs.writeFileSync(b1, buf);
        fs.writeFileSync(b2, buf);
        console.log(`[OK] ${label} (${buf.length} bytes)`);
        return true;
      }
    }
    console.warn(`[WARN] ${label} HTTP ${resp.status}`);
  } catch (e) {
    console.error(`[ERR] ${label}: ${e.message}`);
  }
  return false;
}

async function main() {
  console.log('--- Universal Fast Concurrent Multi-Course Voice Pre-generation ---');
  const courses = await getAllCourses();
  console.log(`Discovered ${courses.length} courses across all tenants.`);

  const tasks = [];

  for (const course of courses) {
    const cur = typeof course.curriculum === 'string' ? JSON.parse(course.curriculum) : course.curriculum;
    const slides = cur?.slides || [];
    if (slides.length === 0) continue;

    for (const lang of langs) {
      const langDir = path.join(process.cwd(), 'uploads', 'courses', course.id, 'audio', lang);
      const langDirBackend = path.join(process.cwd(), 'Backend', 'uploads', 'courses', course.id, 'audio', lang);
      fs.mkdirSync(langDir, { recursive: true });
      fs.mkdirSync(langDirBackend, { recursive: true });

      for (const slide of slides) {
        const sNum = slide.slideNumber || slide.slide_number;
        if (!sNum) continue;

        const langKey = (lang === 'zh') ? 'zh-CN' : lang;
        const text = slide.explanations?.[langKey] ||
          slide.explanations?.[lang] ||
          slide.spokenExplanation ||
          slide.explanation ||
          slide.title ||
          `Slide ${sNum}`;

        // 1. Female task
        const f1 = path.join(langDir, `Slide${sNum}_female.mp3`);
        const f2 = path.join(langDir, `slide_${sNum}_female.mp3`);
        const fb1 = path.join(langDirBackend, `Slide${sNum}_female.mp3`);
        const fb2 = path.join(langDirBackend, `slide_${sNum}_female.mp3`);

        if (!fs.existsSync(f1) || fs.statSync(f1).size < 500) {
          tasks.push({
            url: `http://127.0.0.1:8000/api/tts/speak?text=${encodeURIComponent(text)}&gender=FEMALE&language=${encodeURIComponent(lang)}`,
            f1, f2, b1: fb1, b2: fb2,
            label: `Course ${course.id.slice(0, 8)} [${lang}] Slide ${sNum} (Female)`
          });
        } else {
          if (!fs.existsSync(fb1)) fs.copyFileSync(f1, fb1);
          if (!fs.existsSync(fb2)) fs.copyFileSync(f1, fb2);
        }

        // 2. Male task
        const m1 = path.join(langDir, `Slide${sNum}.mp3`);
        const m2 = path.join(langDir, `slide_${sNum}.mp3`);
        const mb1 = path.join(langDirBackend, `Slide${sNum}.mp3`);
        const mb2 = path.join(langDirBackend, `slide_${sNum}.mp3`);

        if (!fs.existsSync(m1) || fs.statSync(m1).size < 500) {
          tasks.push({
            url: `http://127.0.0.1:8000/api/tts/speak?text=${encodeURIComponent(text)}&gender=MALE&language=${encodeURIComponent(lang)}`,
            f1: m1, f2: m2, b1: mb1, b2: mb2,
            label: `Course ${course.id.slice(0, 8)} [${lang}] Slide ${sNum} (Male)`
          });
        } else {
          if (!fs.existsSync(mb1)) fs.copyFileSync(m1, mb1);
          if (!fs.existsSync(mb2)) fs.copyFileSync(m1, mb2);
        }
      }
    }
  }

  console.log(`Total missing audio clips to synthesize: ${tasks.length}`);

  // Concurrent worker execution
  let index = 0;
  async function worker() {
    while (index < tasks.length) {
      const currentTask = tasks[index++];
      await synthTask(currentTask);
    }
  }

  const workers = Array.from({ length: CONCURRENCY }, () => worker());
  await Promise.all(workers);

  console.log('\n--- All Voices Across All Courses Successfully Pre-generated ---');
  process.exit(0);
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
