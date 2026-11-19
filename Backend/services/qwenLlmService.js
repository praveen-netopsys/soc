import axios from 'axios';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Ensure .env is loaded reliably
dotenv.config({ path: path.resolve(__dirname, '../.env') });
dotenv.config({ path: path.resolve(__dirname, '../../.env') });
dotenv.config();

/**
 * Dedicated Remote Qwen LLM Gateway Service for SkillBridge / Brutally Hired.
 * Communicates with the remote Central AI Gateway:
 * - GET  /health         (no auth key needed)
 * - GET  /api/v1/models  (X-API-Key required)
 * - POST /api/v1/chat    (X-API-Key required, body: {"message": "<string>"})
 *
 * Security:
 * - Exclusively uses server-side requests with X-API-Key header.
 * - Never logs keys or full request/response headers.
 * - Never returns placeholder or made-up text on failure.
 */
class QwenLlmService {
  /**
   * Resolve remote LLM gateway base URL.
   * Priority:
   * 1. AI_GATEWAY_URL
   * 2. QWEN_GATEWAY_URL
   * 3. LLM_GATEWAY_URL
   * 4. BRUTALLYHIRED_LLM_GATEWAY_URL
   * 5. SKILLBRIDGE_LLM_GATEWAY_URL
   *
   * Strips any trailing slash. Returns empty string if not configured.
   */
  getGatewayBaseUrl() {
    const raw = (
      process.env.AI_GATEWAY_URL ||
      process.env.QWEN_GATEWAY_URL ||
      process.env.LLM_GATEWAY_URL ||
      process.env.BRUTALLYHIRED_LLM_GATEWAY_URL ||
      process.env.SKILLBRIDGE_LLM_GATEWAY_URL ||
      ''
    ).trim();

    return raw.replace(/\/+$/, '');
  }

  /**
   * Resolve AI gateway API key from backend environment.
   * Priority:
   * 1. AI_GATEWAY_API_KEY
   * 2. AI_API_KEY
   * 3. QWEN_API_KEY
   * 4. BRUTALLYHIRED_LLM_API_KEY
   * 5. LLM_GATEWAY_API_KEY
   * 6. SKILLBRIDGE_LLM_GATEWAY_API_KEY
   */
  getApiKey() {
    return (
      process.env.AI_GATEWAY_API_KEY ||
      process.env.AI_API_KEY ||
      process.env.QWEN_API_KEY ||
      process.env.BRUTALLYHIRED_LLM_API_KEY ||
      process.env.LLM_GATEWAY_API_KEY ||
      process.env.SKILLBRIDGE_LLM_GATEWAY_API_KEY ||
      ''
    ).trim();
  }

  /**
   * Resolve configured timeout in milliseconds.
   * Defaults to 120,000ms (~120s) because initial model loading can take time.
   */
  getTimeoutMs() {
    const parsed = parseInt(process.env.AI_GATEWAY_TIMEOUT, 10);
    return !isNaN(parsed) && parsed > 0 ? parsed : 120000;
  }

  /**
   * Determine whether the remote LLM gateway has been configured.
   */
  isConfigured() {
    return Boolean(this.getGatewayBaseUrl());
  }

  /**
   * Construct safe HTTP headers with X-API-Key.
   * Never uses "Authorization: Bearer".
   * @param {Object} extra
   */
  getHeaders(extra = {}) {
    const headers = {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...extra
    };

    const apiKey = this.getApiKey();
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }

    return headers;
  }

  /**
   * Health check: GET ${gatewayUrl}/health (no key needed)
   */
  async checkHealth() {
    const gatewayUrl = this.getGatewayBaseUrl();
    if (!gatewayUrl) {
      return {
        online: false,
        status: 'error',
        error: 'AI_GATEWAY_URL is not configured.'
      };
    }

    const startTime = Date.now();
    try {
      const response = await axios.get(`${gatewayUrl}/health`, {
        timeout: 10000,
        headers: { Accept: 'application/json' }
      });

      const latencyMs = Date.now() - startTime;
      return {
        online: response.status >= 200 && response.status < 300,
        status: response.data?.status || 'ok',
        url: gatewayUrl,
        latencyMs,
        details: response.data
      };
    } catch (error) {
      const latencyMs = Date.now() - startTime;
      const status = error.response?.status;
      console.error(`[AI Gateway] Health check failed (${status || error.code || 'UNKNOWN'}) at ${gatewayUrl}/health: ${error.message}`);
      return {
        online: false,
        url: gatewayUrl,
        latencyMs,
        error: error.message,
        code: error.code || (status ? `HTTP_${status}` : 'NETWORK_ERROR'),
        statusCode: status
      };
    }
  }

  /**
   * Model listing: GET ${gatewayUrl}/api/v1/models (key required)
   */
  async listModels() {
    const gatewayUrl = this.getGatewayBaseUrl();
    if (!gatewayUrl) {
      throw this._buildError(new Error('AI_GATEWAY_URL is not configured.'), `${gatewayUrl}/api/v1/models`);
    }

    const startTime = Date.now();
    try {
      const response = await axios.get(`${gatewayUrl}/api/v1/models`, {
        timeout: 15000,
        headers: this.getHeaders()
      });

      const latencyMs = Date.now() - startTime;
      const models = response.data?.models || response.data?.data || response.data || [];
      return {
        success: true,
        latencyMs,
        models: Array.isArray(models) ? models : [models]
      };
    } catch (error) {
      throw this._buildError(error, `${gatewayUrl}/api/v1/models`);
    }
  }

  /**
   * Compile system instructions, context, and conversation history into a single message string.
   */
  _buildMessage({ message, conversationHistory = [], systemPrompt = '', role = '', context = '' }) {
    const MAX_TOTAL_CHARS = 12000;
    const MAX_HISTORY_ITEMS = 6;
    const MAX_ITEM_CHARS = 400;

    const sections = [];

    // System / Persona instructions
    const system = String(systemPrompt || '').trim();
    if (system) {
      sections.push(system);
    } else {
      sections.push(
        'You are the Brutally Hired AI Career & Technical Interview Assistant powered by Qwen.\n' +
        'Provide sharp, authentic, and highly constructive feedback, interview preparation, and technical insights.\n' +
        'Keep responses direct, professional, clear, and audio-friendly.'
      );
    }

    if (role) {
      sections.push(`Target Role / Specialization: ${role}`);
    }

    if (context) {
      sections.push(`Context:\n${String(context).trim()}`);
    }

    // Recent conversation history
    if (Array.isArray(conversationHistory) && conversationHistory.length > 0) {
      const historyLines = [];
      const recent = conversationHistory.slice(-MAX_HISTORY_ITEMS);
      for (const item of recent) {
        const isUser = item.role === 'user' || item.sender === 'user';
        const rawContent = item.content || item.text || item.message || '';
        if (typeof rawContent !== 'string' || !rawContent.trim()) continue;

        const clipped = rawContent.trim().length > MAX_ITEM_CHARS
          ? `${rawContent.trim().slice(0, MAX_ITEM_CHARS)}...`
          : rawContent.trim();
        historyLines.push(`${isUser ? 'Candidate' : 'Interviewer'}: ${clipped}`);
      }
      if (historyLines.length > 0) {
        sections.push(`Recent Conversation:\n${historyLines.join('\n')}`);
      }
    }

    // Active candidate question
    sections.push(`Candidate Question / Input:\n${message.trim()}`);

    let compiled = sections.join('\n\n');
    if (compiled.length > MAX_TOTAL_CHARS) {
      compiled = compiled.slice(0, MAX_TOTAL_CHARS);
    }

    return compiled;
  }

  /**
   * Standard error diagnosis and mapping.
   * Maps:
   * - 401 -> configuration error (gateway key is wrong or missing)
   * - 422 -> schema error (request body does not match the gateway)
   * - 404 -> route error (wrong gateway path)
   * - Network / 5xx -> retryable error
   *
   * Never logs keys or full headers.
   */
  _buildError(error, endpointUrl) {
    const status = error.response?.status;
    const data = error.response?.data;
    const detail = typeof data === 'object' ? (data?.detail || data?.error || data?.message) : (typeof data === 'string' ? data : null);
    const code = error.code || (status ? `HTTP_${status}` : 'NETWORK_ERROR');

    let message;
    let isRetryable = false;
    let isConfigurationError = false;

    if (status === 401) {
      message = 'AI gateway key is wrong or missing. Please verify AI_GATEWAY_API_KEY in the backend environment.';
      isConfigurationError = true;
    } else if (status === 422) {
      message = `Request body does not match the AI gateway: ${detail || 'Unprocessable Entity'}`;
    } else if (status === 404) {
      message = `Wrong AI gateway path at ${endpointUrl}. Expected a supported gateway route (/health, /api/v1/models, /api/v1/chat).`;
    } else if (
      (status && status >= 500) ||
      ['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'ECONNABORTED', 'ENOTFOUND'].includes(error.code) ||
      error.message?.includes('timeout')
    ) {
      message = `AI gateway is temporarily unavailable or returned a server error (${status || error.code || 'timeout'}). This request can be retried.`;
      isRetryable = true;
    } else {
      message = detail || error.message || 'AI gateway request failed.';
    }

    // Log status and short reason only. Never log key or headers.
    console.error(`[AI Gateway] Error (${status || code}) on ${endpointUrl}: ${message}`);

    const mappedError = new Error(message);
    mappedError.statusCode = status || (isRetryable ? 503 : 500);
    mappedError.status = mappedError.statusCode;
    mappedError.code = code;
    mappedError.isRetryable = isRetryable;
    mappedError.isConfigurationError = isConfigurationError;
    return mappedError;
  }

  /**
   * Chat completion via POST /api/v1/chat
   *
   * @param {Object} params
   * @param {string} params.message - Candidate message or question
   * @param {Array} [params.conversationHistory] - Previous chat turns
   * @param {string} [params.systemPrompt] - Custom persona prompt
   * @param {string} [params.role] - Target interview role
   * @param {string} [params.context] - Relevant job/lesson context
   * @param {number} [params.timeoutMs] - Request timeout (default: ~120s)
   * @returns {Promise<{response: string, reply: string, text: string, model: string, durationMs: number}>}
   */
  async chat({ message, conversationHistory = [], systemPrompt = '', role = '', context = '', timeoutMs = null }) {
    const rawMessage = String(message || '').trim();
    if (!rawMessage) {
      const err = new Error('Message is required and cannot be empty.');
      err.statusCode = 400;
      throw err;
    }

    const MAX_INPUT_CHARS = 12000;
    if (rawMessage.length > MAX_INPUT_CHARS) {
      const err = new Error(`Message exceeds maximum allowed length of ${MAX_INPUT_CHARS} characters.`);
      err.statusCode = 400;
      throw err;
    }

    const gatewayUrl = this.getGatewayBaseUrl();
    if (!gatewayUrl) {
      throw this._buildError(new Error('AI_GATEWAY_URL is not configured in environment.'), 'AI_GATEWAY_URL');
    }

    const compiledMessage = this._buildMessage({
      message: rawMessage,
      conversationHistory,
      systemPrompt,
      role,
      context
    });

    const targetUrl = `${gatewayUrl}/api/v1/chat`;
    const payload = { message: compiledMessage };
    const effectiveTimeout = timeoutMs || this.getTimeoutMs();
    const startTime = Date.now();

    try {
      console.log(`[AI Gateway] Sending chat request to ${targetUrl} (${compiledMessage.length} chars)`);
      const response = await axios.post(
        targetUrl,
        payload,
        {
          headers: this.getHeaders(),
          timeout: effectiveTimeout
        }
      );

      const durationMs = Date.now() - startTime;
      const data = response.data || {};
      const replyText = typeof data.response === 'string'
        ? data.response.trim()
        : (typeof data.reply === 'string' ? data.reply.trim() : '');

      if (!replyText) {
        throw new Error('AI gateway returned an empty response.');
      }

      const model = data.model || process.env.QWEN_MODEL || 'qwen3.5:9b';
      console.log(`[AI Gateway] Response received from model "${model}" in ${durationMs}ms (${replyText.length} chars)`);

      return {
        response: replyText,
        reply: replyText,
        text: replyText,
        model,
        durationMs
      };
    } catch (error) {
      throw this._buildError(error, targetUrl);
    }
  }
}

export const qwenLlmService = new QwenLlmService();
export default qwenLlmService;
