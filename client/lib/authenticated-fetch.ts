export type GetToken = () => Promise<string | null>;
export type ApiFetch = (url: string, options?: RequestInit) => Promise<Response>;

export function createAuthenticatedFetch(getToken: GetToken): ApiFetch {
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
