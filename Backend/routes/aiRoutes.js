import express from 'express';
import multer from 'multer';
import { aiGatewayClient } from '../services/aiGatewayClient.js';
import { authenticateToken } from '../middleware/auth.js';

const router = express.Router();
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 50 * 1024 * 1024 } // 50MB audio/doc limit
});

/**
 * GET /api/ai/health or /api/v1/ai/health
 * Public or authenticated health check to verify backend-to-gateway connectivity.
 */
router.get('/health', async (req, res) => {
  try {
    const health = await aiGatewayClient.checkHealth();
    const statusCode = health.status === 'ok' ? 200 : 503;
    res.status(statusCode).json(health);
  } catch (error) {
    res.status(503).json({
      status: 'error',
      error: error.message || 'AI Gateway is unreachable'
    });
  }
});

/**
 * GET /api/ai/models or /api/v1/ai/models
 * Returns available models from the Central AI Gateway.
 */
router.get('/models', authenticateToken, async (req, res) => {
  try {
    const result = await aiGatewayClient.listModels();
    res.json(result);
  } catch (error) {
    console.error('[AI Routes] listModels error:', error.message);
    res.status(error.status || 500).json({
      error: error.message || 'Failed to list AI models'
    });
  }
});

/**
 * POST /api/ai/chat or /api/v1/ai/chat
 * Authenticated user chat completion forwarding to Central AI Gateway.
 * Accepts: { message, system_prompt }
 */
router.post('/chat', authenticateToken, async (req, res) => {
  try {
    const { message, system_prompt } = req.body;

    if (!message || typeof message !== 'string' || !message.trim()) {
      return res.status(400).json({ error: 'Field "message" is required and cannot be empty.' });
    }

    const chatResult = await aiGatewayClient.chat({
      message,
      system_prompt
    });

    res.json(chatResult);
  } catch (error) {
    console.error('[AI Routes] chat error:', error.message);
    res.status(error.status || error.statusCode || 500).json({
      error: error.message || 'Failed to generate chat response'
    });
  }
});

/**
 * POST /api/ai/stt/transcribe or /api/v1/ai/stt/transcribe
 * Transcribe speech audio to text via Gateway Whisper engine.
 */
router.post('/stt/transcribe', authenticateToken, upload.single('file'), async (req, res) => {
  try {
    if (!req.file || !req.file.buffer) {
      return res.status(400).json({ error: 'Audio file is required.' });
    }

    const language = req.body.language || req.query.language || null;
    const filename = req.file.originalname || 'voice.webm';

    const result = await aiGatewayClient.transcribeAudio(req.file.buffer, filename, language);
    res.json(result);
  } catch (error) {
    console.error('[AI Routes] stt error:', error.message);
    res.status(error.status || 500).json({
      error: error.message || 'Speech transcription failed'
    });
  }
});

/**
 * GET /api/ai/tts/voices or /api/v1/ai/tts/voices
 * Lists supported TTS voices.
 */
router.get('/tts/voices', authenticateToken, async (req, res) => {
  try {
    const voices = await aiGatewayClient.listVoices();
    res.json({ voices });
  } catch (error) {
    console.error('[AI Routes] tts voices error:', error.message);
    res.status(error.status || 500).json({
      error: error.message || 'Failed to list TTS voices'
    });
  }
});

/**
 * POST /api/ai/tts/synthesize or /api/v1/ai/tts/synthesize
 * Synthesizes text to speech using Central AI Gateway Piper / TTS.
 */
router.post('/tts/synthesize', authenticateToken, async (req, res) => {
  try {
    const { text, voice, gender, rate, pitch } = req.body;
    if (!text || !text.trim()) {
      return res.status(400).json({ error: 'Field "text" is required.' });
    }

    const audioBuf = await aiGatewayClient.synthesizeSpeech({
      text,
      voice,
      gender,
      rate,
      pitch
    });

    res.setHeader('Content-Type', 'audio/wav');
    res.setHeader('Content-Length', audioBuf.length);
    res.setHeader('Cache-Control', 'public, max-age=86400');
    res.end(audioBuf);
  } catch (error) {
    console.error('[AI Routes] tts synthesize error:', error.message);
    res.status(error.status || 500).json({
      error: error.message || 'Text-to-speech synthesis failed'
    });
  }
});

export default router;
