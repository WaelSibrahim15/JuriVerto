export default function TranslatorPane({
  LanguagePicker,
  sourceLang,
  setSourceLang,
  targetLang,
  setTargetLang,
  clearAll,
  detectSourceLanguage,
  copyOutput,
  providerOneActive,
  providerTwoActive,
  arbiterEnabled,
  setManualProviderChoice,
  selectedProvider,
  selectedModel,
  fallbackProvider,
  fallbackModel,
  providerTwoTriggered,
  setArbiterEnabled,
  arbiterModel,
  onTranslate,
  loading,
  sourceText,
  sourceHtml,
  hasHtmlTable,
  inlineNotice,
  noticeKind,
  effectiveProvider,
  effectiveModel,
  outputMetaLine,
  outputExecutionLine,
  sourceWords,
  sourceChars,
  inputEditorRef,
  syncSourceFromEditor,
  handleSourcePaste,
  swapLanguages,
  outputWords,
  outputChars,
  outputHasRichHtml,
  outputRenderedHtml,
  displayOutputText,
}) {
  return (
    <section className="translator-card">
      <div className="translator-toolbar">
        <div className="lang-col">
          <LanguagePicker value={sourceLang} onChange={setSourceLang} side="left" />
        </div>
        <div className="action-col">
          <button className="small-action" onClick={clearAll}>Clear</button>
          <button className="small-action" onClick={detectSourceLanguage}>Detect</button>
        </div>
        <div className="lang-col right">
          <LanguagePicker value={targetLang} onChange={setTargetLang} side="right" />
          <button className="copy-btn" onClick={copyOutput}>Copy</button>
        </div>
      </div>

      <div className="provider-line">
        <div className="status-strip">
          <button
            className={`status-toggle ${providerOneActive ? 'active' : 'inactive'}`}
            onClick={() => { if (!arbiterEnabled) setManualProviderChoice('one'); }}
            title="Provider One"
          >
            <span className="status-name">Provider One</span>
            <span className="status-value">{selectedProvider} · {selectedModel || '-'}</span>
          </button>
          <button
            className={`status-toggle ${providerTwoActive ? 'active' : 'inactive'}`}
            onClick={() => { if (!arbiterEnabled) setManualProviderChoice('two'); }}
            title="Provider Two"
          >
            <span className="status-name">Provider Two</span>
            <span className="status-value">
              {fallbackProvider}
              {providerTwoTriggered && fallbackModel ? ` · ${fallbackModel}` : ''}
            </span>
          </button>
          <button
            className={`status-toggle ${arbiterEnabled ? 'active' : 'inactive'}`}
            onClick={() => setArbiterEnabled((prev) => !prev)}
            title="Arbiter"
          >
            <span className="status-name">Arbiter</span>
            <span className="status-value">{arbiterModel} · {arbiterEnabled ? 'ON' : 'OFF'}</span>
          </button>
        </div>
      </div>

      <div className="translate-cta-row">
        <button className="translate-cta" onClick={onTranslate} disabled={loading || (!sourceText.trim() && !hasHtmlTable(sourceHtml))}>
          {loading ? 'Translating...' : `Translate ${sourceLang} -> ${targetLang}`}
        </button>
      </div>

      {inlineNotice ? (
        <div className={`inline-notice ${noticeKind}`}>{inlineNotice}</div>
      ) : null}

      <div className="split-area">
        <div className="execution-banner">
          <div className="output-label">{effectiveProvider.toUpperCase()} · {effectiveModel || 'model'}</div>
          <div className="output-meta">
            {outputMetaLine}
          </div>
          <div className="output-path">{outputExecutionLine}</div>
        </div>
        <div className="pane left">
          <div className="pane-head">
            <span className="pane-title">Source Text</span>
            <span className="pane-meta">{sourceWords} words · {sourceChars} chars</span>
          </div>
          <div
            ref={inputEditorRef}
            className="input-editor"
            contentEditable
            suppressContentEditableWarning
            onInput={syncSourceFromEditor}
            onPaste={handleSourcePaste}
            onBlur={syncSourceFromEditor}
            data-placeholder="Type or paste to translate. Supports plain text, markdown tables, TSV/Excel tables, and HTML tables."
          />
          {!sourceText ? (
            <div className="empty-state">
              <p className="empty-title">Type or paste to translate.</p>
              <p className="empty-subtitle">Productivity mode: paste clauses, contracts, legal snippets.</p>
            </div>
          ) : null}
        </div>
        <button className="swap-btn" onClick={swapLanguages} aria-label="Swap languages">⇄</button>
        <div className="pane right">
          <div className="pane-head">
            <span className="pane-title">Translated Text</span>
            <span className="pane-meta">{outputWords} words · {outputChars} chars</span>
          </div>
          {outputHasRichHtml ? (
            <div className="output-rich" dangerouslySetInnerHTML={{ __html: outputRenderedHtml }} />
          ) : (
            <textarea readOnly value={displayOutputText} placeholder="Translation output appears here." />
          )}
          {!displayOutputText ? (
            <div className="empty-state right">
              <p className="empty-title">Translation appears here.</p>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}


