const configuredBase = String(import.meta.env.VITE_API_BASE_URL || '').trim();
const API_BASE = configuredBase;

function apiUrl(path) {
  return API_BASE ? `${API_BASE}${path}` : path;
}

async function getErrorMessage(res, fallback) {
  let body = '';
  try {
    body = await res.text();
  } catch {
    return fallback;
  }

  if (!body) return fallback;
  try {
    const parsed = JSON.parse(body);
    if (parsed?.detail) return String(parsed.detail);
    if (parsed?.message) return String(parsed.message);
  } catch {
    // not JSON
  }
  return body;
}

export async function translateText(payload) {
  const res = await fetch(apiUrl('/api/v1/translate'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const message = await getErrorMessage(res, 'Translation request failed');
    throw new Error(message);
  }
  return res.json();
}

export async function getProviderCatalog() {
  const res = await fetch(apiUrl('/api/v1/providers'));
  if (!res.ok) throw new Error('Failed to load providers');
  return res.json();
}

export async function validateApiKey(payload) {
  const res = await fetch(apiUrl('/api/v1/keys/validate'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const message = await getErrorMessage(res, 'Key validation request failed');
    throw new Error(message);
  }
  return res.json();
}

export async function health() {
  const res = await fetch(apiUrl('/health'));
  if (!res.ok) throw new Error('Health check failed');
  return res.json();
}


