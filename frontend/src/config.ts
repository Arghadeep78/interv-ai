// ---------------------------------------------------------------------------
// Runtime config — backend endpoints are read from Vite env vars so the app
// can be deployed without code changes. Set VITE_API_URL in a `.env` file
// (see `.env.example`); it falls back to the local dev backend.
// ---------------------------------------------------------------------------

// e.g. "http://localhost:8000" or "https://api.example.com"
export const API_URL: string =
  import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// Derive the WebSocket origin from the API URL: http -> ws, https -> wss.
export const WS_URL: string = API_URL.replace(/^http/, 'ws');
