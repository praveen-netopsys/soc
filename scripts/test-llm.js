#!/usr/bin/env node

/**
 * AI Gateway Verification Script
 *
 * Calls the three confirmed public gateway endpoints:
 * 1. GET  /health          (no auth key needed)
 * 2. GET  /api/v1/models   (X-API-Key required)
 * 3. POST /api/v1/chat     (X-API-Key required, body: {"message": "Hello"})
 *
 * Security:
 * - Prints only HTTP status codes and the first 100 characters of the chat answer.
 * - NEVER prints or logs the API key or full headers.
 */

const fs = require('fs');
const path = require('path');

// Resilient .env loader without external dependencies
function loadEnv() {
  const candidates = [
    path.resolve(process.cwd(), 'Backend/.env'),
    path.resolve(process.cwd(), '.env'),
    path.resolve(__dirname, '../Backend/.env'),
    path.resolve(__dirname, '../.env'),
    path.resolve(__dirname, '.env')
  ];

  for (const envPath of candidates) {
    if (fs.existsSync(envPath)) {
      try {
        const content = fs.readFileSync(envPath, 'utf8');
        for (const line of content.split(/\r?\n/)) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith('#')) continue;
          const eqIdx = trimmed.indexOf('=');
          if (eqIdx === -1) continue;
          const key = trimmed.slice(0, eqIdx).trim();
          let val = trimmed.slice(eqIdx + 1).trim();
          if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
            val = val.slice(1, -1);
          }
          if (!process.env[key]) {
            process.env[key] = val;
          }
        }
      } catch (_) {}
    }
  }
}

loadEnv();

const baseUrl = (
  process.env.AI_GATEWAY_URL ||
  process.env.QWEN_GATEWAY_URL ||
  process.env.SKILLBRIDGE_LLM_GATEWAY_URL ||
  process.env.LLM_GATEWAY_URL ||
  ''
).trim().replace(/\/+$/, '');

const apiKey = (
  process.env.AI_GATEWAY_API_KEY ||
  process.env.AI_API_KEY ||
  process.env.QWEN_API_KEY ||
  process.env.BRUTALLYHIRED_LLM_API_KEY ||
  process.env.LLM_GATEWAY_API_KEY ||
  process.env.SKILLBRIDGE_LLM_GATEWAY_API_KEY ||
  ''
).trim();

if (!baseUrl) {
  console.error('Error: AI_GATEWAY_URL is not set in environment.');
  process.exit(1);
}

async function run() {
  let allPassed = true;

  // 1. GET /health
  try {
    const res = await fetch(`${baseUrl}/health`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(15000)
    });
    console.log(`GET /health -> HTTP ${res.status}`);
    if (res.status !== 200) allPassed = false;
  } catch (err) {
    console.error(`GET /health -> Error: ${err.message}`);
    allPassed = false;
  }

  // 2. GET /api/v1/models
  try {
    const headers = { Accept: 'application/json' };
    if (apiKey) headers['X-API-Key'] = apiKey;

    const res = await fetch(`${baseUrl}/api/v1/models`, {
      method: 'GET',
      headers,
      signal: AbortSignal.timeout(20000)
    });
    console.log(`GET /api/v1/models -> HTTP ${res.status}`);
    if (res.status !== 200) allPassed = false;
  } catch (err) {
    console.error(`GET /api/v1/models -> Error: ${err.message}`);
    allPassed = false;
  }

  // 3. POST /api/v1/chat
  try {
    const headers = {
      Accept: 'application/json',
      'Content-Type': 'application/json'
    };
    if (apiKey) headers['X-API-Key'] = apiKey;

    const res = await fetch(`${baseUrl}/api/v1/chat`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ message: 'Hello' }),
      signal: AbortSignal.timeout(120000)
    });

    console.log(`POST /api/v1/chat -> HTTP ${res.status}`);
    if (res.status === 200) {
      const data = await res.json();
      const answer = typeof data?.response === 'string'
        ? data.response
        : (typeof data?.reply === 'string' ? data.reply : '');
      const preview = (answer || '').slice(0, 100).replace(/\r?\n/g, ' ');
      console.log(`Answer preview: "${preview}"`);
    } else {
      allPassed = false;
    }
  } catch (err) {
    console.error(`POST /api/v1/chat -> Error: ${err.message}`);
    allPassed = false;
  }

  if (!allPassed) {
    process.exit(1);
  }
}

run();
