// Where the recommendation backend lives. Override at build time with
// WXT_BACKEND_URL (WXT exposes WXT_*/VITE_* to the bundle), because the
// deployed origin differs from a local dev backend.
//
// Keep this in sync with host_permissions in wxt.config.ts: the extension
// cannot call an origin it has no permission for, and a mismatch fails at
// runtime rather than at build time.
const DEFAULT_BACKEND_URL = 'http://localhost:8000';

export const BACKEND_URL = import.meta.env.WXT_BACKEND_URL || DEFAULT_BACKEND_URL;

// POST /api/v1/search ranks the user's cards for one purchase
// (backend/app/api/v1/routes/search.py). The prefix comes from
// backend/app/core/config.py api_v1_prefix.
export const SEARCH_ENDPOINT = new URL('/api/v1/search', BACKEND_URL).href;
