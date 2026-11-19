/**
 * Teacher Narrator Utility
 * Converts raw slide bullets, fragmented headings, and dense document notes
 * into clean, natural instructional sentences spoken by a human teacher.
 * Eliminates repetitive boilerplate prefixes, artificial transition fillers,
 * and duplicates so every lesson sounds authentic, direct, and professional.
 */

import { stripSlidePlaceholders, cleanUnsupportedWords } from './textCleaner';

/**
 * Clean a single line of raw slide text into clean spoken text
 */
function cleanPhraseForSpeech(phrase) {
  if (!phrase || typeof phrase !== 'string') return '';
  return phrase
    .replace(/^[-*•➔\d\.]+\s*/, '') // Remove bullet dots, dashes, numbers
    .replace(/^[💡🎯📋⚠️📌]\s*/, '')  // Remove leading symbol emojis
    .replace(/[#*_~`^|<>]/g, '')     // Remove markdown syntax
    .replace(/\s*=\s*/g, ' means ')  // Convert = to "means"
    .replace(/\s*&\s*/g, ' and ')    // Convert & to "and"
    .replace(/\s*\/\s*/g, ' or ')    // Convert / to "or"
    .replace(/\s*->\s*/g, ', which leads to ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Transform raw lesson content into clean, natural instructional sentences
 * @param {string} rawContent - Raw explanation or chunk text
 * @param {string} lessonTitle - The title of the lesson
 * @param {string} courseTitle - The course name
 * @returns {string[]} Array of clean, natural, deduplicated sentences
 */
export function buildTeacherLectureSentences(rawContent, lessonTitle = '', courseTitle = '') {
  if (!rawContent || typeof rawContent !== 'string') {
    const topic = cleanPhraseForSpeech(lessonTitle) || 'this topic';
    return [
      `In this section, we examine the essential concepts for ${topic}.`
    ];
  }

  const cleanText = stripSlidePlaceholders(cleanUnsupportedWords(rawContent));
  const rawLines = cleanText.split('\n').map(l => l.trim()).filter(Boolean);

  const sentences = [];
  const seenSentences = new Set();

  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i];
    if (!line || line.length < 3) continue;

    const cleanLine = cleanPhraseForSpeech(line);
    if (!cleanLine || cleanLine.length < 3) continue;

    // Strip common repetitive robotic prefixes if already present in text
    const strippedLine = cleanLine
      .replace(/^(?:welcome(?:!| to this (?:lesson|module|course|section)[^.]*\.?)?|today we will explore[^.]*\.?)\s*/i, '')
      .replace(/^(?:first and foremost,?\s*(?:always\s*)?(?:ensure that|remember to)?|in addition,?\s*(?:it is mandatory to)?|furthermore,?\s*(?:you must)?|to begin with,?\s*|next,?\s*|also,?\s*make sure to|additionally,?\s*|another important point is to|now,?\s*let's pay close attention to[^.]*\.?)\s*/i, '')
      .trim();

    if (!strippedLine || strippedLine.length < 3) continue;

    // Split paragraphs or multi-sentence lines into individual sentences
    const subSentences = strippedLine.split(/(?<=[.!?])\s+/);

    for (let sent of subSentences) {
      sent = sent.trim();
      if (!sent || sent.length < 4) continue;

      // Capitalize first character
      sent = sent.charAt(0).toUpperCase() + sent.slice(1);

      // Ensure proper end punctuation
      if (!/[.!?]$/.test(sent)) {
        sent += '.';
      }

      // Deduplicate sentences
      const normalized = sent.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
      if (normalized.length > 5 && !seenSentences.has(normalized)) {
        seenSentences.add(normalized);
        sentences.push(sent);
      }
    }
  }

  // If no sentences were parsed, fallback to clean lesson topic
  if (sentences.length === 0) {
    const topic = cleanPhraseForSpeech(lessonTitle) || 'this topic';
    return [
      `In this section, we examine the operational requirements and safety guidelines for ${topic}.`
    ];
  }

  return sentences;
}
