export default function SettingsModal({
  isOpen,
  onClose,
  settingsProvider,
  setSettingsProvider,
  providerCatalog,
  settingsApiKey,
  setSettingsApiKey,
  modelsForSettingsProvider,
  selectedProvider,
  selectedModel,
  setSelectedProvider,
  setSelectedModel,
  settingsStatus,
  setSettingsStatus,
  settingsBusy,
  saveAndVerifyProviderKey,
  OPENAI_MODEL_DESCRIPTIONS,
  arbiterDraftEnabled,
  setArbiterDraftEnabled,
  arbiterDraftModel,
  setArbiterDraftModel,
  arbiterDraftFallbackMode,
  setArbiterDraftFallbackMode,
  arbiterDraftApiKey,
  setArbiterDraftApiKey,
  arbiterBusy,
  saveAndVerifyArbiter,
  arbiterStatus,
}) {
  if (!isOpen) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Settings</h3>
          <button className="close-btn" onClick={onClose}>Close</button>
        </div>

        <div className="modal-section">
          <h4>API Key Per Provider</h4>
          <label className="setting-row">
            <span>API provider</span>
            <select value={settingsProvider} onChange={(e) => setSettingsProvider(e.target.value)}>
              {providerCatalog.map((p) => <option key={p.id} value={p.id}>{p.id}</option>)}
            </select>
          </label>
          <label className="setting-row">
            <span>API key</span>
            <input
              type="password"
              value={settingsApiKey}
              onChange={(e) => setSettingsApiKey(e.target.value)}
              placeholder={`API key for ${settingsProvider} (applies to all its models)`}
            />
          </label>
          {settingsProvider === 'openai' ? (
            <div className="openai-models">
              <div className="openai-models-title">OpenAI Models</div>
              {modelsForSettingsProvider.map((modelName) => (
                <button
                  type="button"
                  key={modelName}
                  className={`openai-model-item selectable ${selectedProvider === 'openai' && selectedModel === modelName ? 'selected' : ''}`}
                  onClick={() => {
                    setSelectedProvider('openai');
                    setSelectedModel(modelName);
                    setSettingsStatus({ type: 'ok', text: `Selected OpenAI model: ${modelName}` });
                  }}
                >
                  <div className="openai-model-name">{modelName}</div>
                  <div className="openai-model-desc">
                    {OPENAI_MODEL_DESCRIPTIONS[modelName] || 'General purpose translation model.'}
                  </div>
                </button>
              ))}
            </div>
          ) : null}
          <div className="setting-actions">
            <button className="main-action small" disabled={settingsBusy} onClick={saveAndVerifyProviderKey}>
              {settingsBusy ? 'Saving...' : 'Save & Verify'}
            </button>
            {settingsStatus.text ? (
              <span className={settingsStatus.type === 'ok' ? 'status-ok' : 'status-error'}>{settingsStatus.text}</span>
            ) : null}
          </div>
        </div>

        <div className="modal-section">
          <h4>Future Arbiter (Claude Opus)</h4>
          <label className="setting-row">
            <span>Enable arbiter</span>
            <input type="checkbox" checked={arbiterDraftEnabled} onChange={(e) => setArbiterDraftEnabled(e.target.checked)} />
          </label>
          <label className="setting-row">
            <span>Arbiter model</span>
            <select value={arbiterDraftModel} onChange={(e) => setArbiterDraftModel(e.target.value)}>
              <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
              <option value="claude-opus-4-6">claude-opus-4-6</option>
            </select>
          </label>
          <label className="setting-row">
            <span>Fallback scoring</span>
            <select value={arbiterDraftFallbackMode} onChange={(e) => setArbiterDraftFallbackMode(e.target.value)}>
              <option value="strict_legal">strict legal</option>
              <option value="balanced">balanced</option>
            </select>
          </label>
          <label className="setting-row">
            <span>Arbiter API key</span>
            <input
              type="password"
              value={arbiterDraftApiKey}
              onChange={(e) => setArbiterDraftApiKey(e.target.value)}
              placeholder="API key for Claude Opus arbiter"
            />
          </label>
          <div className="setting-actions">
            <button className="main-action small" disabled={arbiterBusy} onClick={saveAndVerifyArbiter}>
              {arbiterBusy ? 'Saving...' : 'Save & Verify'}
            </button>
            {arbiterStatus.text ? (
              <span className={arbiterStatus.type === 'ok' ? 'status-ok' : 'status-error'}>{arbiterStatus.text}</span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}


