import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { useTranslation } from 'react-i18next';
import { acceptQuote, loadCase, request, saveCase, type Broadband, type Needs, type Quote, type SavedCase, type Session } from './api';
import { Activation, PartnerQueue, useJourneyCopy } from './Activation';
import './i18n';
import './style.css';

const initialNeeds: Needs = {
  segment: 'office', employees: 5, sites: 1, city_tier: 2, speed: 300,
  email_seats: 5, devices: 5, monthly_budget: 3500, coverage: 'fibre',
};
const planNames: Record<Broadband, string> = {
  P01: 'Biz Fibre 100 Mbps', P02: 'Biz Fibre 300 Mbps', P04: 'Biz 5G AirFiber 100 Mbps',
};
type Step = 'needs' | 'quote' | 'saved';

function Icon({ name, size = 20 }: { name: 'arrow' | 'check' | 'wifi' | 'shield' | 'receipt' | 'briefcase'; size?: number }) {
  const paths = {
    arrow: 'M4 12h15m-6-6 6 6-6 6',
    check: 'm5 12 4 4L19 6',
    wifi: 'M3 9a15 15 0 0 1 18 0M6 13a10 10 0 0 1 12 0m-9 4a5 5 0 0 1 6 0m-3 4h.01',
    shield: 'M12 22s7-3 7-9V6l-7-3-7 3v7c0 6 7 9 7 9Zm-3-10 2 2 4-4',
    receipt: 'M5 3v18l3-2 4 2 4-2 3 2V3l-3 2-4-2-4 2Zm4 6h6m-6 4h6',
    briefcase: 'M4 7h16a1 1 0 0 1 1 1v11H3V8a1 1 0 0 1 1-1Zm5 0V4h6v3M3 12h18',
  };
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>;
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function money(paise: number, language: string) {
  const locale = { en: 'en-IN', hi: 'hi-IN', mr: 'mr-IN' }[language] ?? 'en-IN';
  return new Intl.NumberFormat(locale, { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100);
}

function Summary({ quote, status }: { quote: Quote | null; status: 'draft' | 'saved' }) {
  const { t, i18n } = useTranslation();
  const price = (value: number) => money(value, i18n.language);
  return <aside className="summary" aria-label={t('summary')}>
    <div className="summary-cap"><Icon name="receipt" /><span>{t('summary')}</span>{quote && <span className="status-pill">{t(status)}</span>}</div>
    {quote ? <>
      <div className="hero-amount" aria-live="polite"><strong>{price(quote.monthly_paise)}</strong><span>{t('perMonth')}</span></div>
      <dl className="summary-totals">
        <div><dt>{t('recurring')}</dt><dd>{price(quote.monthly_paise)}</dd></div>
        <div><dt>{t('setup')}</dt><dd>{price(quote.setup_paise)}</dd></div>
      </dl>
      <p className="taxes">{t('taxes')}</p>
    </> : <>
      <h2>{t('previewTitle')}</h2><p className="summary-help">{t('previewHelp')}</p>
      <ul className="preview-list">
        <li><Icon name="wifi" />{t('preview1')}</li>
        <li><Icon name="shield" />{t('preview2')}</li>
        <li><Icon name="receipt" />{t('preview3')}</li>
      </ul>
    </>}
  </aside>;
}

function App() {
  const { t, i18n } = useTranslation();
  const { locale, s } = useJourneyCopy();
  const [session, setSession] = useState<Session | null>(null);
  const [record, setRecord] = useState<SavedCase | null>(null);
  const [needs, setNeeds] = useState<Needs>(initialNeeds);
  const [step, setStep] = useState<Step>('needs');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadFailed, setLoadFailed] = useState(false);
  const [description, setDescription] = useState('');
  const [advisorBusy, setAdvisorBusy] = useState(false);
  const [advisorNotice, setAdvisorNotice] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const moveFocus = useRef(false);

  useEffect(() => {
    let cancelled = false;
    request<Session>('/session').then((current) => {
      if (cancelled) return null;
      setSession(current);
      return current.role === 'partner' ? null : loadCase();
    }).then((saved) => {
      if (cancelled || !saved) return;
      setRecord(saved);
      setNeeds(saved.needs);
      setStep(saved.accepted_at ? 'saved' : 'quote');
    }).catch(() => {
      if (!cancelled) { setError('error'); setLoadFailed(true); }
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    document.documentElement.lang = i18n.language;
    localStorage.setItem('saathi_language', i18n.language);
  }, [i18n.language]);

  useEffect(() => {
    if (moveFocus.current) {
      heading.current?.focus();
      window.scrollTo(0, 0);
      moveFocus.current = false;
    }
  }, [step]);

  function navigate(nextStep: Step) {
    setError('');
    moveFocus.current = nextStep !== step;
    setStep(nextStep);
  }

  function update<K extends keyof Needs>(field: K, value: Needs[K]) {
    setNeeds((current) => ({ ...current, [field]: value }));
  }

  async function configure(broadband?: Broadband, failover = false) {
    setError('');
    setBusy(true);
    try {
      const next = await saveCase(record?.revision ?? 0, step === 'needs' ? needs : record!.needs, broadband, failover);
      setRecord(next);
      navigate('quote');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'error');
    } finally {
      setBusy(false);
    }
  }

  async function suggest() {
    if (description.trim().length < 4) return;
    setAdvisorBusy(true);
    setAdvisorNotice('');
    try {
      const { fields } = await request<{ fields: Partial<Needs> }>('/advisor', 'POST', { text: description.trim(), language: locale });
      if (Object.keys(fields).length) {
        setNeeds((current) => ({ ...current, ...fields }));
        setAdvisorNotice('advisor_review');
      } else setAdvisorNotice('advisor_empty');
    } catch { setAdvisorNotice('advisor_unavailable'); }
    finally { setAdvisorBusy(false); }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (needs.email_seats > needs.employees) { setError('emailLimit'); return; }
    void configure();
  }

  async function accept() {
    if (!record?.quote) return;
    setBusy(true);
    setError('');
    try {
      setRecord(await acceptQuote(record.revision, record.quote.id));
      navigate('saved');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'error');
    } finally {
      setBusy(false);
    }
  }

  const price = (paise: number) => money(paise, i18n.language);
  const quote = record?.quote ?? null;
  const progress = [
    { id: 'needs', label: 'stepNeeds' }, { id: 'quote', label: 'stepQuote' }, { id: 'saved', label: 'stepSave' },
  ];
  const currentIndex = progress.findIndex(({ id }) => id === step);
  const errorNeedsReload = loadFailed || error === 'case_changed' || error === 'quote_changed';
  const expired = error === 'quote_expired' || (quote && Date.parse(quote.expires_at) <= Date.now());

  return <>
    <header className="topbar"><div className="shell topbar-inner">
      <div className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
        <div><strong>Udyam Saathi</strong><small>{t('journey')}</small></div>
      </div>
      <div className="topbar-actions">
        <span className="environment"><i />{t('sandbox')}</span>
        {session?.role ? <form action="/api/auth/logout" method="post"><button className="button secondary auth-link">{s('sign_out')}</button></form>
          : session?.enabled && <a className="button secondary auth-link" href="/api/auth/login">{s('sign_in')}</a>}
        <label className="locale"><span className="sr-only">{t('language')}</span>
          <select value={i18n.language} onChange={(event) => void i18n.changeLanguage(event.target.value)}>
            <option value="en">English</option><option value="hi">हिन्दी</option><option value="mr">मराठी</option>
          </select>
        </label>
      </div>
    </div></header>
    <main className="shell main">
      {session?.role === 'partner' ? <PartnerQueue /> : <>
      <ol className="steps">
        {progress.map(({ id, label }, index) => <li key={id} aria-current={step === id ? 'step' : undefined}
          className={index <= currentIndex ? 'reached' : ''}>
          <span className="step-number">{index < currentIndex ? <Icon name="check" size={15} /> : index + 1}</span>{t(label)}
        </li>)}
      </ol>
      <div className="page-heading">
        <h1 tabIndex={-1} ref={heading}>{t(step === 'needs' ? 'intro' : step === 'quote' ? 'quote' : 'accepted')}</h1>
        <p>{t(step === 'needs' ? 'introHelp' : step === 'quote' ? 'quoteHelp' : 'acceptedHelp')}</p>
      </div>
      {loading && <p role="status">{t('loading')}</p>}
      {error && <div className="alert" role="alert">{error === 'order_already_placed' ? s(error) : t(error, { defaultValue: t('error') })}
        {errorNeedsReload && <button type="button" className="text-button" onClick={() => location.reload()}>{t('reload')}</button>}
      </div>}
      {!loading && !loadFailed && <div className="layout">
        <div className="primary">
          {step === 'needs' && <>
            <section className="panel advisor-panel">
              <label className="field"><span>{s('advisor_title')}</span>
                <textarea rows={2} maxLength={450} value={description} placeholder={s('advisor_example')}
                  onChange={(event) => setDescription(event.target.value)} />
              </label>
              <button className="button secondary" type="button" disabled={description.trim().length < 4 || advisorBusy}
                onClick={() => void suggest()}>{s(advisorBusy ? 'working' : 'advisor_button')}</button>
              {advisorNotice && <p className="requirements" role="status">{s(advisorNotice)}</p>}
            </section>
          <form onSubmit={submit}>
            <section className="panel">
              <div className="panel-heading"><span className="panel-icon"><Icon name="briefcase" /></span><div>
                <h2>{t('profile')}</h2><p>{t('profileHelp')}</p>
              </div></div>
              <div className="fields">
                <Field label={t('segment')}><select value={needs.segment} onChange={(e) => update('segment', e.target.value as Needs['segment'])}>
                  <option value="office">{t('office')}</option><option value="retail">{t('retail')}</option><option value="other">{t('other')}</option>
                </select></Field>
                <Field label={t('city')}><select value={needs.city_tier} onChange={(e) => update('city_tier', Number(e.target.value) as 1 | 2)}>
                  <option value="1">{t('tier1')}</option><option value="2">{t('tier2')}</option>
                </select></Field>
                <Field label={t('employees')}><input type="number" required min={1} max={250} value={needs.employees} onChange={(e) => update('employees', Number(e.target.value))} /></Field>
                <Field label={t('sites')}><input type="number" required min={1} max={25} value={needs.sites} onChange={(e) => update('sites', Number(e.target.value))} /></Field>
              </div>
            </section>
            <section className="panel">
              <div className="panel-heading"><span className="panel-icon"><Icon name="wifi" /></span><div>
                <h2>{t('services')}</h2><p>{t('servicesHelp')}</p>
              </div></div>
              <div className="fields">
                <Field label={t('speed')}><select value={needs.speed} onChange={(e) => update('speed', Number(e.target.value) as 100 | 300)}>
                  <option value="100">100 Mbps</option><option value="300">300 Mbps</option>
                </select></Field>
                <Field label={t('coverage')}><select value={needs.coverage} onChange={(e) => update('coverage', e.target.value as Needs['coverage'])}>
                  <option value="fibre">{t('fibre')}</option><option value="wireless">{t('wireless')}</option><option value="unknown">{t('unknown')}</option>
                </select></Field>
                <Field label={t('emailSeats')}><input type="number" required min={0} max={250} value={needs.email_seats} onChange={(e) => update('email_seats', Number(e.target.value))} /></Field>
                <Field label={t('devices')}><input type="number" required min={0} max={1000} value={needs.devices} onChange={(e) => update('devices', Number(e.target.value))} /></Field>
                <Field label={t('budget')} hint={t('budgetHelp')}><input type="number" required min={0} max={1000000} value={needs.monthly_budget} onChange={(e) => update('monthly_budget', Number(e.target.value))} /></Field>
              </div>
            </section>
            <div className="actions"><button className="button" disabled={busy || advisorBusy} type="submit">{t(busy ? 'working' : 'buildQuote')}<Icon name="arrow" size={18} /></button></div>
          </form></>}
          {step !== 'needs' && record && <>
            {record.blocker && <div className="alert" role="status"><strong>{t('attention')}</strong><p>{t(record.blocker)}</p></div>}
            {quote && <section className="panel quote-panel">
              <div className="quote-top"><h2>{t('items')}</h2><span className="case-ref">{t('case', { id: record.id.slice(0, 8).toUpperCase() })}</span></div>
              <div className="coverage-strip"><Icon name="wifi" /><div><strong>{t('coverageLabel')}</strong><span>{t(record.needs.coverage)}</span></div><span className="tag">{t('simulated')}</span></div>
              {step === 'quote' && record.options.length > 1 && <Field label={t('broadband')}><select disabled={busy} value={record.broadband ?? ''}
                onChange={(e) => void configure(e.target.value as Broadband, record.failover)}>
                {record.options.map((sku) => <option key={sku} value={sku}>{planNames[sku]}</option>)}
              </select></Field>}
              <ul className="quote-lines">{quote.lines.map((line) => <li key={line.sku}>
                <div><strong>{line.name}</strong><p>{t(line.reason === 'wireless' ? 'wirelessReason' : line.reason === 'failover' ? 'backup' : line.reason)}</p>
                  <small>{t('units', { count: line.quantity, rate: price(line.rate_paise) })} · {t('months', { count: line.minimum_months })}</small>
                </div><div className="line-price"><strong>{price(line.monthly_paise)}</strong>
                  {line.setup_paise > 0 && <small>{t('setupLine', { amount: price(line.setup_paise) })}</small>}
                </div>
              </li>)}</ul>
              {step === 'quote' && <label className="add-on"><input type="checkbox" checked={record.failover}
                disabled={busy || record.broadband === 'P04'}
                onChange={(e) => void configure(record.broadband ?? undefined, e.target.checked)} />
                <span><strong>{t('failover')}</strong><small>{t(record.broadband === 'P04' ? 'failoverUnavailable' : 'failoverHelp')}</small></span>
              </label>}
            </section>}
            {step === 'quote' && <div className="actions">
              <button className="button secondary" type="button" disabled={busy} onClick={() => navigate('needs')}>{t('editNeeds')}</button>
              {quote && expired && <button className="button" disabled={busy} type="button"
                onClick={() => void configure(record.broadband ?? undefined, record.failover)}>{t('refreshQuote')}</button>}
              {quote && !expired && <button className="button" disabled={busy || !!record.blocker} type="button"
                onClick={() => void accept()}>{t(busy ? 'working' : 'accept')}<Icon name="arrow" size={18} /></button>}
            </div>}
          </>}
          {step === 'saved' && record && (session?.role === 'owner' ? <Activation /> : <section className="panel saved-panel">
            <span className="confirmation"><Icon name="check" size={25} /></span>
            <h2>{t('savedForRecords')}</h2><p>{t('savedForRecordsHelp')}</p>
            <div className="mini-actions">
              <button className="button secondary" type="button" onClick={() => navigate('needs')}>{t('editNeeds')}</button>
              {session?.enabled && <a className="button" href="/api/auth/login">{s('sign_in_next')}</a>}
            </div>
          </section>)}
        </div>
        <Summary quote={step === 'needs' ? null : quote} status={record?.accepted_at ? 'saved' : 'draft'} />
      </div>}
      </>}
    </main>
    <footer className="shell footer"><span>{t('disclaimer')}</span><span>Udyam Saathi</span></footer>
  </>;
}

createRoot(document.getElementById('root')!).render(<App />);
