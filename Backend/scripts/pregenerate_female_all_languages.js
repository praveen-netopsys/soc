import fs from 'fs';
import path from 'path';
import { defaultPrisma } from '../config/prisma.js';

const coursesToWarm = [
  '0bccab1c-9748-4dc0-bf13-d1592681eb68',
  'c14a15c9-6f82-4b3c-8e38-46818733651d'
];

const langs = ['en', 'zh', 'ms', 'ta', 'te', 'bn'];

async function main() {
  console.log('--- Starting All-Language Female & Male Voice Pre-generation ---');

  const courseRecord = await defaultPrisma.course.findUnique({
    where: { id: '0bccab1c-9748-4dc0-bf13-d1592681eb68' },
    select: { id: true, title: true, curriculum: true }
  });

  if (!courseRecord) {
    console.error('Target course not found.');
    process.exit(1);
  }

  const cur = typeof courseRecord.curriculum === 'string'
    ? JSON.parse(courseRecord.curriculum)
    : courseRecord.curriculum;
  const slides = cur?.slides || [];

  console.log(`Found ${slides.length} slides for course "${courseRecord.title}".`);

  for (const courseId of coursesToWarm) {
    for (const lang of langs) {
      const dirs = [
        path.join(process.cwd(), 'uploads', 'courses', courseId, 'audio', lang),
        path.join(process.cwd(), 'Backend', 'uploads', 'courses', courseId, 'audio', lang)
      ];
      for (const d of dirs) {
        fs.mkdirSync(d, { recursive: true });
      }

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

        // 1. Female voice
        const femaleFile1 = path.join(dirs[0], `Slide${sNum}_female.mp3`);
        const femaleFile2 = path.join(dirs[0], `slide_${sNum}_female.mp3`);

        if (!fs.existsSync(femaleFile1) || fs.statSync(femaleFile1).size < 500) {
          process.stdout.write(`Course ${courseId.slice(0, 8)} [${lang}] Slide ${sNum} (Female)... `);
          try {
            const resp = await fetch(`http://127.0.0.1:8000/api/tts/speak?text=${encodeURIComponent(text)}&gender=FEMALE&language=${encodeURIComponent(lang)}`);
            if (resp.ok) {
              const buf = Buffer.from(await resp.arrayBuffer());
              if (buf.length > 200) {
                for (const d of dirs) {
                  fs.writeFileSync(path.join(d, `Slide${sNum}_female.mp3`), buf);
                  fs.writeFileSync(path.join(d, `slide_${sNum}_female.mp3`), buf);
                }
                console.log(`OK (${buf.length} bytes)`);
              } else {
                console.log('TOO_SMALL');
              }
            } else {
              console.log(`HTTP ${resp.status}`);
            }
          } catch (e) {
            console.log(`ERR: ${e.message}`);
          }
        } else {
          // Sync to secondary dir if missing
          for (const d of dirs) {
            const f1 = path.join(d, `Slide${sNum}_female.mp3`);
            const f2 = path.join(d, `slide_${sNum}_female.mp3`);
            if (!fs.existsSync(f1)) fs.copyFileSync(femaleFile1, f1);
            if (!fs.existsSync(f2)) fs.copyFileSync(femaleFile1, f2);
          }
        }

        // 2. Male voice (ensure synced to both dirs)
        const maleFile1 = path.join(dirs[0], `Slide${sNum}.mp3`);
        if (fs.existsSync(maleFile1) && fs.statSync(maleFile1).size > 500) {
          for (const d of dirs) {
            const m1 = path.join(d, `Slide${sNum}.mp3`);
            const m2 = path.join(d, `slide_${sNum}.mp3`);
            if (!fs.existsSync(m1)) fs.copyFileSync(maleFile1, m1);
            if (!fs.existsSync(m2)) fs.copyFileSync(maleFile1, m2);
          }
        }
      }
    }
  }

  console.log('--- All Audio Generation & Synchronization Complete ---');
  process.exit(0);
}

main().catch(err => {
  console.error('Fatal error in pregeneration:', err);
  process.exit(1);
});
