import { verifyToken } from '@clerk/backend';

export function createRequireAuth({ secretKey, jwtKey, issuer, authorizedParties }, verify = verifyToken) {
  if ((!secretKey && !jwtKey) || !issuer || !authorizedParties?.length) {
    throw new Error('Configure CLERK_SECRET_KEY (or CLERK_JWT_KEY), CLERK_ISSUER, and CLIENT_ORIGINS in server/.env.');
  }
  return async (req, res, next) => {
    res.set('Cache-Control', 'no-store');
    const token = req.get('authorization')?.match(/^Bearer ([^\s]+)$/i)?.[1];
    if (!token) return res.status(401).json({ message: 'Sign in to continue.' });
    try {
      const data = await verify(token, { secretKey, jwtKey, authorizedParties });
      // Also pin the instance and require a session token, not another token type.
      if (!data?.sub?.startsWith('user_') || !data.sid?.startsWith('sess_') ||
          data.iss !== issuer || !authorizedParties.includes(data.azp)) throw new Error('Invalid session');
      req.ownerId = data.sub;
      next();
    } catch {
      res.status(401).json({ message: 'Your session could not be verified. Sign in again.' });
    }
  };
}
