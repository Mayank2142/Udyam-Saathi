import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { request } from './api';
import { copy } from './journeyCopy';

const docs = ['address', 'signatory', 'gst', 'udyam'] as const;
type Kind = typeof docs[number];
type Decision = { status: 'pass' | 'action'; reason: string; date?: string; masked_id?: string; age_days?: number };
type Order = {
  id: string; status: string;
  lines: { sku: string; name: string; state: string }[];
  events: { step: string; at: string }[];
};
type State = {
  consented: boolean; business: string; docs: Partial<Record<Kind, Decision>>;
  pending: Kind[]; ready: boolean; order: Order | null;
  handoff: { status: 'queued' | 'reviewed' } | null;
};
type Assignment = { id: string; reference: string; status: 'queued' | 'reviewed'; blockers: string[]; monthly_paise: number; source: string };
const names = { K08: 'Green Leaf Legal Associates', K11: 'Trinity Bake House Pvt Ltd' };

export function useJourneyCopy() {
  const { i18n } = useTranslation();
  const locale = i18n.language === 'hi' ? 'hi' : i18n.language === 'mr' ? 'mr' : 'en';
  return { locale, s: (key: string) => copy[locale][key] ?? copy.en[key] ?? key };
}

export function Activation() {
  const { locale, s } = useJourneyCopy();
  const [data, setData] = useState<State | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [business, setBusiness] = useState(names.K08);
  const [applicant, setApplicant] = useState<keyof typeof names>('K08');

  useEffect(() => {
    let active = true;
    request<State>('/activation').then((state) => {
      if (!active) return;
      setData(state);
      if (state.business) {
        setBusiness(state.business);
        if (state.business === names.K11) setApplicant('K11');
      }
    }).catch((err: Error) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);

  const tracking = Boolean(data?.order && data.order.status !== 'active');
  const waiting = Boolean(data?.handoff?.status === 'queued');
  useEffect(() => {
    if (!tracking && !waiting) return;
    const timer = setInterval(() => {
      request<State>('/activation').then(setData).catch(() => {});
    }, 2500);
    return () => clearInterval(timer);
  }, [tracking, waiting]);

  async function act(action: string, path: string, body?: object, method = 'POST') {
    setBusy(action);
    setError('');
    try { setData(await request<State>('/activation' + path, method, body)); }
    catch (err) { setError(err instanceof Error ? err.message : 'error'); }
    finally { setBusy(''); }
  }

  return <section className="panel activation-panel" aria-labelledby="activation-heading">
    <div className="quote-top"><h2 id="activation-heading">{s(data?.order ? 'tracker' : 'kyc')}</h2>
      <span className="tag">{s('synthetic')}</span></div>
    {error && <div role="alert" className="alert">{s(error in copy.en ? error : 'workflow_error')}</div>}
    {!data && !error && <p role="status">{s('working')}</p>}
    {data?.order ? <Tracker order={data.order} /> : data && <>
      <p>{s('kyc_intro')}</p>
      {!data.consented ? <form onSubmit={(event) => {
        event.preventDefault();
        void act('consent', '/consent', { accepted: true, notice: 'saathi-synthetic-v1', language: locale, business });
      }}>
        <label className="field"><span>{s('applicant')}</span>
          <select value={applicant} onChange={(e) => {
            const next = e.target.value as keyof typeof names;
            setApplicant(next);
            setBusiness(names[next]);
          }}>
            <option value="K08">K08 · {names.K08}</option><option value="K11">K11 · {names.K11}</option>
          </select>
        </label>
        <label className="field"><span>{s('business')}</span>
          <input required minLength={3} maxLength={120} value={business} onChange={(e) => setBusiness(e.target.value)} />
        </label>
        <div className="notice"><strong>{s('consent_title')}</strong><p>{s('consent_copy')}</p>
          <label className="check-label"><input required type="checkbox" />{s('consent_agree')}</label>
        </div>
        <button type="submit" className="button" disabled={Boolean(busy)}>{s('consent_button')}</button>
      </form> : <>
        <div className="coverage-strip"><div><strong>{data.business}</strong><span>{s('consented')}</span></div>
          <button type="button" className="text-button" disabled={Boolean(busy)}
            onClick={() => void act('withdraw', '/consent', { accepted: false, notice: 'saathi-synthetic-v1', language: locale })}>{s('withdraw')}</button>
        </div>
        <div className="document-list">
          {docs.map((kind) => {
            const evidence = data.docs[kind];
            const pending = busy === kind || data.pending.includes(kind);
            return <article className="document" key={kind}>
              <div className="quote-top"><strong>{s(kind)}</strong>
                <span className={'tag ' + (evidence?.status === 'pass' ? 'good' : '')}>
                  {s(pending ? 'working' : evidence?.status === 'pass' ? 'passed' : evidence ? 'action' : 'missing')}
                </span>
              </div>
              {evidence && <p>{s(evidence.reason)}{evidence.date && ' · ' + evidence.date}
                {evidence.age_days !== undefined && ' · ' + evidence.age_days + ' ' + s('days')}
                {evidence.masked_id && ' · ' + evidence.masked_id}</p>}
              <div className="mini-actions">
                <button type="button" className="button secondary" disabled={Boolean(busy)}
                  onClick={() => void act(kind, '/specimens/' + applicant + '/' + kind)}>{s('use_specimen')}</button>
                <label className="button secondary upload">{s('upload')}
                  <input type="file" accept=".png,.jpg,.jpeg" disabled={Boolean(busy)} onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (!file) return;
                    const upload = new FormData();
                    upload.append('file', file);
                    void act(kind, '/documents/' + kind, upload);
                    event.target.value = '';
                  }} />
                </label>
                {evidence && <button type="button" className="text-button" disabled={Boolean(busy)}
                  onClick={() => void act(kind, '/documents/' + kind, undefined, 'DELETE')}>{s('clear')}</button>}
                {kind === 'address' && applicant === 'K11' && evidence?.reason === 'stale_proof' &&
                  <button type="button" className="button secondary" disabled={Boolean(busy)}
                    onClick={() => void act(kind, '/replacement')}>{s('use_replacement')}</button>}
              </div>
            </article>;
          })}
        </div>
        <p className="requirements">{s(data.ready ? 'ready' : 'requirements')}</p>
        <div className="mini-actions">
          <button type="button" className="button" disabled={!data.ready || Boolean(busy)}
            onClick={() => void act('order', '/order')}>{s('place_order')}</button>
          <button type="button" className="button secondary" disabled={Boolean(data.handoff || busy)}
            onClick={() => void act('handoff', '/handoff')}>{s('handoff')}</button>
        </div>
        {data.handoff && <p role="status">{s(data.handoff.status === 'reviewed' ? 'partner_reviewed' : 'partner_queued')}</p>}
      </>}
    </>}
  </section>;
}

function Tracker({ order }: { order: Order }) {
  const { locale, s } = useJourneyCopy();
  return <>
    <p>{s('tracker_help')}</p>
    <p className="tracker-status"><strong>{s(order.status)}</strong><span>{order.id.slice(0, 8).toUpperCase()}</span></p>
    <ul className="status-list">{order.lines.map((line) => <li key={line.sku}>
      <span>{line.name}</span><span className={'tag ' + (line.state === 'active' ? 'good' : '')}>{s(line.state)}</span>
    </li>)}</ul>
    <h3>{s('timeline')}</h3>
    <ol className="status-list">{[...order.events].reverse().map((event) => <li key={event.step}>
      <strong>{s(event.step)}</strong><time dateTime={event.at}>
        {new Date(event.at).toLocaleTimeString(locale + '-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </time>
    </li>)}</ol>
  </>;
}

export function PartnerQueue() {
  const { locale, s } = useJourneyCopy();
  const [items, setItems] = useState<Assignment[]>([]);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState('');
  async function refresh() {
    try { setItems(await request<Assignment[]>('/activation/partner')); setError(false); }
    catch { setError(true); }
  }
  useEffect(() => { void refresh(); }, []);
  async function acknowledge(id: string) {
    setBusy(id);
    try { await request('/activation/partner/' + id + '/acknowledge', 'POST'); await refresh(); }
    catch { setError(true); }
    finally { setBusy(''); }
  }
  return <section className="panel activation-panel">
    <div className="quote-top"><h2>{s('partner_queue')}</h2>
      <button type="button" className="button secondary" onClick={() => void refresh()}>{s('refresh')}</button></div>
    <p>{s('partner_help')}</p>
    {error && <p role="alert" className="alert">{s('workflow_error')}</p>}
    {!items.length && <p>{s('no_assignments')}</p>}
    {items.map((item) => <article className="document" key={item.id}>
      <div className="quote-top"><strong>{item.reference}</strong><span className="tag">{s(item.status === 'reviewed' ? 'partner_reviewed' : 'partner_queued')}</span></div>
      <p>{new Intl.NumberFormat(locale + '-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(item.monthly_paise / 100)} · {s(item.source)}</p>
      {item.blockers.length > 0 && <p>{item.blockers.map(s).join(' · ')}</p>}
      <button type="button" className="button secondary" disabled={item.status === 'reviewed' || Boolean(busy)}
        onClick={() => void acknowledge(item.id)}>{s('acknowledge')}</button>
    </article>)}
  </section>;
}
