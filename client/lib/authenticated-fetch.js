export function createAuthenticatedFetch(getToken) {
  return async (url, options = {}) => {
    options.signal?.throwIfAborted();
    const token = await getToken();
    options.signal?.throwIfAborted();
    if (!token) throw new Error('Sign in again to continue.');
    const headers = new Headers(options.headers);
    headers.set('Authorization', `Bearer ${token}`);
    return fetch(url, { ...options, headers, cache: 'no-store' });
  };
}
