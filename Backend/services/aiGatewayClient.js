import axios from 'axios';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Explicitly load .env with multiple resilient fallbacks
dotenv.config({ path: path.resolve(__dirname, '../.env') });
dotenv.config({ path: path.resolve(__dirname, '../../.env') });
dotenv.config();

/**
 * Enterprise Centralized AI Gateway Client for SkillBridge.
 *
 * Interfaces with the Central AI Gateway:
 * - Health Check:           GET  /health         (no auth key needed)
 * - Model Listing:          GET  /api/v1/models  (X-API-Key required)
 * - Chat Completion:        POST /api/v1/chat    (X-API-Key required, body: {"message": "<string>"})
 *
 * Security:
 * - Never logs raw API keys or full request/response headers.
 * - Enforces server-side authentication using the HTTP header X-API-Key (never Authorization Bearer).
 * - Default request timeout: 120,000ms (~120s) because initial model loading can take time.
 * - Never returns placeholder or made-up text on failure.
 */
export class AIGatewayClient {
  constructor() {
    this.defaultTimeoutMs = 120000; // 120 seconds default
  }

  /**
   * Resolve Central AI Gateway Base URL.
   * Priority:
   * 1. AI_GATEWAY_URL
   * 2. QWEN_GATEWAY_URL
   * 3. SKILLBRIDGE_LLM_GATEWAY_URL
   * 4. LLM_GATEWAY_URL
   *
   * Strips any trailing slash. Returns empty string if not configured.
   */
  getGatewayUrl() {
    const raw = (
      process.env.AI_GATEWAY_URL ||
      process.env.QWEN_GATEWAY_URL ||
      process.env.SKILLBRIDGE_LLM_GATEWAY_URL ||
      process.env.LLM_GATEWAY_URL ||
      ''
    ).trim();

    return raw.replace(/\/+$/, '');
  }

  /**
   * Resolve Central AI Gateway API Key.
   * Priority:
   * 1. AI_GATEWAY_API_KEY
   * 2. AI_API_KEY
   * 3. QWEN_API_KEY
   * 4. SKILLBRIDGE_LLM_GATEWAY_API_KEY
   * 5. LLM_GATEWAY_API_KEY
   * 6. BRUTALLYHIRED_LLM_API_KEY
   */
  getApiKey() {
    return (
      process.env.AI_GATEWAY_API_KEY ||
      process.env.AI_API_KEY ||
      process.env.QWEN_API_KEY ||
      process.env.SKILLBRIDGE_LLM_GATEWAY_API_KEY ||
      process.env.LLM_GATEWAY_API_KEY ||
      process.env.BRUTALLYHIRED_LLM_API_KEY ||
      ''
    ).trim();
  }

  /**
   * Resolve configured timeout in milliseconds.
   */
  getTimeoutMs() {
    const parsed = parseInt(process.env.AI_GATEWAY_TIMEOUT, 10);
    return !isNaN(parsed) && parsed > 0 ? parsed : this.defaultTimeoutMs;
  }

  /**
   * Determine whether the gateway URL is configured.
   */
  isConfigured() {
    return Boolean(this.getGatewayUrl());
  }

  /**
   * Build request headers with X-API-Key.
   * Never uses "Authorization: Bearer".
   */
  _getHeaders(extra = {}) {
    const apiKey = this.getApiKey();
    const headers = {
      Accept: 'application/json',
      ...extra
    };

    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }

    return headers;
  }

  /**
   * Health Check: GET /health (no key needed)
   */
  async checkHealth() {
    const gatewayUrl = this.getGatewayUrl();
    if (!gatewayUrl) {
      return {
        status: 'error',
        gatewayUrl: '',
        latencyMs: 0,
        error: 'AI_GATEWAY_URL is not configured.'
      };
    }

    const startTime = Date.now();
    try {
      const response = await axios.get(`${gatewayUrl}/health`, {
        headers: { Accept: 'application/json' },
        timeout: 10000
      });

      const latencyMs = Date.now() - startTime;
      return {
        status: 'ok',
        gatewayUrl,
        latencyMs,
        data: response.data
      };
    } catch (err) {
      const latencyMs = Date.now() - startTime;
      const failureReason = this._diagnoseError(err, `${gatewayUrl}/health`);
      return {
        status: 'error',
        gatewayUrl,
        latencyMs,
        error: failureReason.message,
        code: failureReason.code,
        status_code: failureReason.status
      };
    }
  }

  /**
   * Model Listing: GET /api/v1/models (key required)
   */
  async listModels() {
    const gatewayUrl = this.getGatewayUrl();
    if (!gatewayUrl) {
      throw this._buildError(new Error('AI_GATEWAY_URL is not configured.'), 'AI_GATEWAY_URL');
    }

    const startTime = Date.now();
    try {
      const response = await axios.get(`${gatewayUrl}/api/v1/models`, {
        headers: this._getHeaders(),
        timeout: 15000
      });

      const latencyMs = Date.now() - startTime;
      const models = response.data?.models || response.data?.data || response.data || [];
      return {
        success: true,
        latencyMs,
        models: Array.isArray(models) ? models : [models]
      };
    } catch (err) {
      throw this._buildError(err, `${gatewayUrl}/api/v1/models`);
    }
  }

  /**
   * Chat Completion: POST /api/v1/chat
   *
   * Strictly adheres to the confirmed AI gateway specification:
   * - Endpoint: POST ${AI_GATEWAY_URL}/api/v1/chat
   * - Headers: X-API-Key: <key>, Content-Type: application/json
   * - Body: {"message": "<string>"}
   * - Response: {"model": "...", "response": "<string>", "done": true}
   *
   * @param {Object} params
   * @param {string} params.message - User prompt or question
   * @param {string} [params.system_prompt] - Optional system instruction prompt (prepended to message)
   * @param {number} [params.timeout] - Optional timeout override in ms
   */
  async chat({ message, system_prompt = '', timeout = null }) {
    const gatewayUrl = this.getGatewayUrl();
    if (!gatewayUrl) {
      throw this._buildError(new Error('AI_GATEWAY_URL is not configured.'), 'AI_GATEWAY_URL');
    }

    const cleanMessage = String(message || '').trim();
    if (!cleanMessage) {
      throw new Error('Chat message content is required.');
    }

    const cleanSystem = typeof system_prompt === 'string' ? system_prompt.trim() : '';
    const fullMessage = cleanSystem ? `${cleanSystem}\n\n${cleanMessage}` : cleanMessage;

    // Confirmed gateway body: strictly {"message": "<string>"}
    const payload = {
      message: fullMessage
    };

    const effectiveTimeout = timeout || this.getTimeoutMs();
    const startTime = Date.now();
    const targetUrl = `${gatewayUrl}/api/v1/chat`;

    try {
      console.log(`[AI Gateway] Sending chat request to ${targetUrl} (${fullMessage.length} chars)`);
      const response = await axios.post(targetUrl, payload, {
        headers: this._getHeaders({ 'Content-Type': 'application/json' }),
        timeout: effectiveTimeout
      });

      const durationMs = Date.now() - startTime;
      const data = response.data;

      // Confirmed gateway response schema: {"model": "...", "response": "...", "done": true}
      const reply = typeof data?.response === 'string'
        ? data.response.trim()
        : (typeof data?.reply === 'string' ? data.reply.trim() : '');

      if (!reply) {
        throw new Error('AI gateway returned an empty response.');
      }

      const model = data?.model || process.env.QWEN_MODEL || 'qwen3.5:9b';
      console.log(`[AI Gateway] Received response from model "${model}" in ${durationMs}ms (${reply.length} chars)`);

      return {
        success: true,
        reply,
        response: reply,
        text: reply,
        model,
        raw: data,
        latencyMs: durationMs
      };
    } catch (err) {
      throw this._buildError(err, targetUrl);
    }
  }

  /**
   * Detailed Error Diagnosis & Sanitization
   * Maps:
   * - 401 -> "gateway key is wrong or missing" configuration error
   * - 422 -> "request body does not match the gateway"
   * - 404 -> "wrong gateway path"
   * - Network / 5xx -> retryable error
   *
   * Never logs keys or full headers.
   */
  _diagnoseError(err, endpoint) {
    const status = err.response?.status;
    const data = err.response?.data;
    const detail = typeof data === 'object' ? (data?.detail || data?.error || data?.message) : (typeof data === 'string' ? data : null);
    const code = err.code || (status ? `HTTP_${status}` : 'NETWORK_ERROR');

    let message;
    let isRetryable = false;
    let isConfigurationError = false;

    if (status === 401) {
      message = 'AI gateway key is wrong or missing. Please verify AI_GATEWAY_API_KEY in the backend environment.';
      isConfigurationError = true;
    } else if (status === 422) {
      message = `Request body does not match the AI gateway: ${detail || 'Unprocessable Entity'}`;
    } else if (status === 404) {
      message = `Wrong AI gateway path at ${endpoint}. Supported gateway routes are /health, /api/v1/models, and /api/v1/chat.`;
    } else if (
      (status && status >= 500) ||
      ['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'ECONNABORTED', 'ENOTFOUND'].includes(err.code) ||
      err.message?.includes('timeout')
    ) {
      message = `AI gateway is temporarily unavailable or returned a server error (${status || err.code || 'timeout'}). This request can be retried.`;
      isRetryable = true;
    } else {
      message = detail || err.message || 'AI gateway request failed.';
    }

    // Log status and short reason only. Never log key or headers.
    console.error(`[AI Gateway] Error (${status || code}) on ${endpoint}: ${message}`);

    return {
      code,
      status: status || (isRetryable ? 503 : 500),
      message,
      isRetryable,
      isConfigurationError
    };
  }

  _buildError(err, endpoint) {
    const diag = this._diagnoseError(err, endpoint);
    const customErr = new Error(diag.message);
    customErr.code = diag.code;
    customErr.status = diag.status;
    customErr.statusCode = diag.status;
    customErr.isRetryable = diag.isRetryable;
    customErr.isConfigurationError = diag.isConfigurationError;
    customErr.originalError = err;
    return customErr;
  }
}

// Singleton instance export
export const aiGatewayClient = new AIGatewayClient();
export default aiGatewayClient;
