export type Broadband = 'P01' | 'P02' | 'P04';

export interface Needs {
  segment: 'office' | 'retail' | 'other';
  employees: number;
  sites: number;
  city_tier: 1 | 2;
  speed: 100 | 300;
  email_seats: number;
  devices: number;
  monthly_budget: number;
  coverage: 'fibre' | 'wireless' | 'unknown';
}

export interface QuoteLine {
  sku: string;
  name: string;
  unit: string;
  quantity: number;
  rate_paise: number;
  monthly_paise: number;
  setup_paise: number;
  minimum_months: number;
  reason: 'fibre100' | 'fibre300' | 'wireless' | 'email' | 'security' | 'failover';
}

export interface Quote {
  id: string;
  version: string;
  monthly_paise: number;
  setup_paise: number;
  lines: QuoteLine[];
  expires_at: string;
}

export interface SavedCase {
  id: string;
  revision: number;
  needs: Needs;
  broadband: Broadband | null;
  failover: boolean;
  options: Broadband[];
  quote: Quote | null;
  blocker: string | null;
  accepted_at: string | null;
}

export async function request<T>(path: string, method = 'GET', body?: object): Promise<T> {
  const upload = body instanceof FormData;
  const result = await fetch('/api' + path, {
    method,
    credentials: 'same-origin',
    headers: body && !upload ? { 'Content-Type': 'application/json' } : undefined,
    body: upload ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!result.ok) {
    const detail = await result.json().catch(() => null);
    throw new Error(typeof detail?.detail === 'string' ? detail.detail : 'error');
  }
  return result.json();
}

export const loadCase = () => request<SavedCase | null>('/case');

export const saveCase = (revision: number, needs: Needs, broadband?: Broadband, failover = false) =>
  request<SavedCase>('/case', 'PUT', { revision, needs, broadband, failover });

export const acceptQuote = (revision: number, quote_id: string) =>
  request<SavedCase>('/case/accept', 'POST', { revision, quote_id });

export interface Session {
  enabled: boolean;
  role: 'owner' | 'partner' | null;
  name: string | null;
}
