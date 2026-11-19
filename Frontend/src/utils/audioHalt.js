/**
 * Global audio and speech synthesis halting utility for SkillBridge.
 * Safely pauses and resets active HTML5 audio streams and Web Speech synthesis instances
 * WITHOUT destroying React DOM event listeners.
 */
export const haltAllSkillBridgeAudio = () => {
  try {
    if (window._skillbridgeCurrentAudio) {
      const audio = window._skillbridgeCurrentAudio;
      audio.pause();
      audio.currentTime = 0;
    }
  } catch (e) { }

  if (window._skillbridgeResumeInterval) {
    clearInterval(window._skillbridgeResumeInterval);
    window._skillbridgeResumeInterval = null;
  }

  try {
    if (window._skillbridgeCurrentUtterance) {
      window._skillbridgeCurrentUtterance.onstart = null;
      window._skillbridgeCurrentUtterance.onend = null;
      window._skillbridgeCurrentUtterance.onerror = null;
      window._skillbridgeCurrentUtterance.onpause = null;
      window._skillbridgeCurrentUtterance.onresume = null;
      window._skillbridgeCurrentUtterance = null;
    }
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      window.speechSynthesis.pause();
      window.speechSynthesis.cancel();
      try { window.speechSynthesis.resume(); } catch (_) { }
      window.speechSynthesis.cancel();
    }
  } catch (e) { }
};

export default haltAllSkillBridgeAudio;


