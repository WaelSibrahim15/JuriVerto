import { useEffect, useMemo, useRef, useState } from 'react';
import { getProviderCatalog, translateText, validateApiKey } from './api';
import TranslatorPane from './components/TranslatorPane';
import TracePanel from './components/TracePanel';
import SettingsModal from './components/SettingsModal';

const LANGUAGES = [
  'Afrikaans',
  'Albanian',
  'Amharic',
  'Arabic',
  'Armenian',
  'Assamese',
  'Azerbaijani',
  'Basque',
  'Belarusian',
  'Bengali',
  'Bosnian',
  'Bulgarian',
  'Burmese',
  'Catalan',
  'Cebuano',
  'Chinese',
  'Chinese (Cantonese)',
  'Chinese (Traditional)',
  'Corsican',
  'Croatian',
  'Czech',
  'Danish',
  'Dari',
  'Dutch',
  'English',
  'Esperanto',
  'Estonian',
  'Filipino',
  'Finnish',
  'French',
  'Frisian',
  'Galician',
  'Georgian',
  'German',
  'Greek',
  'Gujarati',
  'Haitian Creole',
  'Hausa',
  'Hawaiian',
  'Hebrew',
  'Hindi',
  'Hmong',
  'Hungarian',
  'Icelandic',
  'Igbo',
  'Indonesian',
  'Irish',
  'Italian',
  'Japanese',
  'Javanese',
  'Kannada',
  'Kazakh',
  'Khmer',
  'Kinyarwanda',
  'Korean',
  'Kurdish',
  'Kyrgyz',
  'Lao',
  'Latin',
  'Latvian',
  'Lithuanian',
  'Luxembourgish',
  'Macedonian',
  'Malagasy',
  'Malay',
  'Malayalam',
  'Maltese',
  'Maori',
  'Marathi',
  'Mongolian',
  'Nepali',
  'Norwegian',
  'Nyanja',
  'Odia',
  'Pashto',
  'Persian',
  'Polish',
  'Portuguese',
  'Punjabi',
  'Romanian',
  'Russian',
  'Samoan',
  'Scots Gaelic',
  'Serbian',
  'Sesotho',
  'Shona',
  'Sindhi',
  'Sinhala',
  'Slovak',
  'Slovenian',
  'Somali',
  'Spanish',
  'Sundanese',
  'Swahili',
  'Swedish',
  'Tajik',
  'Tamil',
  'Tatar',
  'Telugu',
  'Thai',
  'Turkish',
  'Turkmen',
  'Ukrainian',
  'Urdu',
  'Uyghur',
  'Uzbek',
  'Vietnamese',
  'Welsh',
  'Xhosa',
  'Yiddish',
  'Yoruba',
  'Zulu',
];

const SETTINGS_KEY = 'juriverto.settings.v1';
const ARBITER_MODEL_ALIASES = {
  'claude-sonnet-4.6': 'claude-sonnet-4-6',
  'claude-opus-4.6': 'claude-opus-4-6',
};
const ARBITER_MODELS = ['claude-sonnet-4-6', 'claude-opus-4-6'];
const DEFAULT_ARBITER_MODEL = 'claude-opus-4-6';
const ARBITER_FALLBACK_MODES = ['strict_legal', 'balanced'];
const DEFAULT_ARBITER_FALLBACK_MODE = 'strict_legal';
const OPENAI_MODEL_DESCRIPTIONS = {
  'gpt-4.1': 'Great all-around choice, widely available, robust for standard and professional translation.',
  'gpt-4o': 'Better nuance and idiomatic handling, especially with creative or context-heavy text.',
  'gpt-5': 'State-of-the-art where available, especially for rare languages or technical translations.',
};
const DEFAULT_SHOW_TRACE = String(import.meta.env.VITE_SHOW_TRACE || '').trim() === '1';

function normalizeArbiterModel(modelName) {
  const next = String(modelName || '').trim();
  return ARBITER_MODEL_ALIASES[next] || next;
}

function normalizeArbiterFallbackMode(modeName) {
  const next = String(modeName || '').trim().toLowerCase();
  return ARBITER_FALLBACK_MODES.includes(next) ? next : DEFAULT_ARBITER_FALLBACK_MODE;
}

function sanitizeOutputHtml(rawHtml) {
  if (!rawHtml) return '';
  if (typeof window === 'undefined' || typeof DOMParser === 'undefined') return rawHtml;
  const parser = new DOMParser();
  const doc = parser.parseFromString(rawHtml, 'text/html');
  const allowedTags = new Set([
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'div', 'p', 'span', 'br', 'b', 'strong', 'i', 'em', 'u', 'ol', 'ul', 'li', 'sup', 'sub',
  ]);
  const allowedAttrs = new Set(['colspan', 'rowspan', 'scope', 'title']);
  const queue = [...doc.body.querySelectorAll('*')];
  queue.forEach((el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'script' || tag === 'style' || tag === 'iframe') {
      el.remove();
      return;
    }
    if (!allowedTags.has(tag)) {
      const textNode = doc.createTextNode(el.textContent || '');
      el.replaceWith(textNode);
      return;
    }
    [...el.attributes].forEach((attr) => {
      const attrName = attr.name.toLowerCase();
      if (attrName.startsWith('on') || !allowedAttrs.has(attrName)) {
        el.removeAttribute(attr.name);
      }
    });
  });
  return doc.body.innerHTML;
}

function sanitizeInputHtml(rawHtml) {
  if (!rawHtml) return '';
  if (typeof window === 'undefined' || typeof DOMParser === 'undefined') return rawHtml;
  const parser = new DOMParser();
  const doc = parser.parseFromString(rawHtml, 'text/html');
  const allowedTags = new Set([
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'div', 'p', 'span', 'br', 'b', 'strong', 'i', 'em', 'u', 'ul', 'ol', 'li',
  ]);
  const allowedAttrs = new Set(['colspan', 'rowspan', 'scope', 'title']);
  const queue = [...doc.body.querySelectorAll('*')];
  queue.forEach((el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'script' || tag === 'style' || tag === 'iframe' || tag === 'object' || tag === 'embed') {
      el.remove();
      return;
    }
    if (!allowedTags.has(tag)) {
      const textNode = doc.createTextNode(el.textContent || '');
      el.replaceWith(textNode);
      return;
    }
    [...el.attributes].forEach((attr) => {
      const attrName = attr.name.toLowerCase();
      if (attrName.startsWith('on') || !allowedAttrs.has(attrName)) {
        el.removeAttribute(attr.name);
      }
    });
  });
  return doc.body.innerHTML;
}

function extractPlainTextFromHtml(rawHtml) {
  if (!rawHtml) return '';
  if (typeof window === 'undefined' || typeof DOMParser === 'undefined') {
    return String(rawHtml).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  }
  const parser = new DOMParser();
  const doc = parser.parseFromString(rawHtml, 'text/html');
  return (doc.body.textContent || '').replace(/\u00a0/g, ' ').trim();
}

function hasHtmlTable(rawHtml) {
  return /<\s*table\b/i.test(String(rawHtml || ''));
}

function hasRenderableOutputHtml(rawHtml) {
  return /<\s*(table|thead|tbody|tfoot|tr|th|td|div|p|span|br|b|strong|u|em|i|ol|ul|li|sup|sub)\b/i.test(String(rawHtml || ''));
}

function LanguagePicker({ value, onChange, side = 'left' }) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef(null);

  const filteredLanguages = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return LANGUAGES;
    return LANGUAGES.filter((lang) => lang.toLowerCase().includes(q));
  }, [query]);

  useEffect(() => {
    if (!isOpen) return undefined;
    function handlePointerDown(event) {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) setQuery('');
  }, [isOpen]);

  return (
    <div className={`lang-picker ${side}`} ref={rootRef}>
      <button
        type="button"
        className={`lang-select ${side}`}
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
      >
        <span>{value}</span>
        <span className="lang-caret">▾</span>
      </button>
      {isOpen ? (
        <div className={`lang-panel ${side}`}>
          <input
            className="lang-search"
            placeholder="Search language..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          <div className="lang-list-meta">{filteredLanguages.length} / {LANGUAGES.length} languages</div>
          <div className="lang-list" role="listbox">
            {filteredLanguages.map((lang) => (
              <button
                key={lang}
                type="button"
                className={`lang-option ${lang === value ? 'selected' : ''}`}
                onClick={() => {
                  onChange(lang);
                  setIsOpen(false);
                }}
              >
                {lang}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function App({
  showTopBar = true,
  showSettingsButton = true,
  showTrace = DEFAULT_SHOW_TRACE,
} = {}) {
  const [sourceText, setSourceText] = useState('');
  const [sourceHtml, setSourceHtml] = useState('');
  const [sourceLang, setSourceLang] = useState('English');
  const [targetLang, setTargetLang] = useState('Spanish');
  const [domain, setDomain] = useState('legal');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [detectText, setDetectText] = useState('');

  const [providerCatalog, setProviderCatalog] = useState([]);
  const [selectedProvider, setSelectedProvider] = useState('openai');
  const [selectedModel, setSelectedModel] = useState('');
  const [fallbackProvider, setFallbackProvider] = useState('deepl');
  const [fallbackModel, setFallbackModel] = useState('');
  const [manualProviderChoice, setManualProviderChoice] = useState('one');

  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [providerApiKeys, setProviderApiKeys] = useState({});

  const [arbiterEnabled, setArbiterEnabled] = useState(false);
  const [arbiterModel, setArbiterModel] = useState(DEFAULT_ARBITER_MODEL);
  const [arbiterApiKey, setArbiterApiKey] = useState('');
  const [arbiterFallbackMode, setArbiterFallbackMode] = useState(DEFAULT_ARBITER_FALLBACK_MODE);

  const [settingsProvider, setSettingsProvider] = useState('openai');
  const [settingsApiKey, setSettingsApiKey] = useState('');
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsStatus, setSettingsStatus] = useState({ type: '', text: '' });

  const [arbiterDraftEnabled, setArbiterDraftEnabled] = useState(false);
  const [arbiterDraftModel, setArbiterDraftModel] = useState(DEFAULT_ARBITER_MODEL);
  const [arbiterDraftApiKey, setArbiterDraftApiKey] = useState('');
  const [arbiterDraftFallbackMode, setArbiterDraftFallbackMode] = useState(DEFAULT_ARBITER_FALLBACK_MODE);
  const [arbiterBusy, setArbiterBusy] = useState(false);
  const [arbiterStatus, setArbiterStatus] = useState({ type: '', text: '' });
  const inputEditorRef = useRef(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const nextProviderKeys = parsed.providerApiKeys || {};
      if (!parsed.providerApiKeys && parsed.modelApiKeys) {
        for (const [modelName, key] of Object.entries(parsed.modelApiKeys)) {
          if (typeof key !== 'string' || !key.trim()) continue;
          if (modelName.startsWith('gpt-')) nextProviderKeys.openai = key;
          if (modelName.startsWith('deepl-')) nextProviderKeys.deepl = key;
          if (modelName.startsWith('claude-')) nextProviderKeys.anthropic = key;
        }
      }
      setProviderApiKeys(nextProviderKeys);
      setArbiterEnabled(Boolean(parsed.arbiterEnabled));
      const restoredArbiterModel = normalizeArbiterModel(parsed.arbiterModel || DEFAULT_ARBITER_MODEL);
      setArbiterModel(ARBITER_MODELS.includes(restoredArbiterModel) ? restoredArbiterModel : DEFAULT_ARBITER_MODEL);
      setArbiterApiKey(parsed.arbiterApiKey || '');
      setArbiterFallbackMode(normalizeArbiterFallbackMode(parsed.arbiterFallbackMode || DEFAULT_ARBITER_FALLBACK_MODE));
    } catch {
      // ignore invalid settings
    }
  }, []);

  useEffect(() => {
    async function loadCatalog() {
      try {
        const data = await getProviderCatalog();
        const providers = data.providers || [];
        setProviderCatalog(providers);

        const providerOne = providers.find((p) => p.id === 'openai')?.id || data.defaultPrimary || providers[0]?.id || 'openai';
        const providerTwo = providers.find((p) => p.id === 'deepl')?.id || data.defaultFallback || providerOne;
        const primaryModels = providers.find((p) => p.id === providerOne)?.models || [];
        const providerTwoModels = providers.find((p) => p.id === providerTwo)?.models || [];

        setSelectedProvider(providerOne);
        setSelectedModel(primaryModels[0] || '');
        setFallbackProvider(providerTwo);
        setFallbackModel(providerTwoModels[0] || '');
      } catch {
        setError('Could not load provider/model options.');
      }
    }
    loadCatalog();
  }, []);

  const modelsForSelectedProvider = useMemo(
    () => providerCatalog.find((p) => p.id === selectedProvider)?.models || [],
    [providerCatalog, selectedProvider],
  );
  const modelsForSettingsProvider = useMemo(
    () => providerCatalog.find((p) => p.id === settingsProvider)?.models || [],
    [providerCatalog, settingsProvider],
  );

  useEffect(() => {
    if (!modelsForSelectedProvider.includes(selectedModel)) {
      setSelectedModel(modelsForSelectedProvider[0] || '');
    }
  }, [modelsForSelectedProvider, selectedModel]);

  const modelsForFallbackProvider = useMemo(
    () => providerCatalog.find((p) => p.id === fallbackProvider)?.models || [],
    [providerCatalog, fallbackProvider],
  );

  useEffect(() => {
    if (!modelsForFallbackProvider.includes(fallbackModel)) {
      setFallbackModel(modelsForFallbackProvider[0] || '');
    }
  }, [modelsForFallbackProvider, fallbackModel]);

  useEffect(() => {
    if (!isSettingsOpen) return;
    const defaultProvider = selectedProvider || providerCatalog[0]?.id || 'openai';
    setSettingsProvider(defaultProvider);
    setSettingsApiKey(providerApiKeys[defaultProvider] || '');
    setArbiterDraftEnabled(arbiterEnabled);
    setArbiterDraftModel(arbiterModel);
    setArbiterDraftApiKey(arbiterApiKey);
    setArbiterDraftFallbackMode(arbiterFallbackMode);
    setSettingsStatus({ type: '', text: '' });
    setArbiterStatus({ type: '', text: '' });
  }, [isSettingsOpen, providerCatalog, selectedProvider, providerApiKeys, arbiterEnabled, arbiterModel, arbiterApiKey, arbiterFallbackMode]);

  useEffect(() => {
    if (!isSettingsOpen) return;
    setSettingsApiKey(providerApiKeys[settingsProvider] || '');
  }, [settingsProvider, isSettingsOpen, providerApiKeys]);

  const providerOneHasKey = Boolean(providerApiKeys[selectedProvider]?.trim());
  const providerTwoHasKey = Boolean(providerApiKeys[fallbackProvider]?.trim());
  const selectedProviderHasKey = manualProviderChoice === 'one' ? providerOneHasKey : providerTwoHasKey;
  const arbiterMissingKey = arbiterEnabled && (!providerOneHasKey || !providerTwoHasKey);
  const providerOneActive = arbiterEnabled || manualProviderChoice === 'one';
  const providerTwoActive = arbiterEnabled || manualProviderChoice === 'two';
  const providerTwoTriggered = arbiterEnabled || manualProviderChoice === 'two' || Boolean(result?.providerSummary?.fallbackUsed);
  const outputText = result?.finalText || '';
  const translatedRawText = result?.translation || '';
  const outputTableFromApi = result?.tableOutputHtml || '';
  const displayOutputText = hasHtmlTable(translatedRawText) && !hasHtmlTable(outputText)
    ? translatedRawText
    : outputText;
  const outputHtmlFromApi = outputTableFromApi || '';
  const outputHtmlCandidate = outputHtmlFromApi || displayOutputText;
  const outputHasRichHtml = hasRenderableOutputHtml(outputHtmlCandidate);
  const outputRenderedHtml = useMemo(
    () => {
      if (!outputHasRichHtml) return '';
      return sanitizeOutputHtml(outputHtmlCandidate);
    },
    [outputHasRichHtml, outputHtmlCandidate],
  );
  const traceRows = useMemo(() => result?.trace || [], [result]);
  const visibleTraceRows = useMemo(
    () => traceRows.filter((row) => row.step !== 'proofread' && row.step !== 'checks'),
    [traceRows],
  );
  const inlineNotice = error || (
    arbiterEnabled
      ? ((!providerOneHasKey || !providerTwoHasKey) ? 'Arbiter ON requires API keys for both Provider One and Provider Two.' : detectText)
      : (!selectedProviderHasKey ? 'Missing API key for selected provider. Open Settings.' : detectText)
  );
  const noticeKind = (error || arbiterMissingKey || (!arbiterEnabled && !selectedProviderHasKey)) ? 'error' : 'info';
  const sourceChars = sourceText.length;
  const sourceWords = sourceText.trim() ? sourceText.trim().split(/\s+/).length : 0;
  const outputChars = displayOutputText.length;
  const outputWords = displayOutputText.trim() ? displayOutputText.trim().split(/\s+/).length : 0;
  const chosenProvider = arbiterEnabled
    ? selectedProvider
    : (manualProviderChoice === 'one' ? selectedProvider : fallbackProvider);
  const chosenModel = arbiterEnabled
    ? selectedModel
    : (manualProviderChoice === 'one' ? selectedModel : fallbackModel);
  const effectiveProvider = result?.providerSummary?.used || chosenProvider;
  const effectiveModel = result?.providerSummary?.usedModel || chosenModel;
  const arbiterConfidence = typeof result?.providerSummary?.arbiterConfidence === 'number'
    ? Math.round(result.providerSummary.arbiterConfidence * 100)
    : null;
  const hasResult = Boolean(result);
  const fallbackAttempted = traceRows.some((row) => row.step === 'translate_fallback' && row.status === 'success');
  const fallbackFailed = traceRows.some((row) => row.step === 'translate_fallback' && row.status === 'failed');
  const arbiterTrace = [...traceRows].reverse().find((row) => row.step === 'arbiter_judge');
  const arbiterMessage = arbiterTrace?.message || '';
  const outputArbiterStatus = hasResult
    ? (result?.providerSummary?.arbiterUsed ? 'applied' : (arbiterEnabled ? 'requested, not applied' : 'off'))
    : (arbiterEnabled ? 'ready (on)' : 'ready (off)');
  const outputMetaLine = hasResult
    ? `Final: ${effectiveProvider} (${effectiveModel || '-'}) · Arbiter: ${outputArbiterStatus}`
    : `Ready: ${chosenProvider} (${chosenModel || '-'}) · Arbiter: ${outputArbiterStatus}`;
  const outputExecutionLine = !hasResult
    ? 'Execution path appears after translation.'
    : (
      result?.providerSummary?.arbiterUsed
        ? (
          result?.providerSummary?.arbiterFallbackUsed
            ? `Providers engaged: ${selectedProvider} + ${fallbackProvider} + arbiter. Arbiter fallback (${result?.providerSummary?.arbiterFallbackMode || arbiterFallbackMode}) selected candidate ${result?.providerSummary?.arbiterWinner || '-'}${arbiterConfidence !== null ? ` (${arbiterConfidence}% confidence)` : ''}.`
            : `Providers engaged: ${selectedProvider} + ${fallbackProvider} + arbiter. Arbiter ${arbiterModel} selected candidate ${result?.providerSummary?.arbiterWinner || '-'}${arbiterConfidence !== null ? ` (${arbiterConfidence}% confidence)` : ''}.`
        )
        : (
          arbiterEnabled
            ? `Arbiter requested but not applied. Final result from ${effectiveProvider} (${effectiveModel || '-'}).${fallbackAttempted ? ` ${fallbackProvider} candidate was generated.` : ''}${fallbackFailed ? ` ${fallbackProvider} fallback failed.` : ''}${arbiterMessage ? ` Reason: ${arbiterMessage}` : ''}`
            : `Executed by ${effectiveProvider} (${effectiveModel || '-'}).`
        )
    );

  function syncSourceFromEditor() {
    const editor = inputEditorRef.current;
    if (!editor) return;
    const sanitizedHtml = sanitizeInputHtml(editor.innerHTML);
    if (sanitizedHtml !== editor.innerHTML) editor.innerHTML = sanitizedHtml;
    setSourceHtml(sanitizedHtml);
    setSourceText((editor.textContent || '').replace(/\u00a0/g, ' ').trim());
  }

  function setEditorContent(rawHtml, plainText) {
    const editor = inputEditorRef.current;
    const normalizedHtml = sanitizeInputHtml(rawHtml || '');
    if (editor) {
      if (normalizedHtml) editor.innerHTML = normalizedHtml;
      else editor.textContent = plainText || '';
    }
    setSourceHtml(normalizedHtml);
    setSourceText((plainText ?? extractPlainTextFromHtml(normalizedHtml)).trim());
  }

  function handleSourcePaste() {
    setTimeout(() => syncSourceFromEditor(), 0);
  }

  useEffect(() => {
    const editor = inputEditorRef.current;
    if (!editor) return;
    const currentHtml = editor.innerHTML;
    if (!sourceHtml && !sourceText) {
      if (currentHtml) editor.innerHTML = '';
      return;
    }
    if (sourceHtml && currentHtml !== sourceHtml) {
      editor.innerHTML = sourceHtml;
      return;
    }
    if (!sourceHtml && editor.textContent !== sourceText) {
      editor.textContent = sourceText;
    }
  }, [sourceHtml, sourceText]);

  function persistSettings(nextProviderApiKeys, nextArbiterEnabled, nextArbiterModel, nextArbiterApiKey, nextArbiterFallbackMode) {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        providerApiKeys: nextProviderApiKeys,
        arbiterEnabled: nextArbiterEnabled,
        arbiterModel: nextArbiterModel,
        arbiterApiKey: nextArbiterApiKey,
        arbiterFallbackMode: nextArbiterFallbackMode,
      }),
    );
  }

  async function saveAndVerifyProviderKey() {
    const key = settingsApiKey.trim();
    if (!settingsProvider) {
      setSettingsStatus({ type: 'error', text: 'Select API provider first.' });
      return;
    }
    if (!key) {
      setSettingsStatus({ type: 'error', text: 'Enter API key before saving.' });
      return;
    }

    setSettingsBusy(true);
    setSettingsStatus({ type: '', text: '' });
    try {
      const nextKeys = { ...providerApiKeys, [settingsProvider]: key };
      setProviderApiKeys(nextKeys);
      persistSettings(nextKeys, arbiterEnabled, arbiterModel, arbiterApiKey, arbiterFallbackMode);

      const validationModel = modelsForSettingsProvider[0];
      if (!validationModel) {
        setSettingsStatus({ type: 'error', text: `No models available for provider ${settingsProvider}.` });
        return;
      }

      const check = await validateApiKey({
        provider: settingsProvider,
        model: validationModel,
        apiKey: key,
      });

      if (check.ok) setSettingsStatus({ type: 'ok', text: `Saved and verified: ${check.message}` });
      else setSettingsStatus({ type: 'error', text: `Saved, but verification failed: ${check.message}` });
    } catch (e) {
      setSettingsStatus({ type: 'error', text: `Save failed: ${e.message || 'Unknown error'}` });
    } finally {
      setSettingsBusy(false);
    }
  }

  async function saveAndVerifyArbiter() {
    const key = arbiterDraftApiKey.trim();
    if (!key) {
      setArbiterStatus({ type: 'error', text: 'Enter arbiter API key before saving.' });
      return;
    }

    setArbiterBusy(true);
    setArbiterStatus({ type: '', text: '' });
    try {
      const check = await validateApiKey({
        provider: 'anthropic',
        model: arbiterDraftModel,
        apiKey: key,
      });

      setArbiterEnabled(arbiterDraftEnabled);
      setArbiterModel(arbiterDraftModel);
      setArbiterApiKey(key);
      const normalizedDraftMode = normalizeArbiterFallbackMode(arbiterDraftFallbackMode);
      setArbiterFallbackMode(normalizedDraftMode);
      const nextKeys = { ...providerApiKeys, anthropic: key };
      setProviderApiKeys(nextKeys);
      persistSettings(nextKeys, arbiterDraftEnabled, arbiterDraftModel, key, normalizedDraftMode);

      if (check.ok) setArbiterStatus({ type: 'ok', text: `Saved and verified: ${check.message}` });
      else setArbiterStatus({ type: 'error', text: `Saved, but verification failed: ${check.message}` });
    } catch (e) {
      setArbiterStatus({ type: 'error', text: `Save failed: ${e.message || 'Unknown error'}` });
    } finally {
      setArbiterBusy(false);
    }
  }

  async function onTranslate() {
    const editor = inputEditorRef.current;
    const liveHtml = editor ? sanitizeInputHtml(editor.innerHTML) : sourceHtml;
    const liveText = editor
      ? (editor.textContent || '').replace(/\u00a0/g, ' ').trim()
      : sourceText.trim();
    if (editor && liveHtml !== editor.innerHTML) {
      editor.innerHTML = liveHtml;
    }
    setSourceHtml(liveHtml);
    setSourceText(liveText);

    if (!liveText && !hasHtmlTable(liveHtml)) return;
    if (sourceLang === targetLang) {
      setError('Source and target must be different.');
      return;
    }
    if (arbiterEnabled && (!providerOneHasKey || !providerTwoHasKey)) {
      setError('Arbiter ON requires API keys for both Provider One and Provider Two.');
      return;
    }
    if (!arbiterEnabled && !selectedProviderHasKey) {
      const chosenProviderName = manualProviderChoice === 'one' ? selectedProvider : fallbackProvider;
      setError(`Missing API key for provider: ${chosenProviderName}`);
      return;
    }

    setLoading(true);
    setError('');
    setDetectText('');
    setResult(null);
    try {
      const sourcePayload = hasHtmlTable(liveHtml) ? liveHtml : liveText;
      const requestedProvider = arbiterEnabled
        ? selectedProvider
        : (manualProviderChoice === 'one' ? selectedProvider : fallbackProvider);
      const requestedModel = arbiterEnabled
        ? selectedModel
        : (manualProviderChoice === 'one' ? selectedModel : fallbackModel);
      // When arbiter is OFF, set fallback provider to same chosen provider to avoid engaging the second one.
      const requestedFallbackProvider = arbiterEnabled
        ? fallbackProvider
        : requestedProvider;

      const data = await translateText({
        sourceText: sourcePayload,
        sourceLang,
        targetLang,
        domain,
        strictness: 'strict',
        selectedProvider: requestedProvider,
        selectedModel: requestedModel,
        fallbackProvider: requestedFallbackProvider,
        providerApiKeys,
        arbiter: {
          enabled: arbiterEnabled,
          provider: 'anthropic',
          model: arbiterModel,
          apiKey: arbiterApiKey || providerApiKeys.anthropic || '',
          fallbackMode: arbiterFallbackMode,
        },
        debug: showTrace,
      });
      setResult(data);
    } catch (e) {
      setError(e.message || 'Request failed');
    } finally {
      setLoading(false);
    }
  }

  function swapLanguages() {
    const nextSource = outputHasRichHtml ? outputRenderedHtml : (displayOutputText || sourceText);
    setSourceLang(targetLang);
    setTargetLang(sourceLang);
    if (outputHasRichHtml) {
      setEditorContent(nextSource, extractPlainTextFromHtml(nextSource));
    } else {
      setEditorContent('', nextSource);
    }
    setResult(null);
  }

  function clearAll() {
    setEditorContent('', '');
    setResult(null);
    setError('');
    setDetectText('');
  }

  function detectSourceLanguage() {
    if (!sourceText.trim()) return;
    const text = sourceText.trim();
    const containsArabic = /[\u0600-\u06FF]/.test(text);
    const containsCJK = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]/.test(text);
    const guess = containsArabic ? 'Arabic' : containsCJK ? 'Chinese' : 'English';
    setSourceLang(guess);
    setDetectText(`Detected: ${guess}`);
    setTimeout(() => setDetectText(''), 2200);
  }

  function copyOutput() {
    if (!displayOutputText) return;
    navigator.clipboard.writeText(displayOutputText);
  }

  function formatTraceDetails(row) {
    const base = row?.message || '-';
    const meta = row?.metadata;
    if (!meta || typeof meta !== 'object') return base;
    const tags = [];
    if (typeof meta.reason === 'string' && meta.reason) tags.push(`reason=${meta.reason}`);
    if (typeof meta.sourceContainsHtmlTable === 'boolean') tags.push(`sourceContainsHtmlTable=${meta.sourceContainsHtmlTable}`);
    if (typeof meta.outputContainsHtmlTable === 'boolean') tags.push(`outputContainsHtmlTable=${meta.outputContainsHtmlTable}`);
    return tags.length ? `${base} (${tags.join(', ')})` : base;
  }

  return (
    <div className="app-shell">
      {showTopBar ? (
        <div className="top-strip">
          <div className="brand-logo" aria-label="JuriVerto logo">
            <span className="brand-juri">Juri</span>
            <span className="brand-verto">Verto</span>
          </div>
          {showSettingsButton ? (
            <button className="ghost-btn" onClick={() => setIsSettingsOpen(true)}>Settings</button>
          ) : null}
        </div>
      ) : null}

      <TranslatorPane
        LanguagePicker={LanguagePicker}
        sourceLang={sourceLang}
        setSourceLang={setSourceLang}
        targetLang={targetLang}
        setTargetLang={setTargetLang}
        clearAll={clearAll}
        detectSourceLanguage={detectSourceLanguage}
        copyOutput={copyOutput}
        providerOneActive={providerOneActive}
        providerTwoActive={providerTwoActive}
        arbiterEnabled={arbiterEnabled}
        setManualProviderChoice={setManualProviderChoice}
        selectedProvider={selectedProvider}
        selectedModel={selectedModel}
        fallbackProvider={fallbackProvider}
        fallbackModel={fallbackModel}
        providerTwoTriggered={providerTwoTriggered}
        setArbiterEnabled={setArbiterEnabled}
        arbiterModel={arbiterModel}
        onTranslate={onTranslate}
        loading={loading}
        sourceText={sourceText}
        sourceHtml={sourceHtml}
        hasHtmlTable={hasHtmlTable}
        inlineNotice={inlineNotice}
        noticeKind={noticeKind}
        effectiveProvider={effectiveProvider}
        effectiveModel={effectiveModel}
        outputMetaLine={outputMetaLine}
        outputExecutionLine={outputExecutionLine}
        sourceWords={sourceWords}
        sourceChars={sourceChars}
        inputEditorRef={inputEditorRef}
        syncSourceFromEditor={syncSourceFromEditor}
        handleSourcePaste={handleSourcePaste}
        swapLanguages={swapLanguages}
        outputWords={outputWords}
        outputChars={outputChars}
        outputHasRichHtml={outputHasRichHtml}
        outputRenderedHtml={outputRenderedHtml}
        displayOutputText={displayOutputText}
      />

      <TracePanel
        showTrace={showTrace}
        visibleTraceRows={visibleTraceRows}
        formatTraceDetails={formatTraceDetails}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        settingsProvider={settingsProvider}
        setSettingsProvider={setSettingsProvider}
        providerCatalog={providerCatalog}
        settingsApiKey={settingsApiKey}
        setSettingsApiKey={setSettingsApiKey}
        modelsForSettingsProvider={modelsForSettingsProvider}
        selectedProvider={selectedProvider}
        selectedModel={selectedModel}
        setSelectedProvider={setSelectedProvider}
        setSelectedModel={setSelectedModel}
        settingsStatus={settingsStatus}
        setSettingsStatus={setSettingsStatus}
        settingsBusy={settingsBusy}
        saveAndVerifyProviderKey={saveAndVerifyProviderKey}
        OPENAI_MODEL_DESCRIPTIONS={OPENAI_MODEL_DESCRIPTIONS}
        arbiterDraftEnabled={arbiterDraftEnabled}
        setArbiterDraftEnabled={setArbiterDraftEnabled}
        arbiterDraftModel={arbiterDraftModel}
        setArbiterDraftModel={setArbiterDraftModel}
        arbiterDraftFallbackMode={arbiterDraftFallbackMode}
        setArbiterDraftFallbackMode={setArbiterDraftFallbackMode}
        arbiterDraftApiKey={arbiterDraftApiKey}
        setArbiterDraftApiKey={setArbiterDraftApiKey}
        arbiterBusy={arbiterBusy}
        saveAndVerifyArbiter={saveAndVerifyArbiter}
        arbiterStatus={arbiterStatus}
      />
    </div>
  );
}


