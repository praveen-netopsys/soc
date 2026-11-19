import crypto from 'crypto';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.join(__dirname, '..', '.env') });
dotenv.config();

let secret = process.env.JWT_SECRET;

if (!secret || secret.trim() === '' || secret === 'super-secret-key-change-in-prod') {
  secret = 'skillbridge_production_jwt_master_secret_2026_qwen_explain_studio';
  process.env.JWT_SECRET = secret;
}

export const JWT_SECRET = secret;
export default JWT_SECRET;

