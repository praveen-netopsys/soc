import fs from 'fs';
import path from 'path';
import { defaultPrisma } from '../config/prisma.js';

const coursesToWarm = [
  '0bccab1c-9748-4dc0-bf13-d1592681eb68',
  'c14a15c9-6f82-4b3c-8e38-46818733651d'
];

async function main() {
  console.log('--- Starting All-Slide Audio Synthesis Pre-generation ---');

  // Load course curriculum
  const courseRecord = await defaultPrisma.course.findUnique({
    where: { id: '0bccab1c-9748-4dc0-bf13-d1592681eb68' },
    select: { id: true, title: true, curriculum: true }
  });

  if (!courseRecord) {
    console.error('Course 0bccab1c-9748-4dc0-bf13-d1592681eb68 not found in default database.');
    process.exit(1);
  }

  const cur = typeof courseRecord.curriculum === 'string'
    ? JSON.parse(courseRecord.curriculum)
    : courseRecord.curriculum;
  const slides = cur?.slides || [];

  console.log(`Found ${slides.length} slides for course "${courseRecord.title}".`);

  for (const courseId of coursesToWarm) {
    const audioDir = path.join(process.cwd(), 'Backend', 'uploads', 'courses', courseId, 'audio', 'en');
    fs.mkdirSync(audioDir, { recursive: true });

    for (const slide of slides) {
      const sNum = slide.slideNumber || slide.slide_number;
      if (!sNum) continue;

      const text = slide.spokenExplanation || slide.explanation || slide.title || `Slide ${sNum}`;

      // 1. Synthesize Female Voice
      const femaleFile = path.join(audioDir, `Slide${sNum}_female.mp3`);
      const femaleFile2 = path.join(audioDir, `slide_${sNum}_female.mp3`);

      if (!fs.existsSync(femaleFile) || fs.statSync(femaleFile).size < 500) {
        process.stdout.write(`Course ${courseId.slice(0, 8)} Slide ${sNum} [Female]... `);
        try {
          const resp = await fetch(`http://127.0.0.1:8000/api/tts/speak?text=${encodeURIComponent(text)}&gender=FEMALE&language=en`);
          if (resp.ok) {
            const buf = Buffer.from(await resp.arrayBuffer());
            if (buf.length > 200) {
              fs.writeFileSync(femaleFile, buf);
              fs.writeFileSync(femaleFile2, buf);
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
        if (!fs.existsSync(femaleFile2)) {
          fs.copyFileSync(femaleFile, femaleFile2);
        }
      }

      // 2. Synthesize Male Voice
      const maleFile = path.join(audioDir, `Slide${sNum}.mp3`);
      const maleFile2 = path.join(audioDir, `slide_${sNum}.mp3`);

      if (!fs.existsSync(maleFile) || fs.statSync(maleFile).size < 500) {
        process.stdout.write(`Course ${courseId.slice(0, 8)} Slide ${sNum} [Male]... `);
        try {
          const resp = await fetch(`http://127.0.0.1:8000/api/tts/speak?text=${encodeURIComponent(text)}&gender=MALE&language=en`);
          if (resp.ok) {
            const buf = Buffer.from(await resp.arrayBuffer());
            if (buf.length > 200) {
              fs.writeFileSync(maleFile, buf);
              fs.writeFileSync(maleFile2, buf);
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
        if (!fs.existsSync(maleFile2)) {
          fs.copyFileSync(maleFile, maleFile2);
        }
      }
    }
  }

  console.log('--- All Slide Audio Pre-generation Completed Successfully! ---');
  process.exit(0);
}

main().catch(err => {
  console.error('Fatal pregeneration error:', err);
  process.exit(1);
});
