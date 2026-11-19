import axios from 'axios';
import jwt from 'jsonwebtoken';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:5000';
const JWT_SECRET = process.env.JWT_SECRET || 'super-secret-key-change-in-prod';

const validToken = jwt.sign(
  { id: 'dev-tester', role: 'STUDENT', email: 'dev@skillbridge.com', tenantCode: 'default' },
  JWT_SECRET,
  { expiresIn: '1h' }
);

async function runTests() {
  console.log('====================================================');
  console.log('   CENTRAL AI GATEWAY INTEGRATION TEST SUITE       ');
  console.log('====================================================');
  console.log(`Backend Target:  ${BACKEND_URL}`);
  console.log(`Gateway Setting: ${process.env.AI_GATEWAY_URL || 'Not configured'}`);
  console.log('====================================================\n');

  // 1. Health Check
  console.log('TEST 1: Backend-to-Gateway Health Check (GET /api/ai/health)');
  try {
    const res = await axios.get(`${BACKEND_URL}/api/ai/health`, { timeout: 10000 });
    console.log('✅ Health Check OK:', res.data);
  } catch (err) {
    console.log('⚠️ Health Check Response:', err.response?.data || err.message);
  }
  console.log('----------------------------------------------------\n');

  // 2. Authentication Enforcement (No Token)
  console.log('TEST 2: Authentication Security Check (Reject Missing Token)');
  try {
    await axios.get(`${BACKEND_URL}/api/ai/models`);
    console.log('❌ FAIL: Request without token was unexpectedly allowed!');
  } catch (err) {
    if (err.response?.status === 401) {
      console.log('✅ PASS: Unauthenticated request rejected with HTTP 401.');
    } else {
      console.log(`⚠️ Status ${err.response?.status}:`, err.response?.data);
    }
  }
  console.log('----------------------------------------------------\n');

  // 3. Model Listing with Valid Token
  console.log('TEST 3: Model Listing (GET /api/ai/models)');
  try {
    const res = await axios.get(`${BACKEND_URL}/api/ai/models`, {
      headers: { Authorization: `Bearer ${validToken}` },
      timeout: 10000
    });
    console.log('✅ Model Listing OK:', res.data);
  } catch (err) {
    console.log('⚠️ Model Listing Response:', err.response?.data || err.message);
  }
  console.log('----------------------------------------------------\n');

  // 4. Basic Chat Completion
  console.log('TEST 4: Chat Completion (POST /api/ai/chat)');
  try {
    const res = await axios.post(
      `${BACKEND_URL}/api/ai/chat`,
      {
        message: 'What is artificial intelligence in one sentence?',
        system_prompt: 'You are a concise tutor.'
      },
      {
        headers: {
          Authorization: `Bearer ${validToken}`,
          'Content-Type': 'application/json'
        },
        timeout: 15000
      }
    );
    console.log('✅ Chat Completion OK:', res.data);
  } catch (err) {
    console.log('⚠️ Chat Completion Response:', err.response?.data || err.message);
  }
  console.log('----------------------------------------------------\n');

  // 5. Input Validation (Empty Message)
  console.log('TEST 5: Chat Input Validation (Empty Message Rejection)');
  try {
    await axios.post(
      `${BACKEND_URL}/api/ai/chat`,
      { message: '   ' },
      {
        headers: {
          Authorization: `Bearer ${validToken}`,
          'Content-Type': 'application/json'
        }
      }
    );
    console.log('❌ FAIL: Empty message was allowed!');
  } catch (err) {
    if (err.response?.status === 400) {
      console.log('✅ PASS: Empty message rejected with HTTP 400:', err.response.data);
    } else {
      console.log(`⚠️ Status ${err.response?.status}:`, err.response?.data);
    }
  }
  console.log('----------------------------------------------------\n');

  console.log('====================================================');
  console.log('   TEST SUITE EXECUTION COMPLETED                   ');
  console.log('====================================================');
}

runTests().catch(console.error);
