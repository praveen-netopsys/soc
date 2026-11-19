import rateLimit from 'express-rate-limit';

const isDevOrLocal = (req) => {
  // In development or local testing, never rate-limit developers / testers
  if (process.env.NODE_ENV !== 'production' || process.env.DISABLE_RATE_LIMIT === 'true') {
    return true;
  }
  const ip = req.ip || req.connection?.remoteAddress || '';
  if (ip === '127.0.0.1' || ip === '::1' || ip.includes('127.0.0.1') || ip === 'localhost') {
    return true;
  }
  return false;
};

/**
 * Rate limiter for authentication endpoints:
 * - /api/v1/auth/login
 * - /api/v1/auth/face-login
 * - /api/v1/admin/login
 * - /api/v1/super-admin/auth/login
 */
export const authRateLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: process.env.NODE_ENV === 'production' ? 100 : 10000,
  standardHeaders: true,
  legacyHeaders: false,
  skipSuccessfulRequests: true,
  skip: isDevOrLocal,
  validate: { trustProxy: false },
  message: {
    error: 'Too many login attempts from this network. Please try again after 15 minutes.'
  },
});

/**
 * Stricter limiter for password reset and initial password setup:
 */
export const passwordResetLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: process.env.NODE_ENV === 'production' ? 20 : 10000,
  standardHeaders: true,
  legacyHeaders: false,
  skip: isDevOrLocal,
  validate: { trustProxy: false },
  message: {
    error: 'Too many password reset attempts. Please wait 15 minutes before trying again.'
  }
});

/**
 * General API rate limiter for regular traffic protection against DoS/scraping:
 */
export const generalApiLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: process.env.NODE_ENV === 'production' ? 1200 : 50000,
  standardHeaders: true,
  legacyHeaders: false,
  skip: isDevOrLocal,
  validate: { trustProxy: false },
  message: {
    error: 'Too many requests. Please slow down and try again later.'
  }
});

