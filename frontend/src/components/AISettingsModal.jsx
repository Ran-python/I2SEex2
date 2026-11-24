import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client.js'
import { useRole } from '../context/RoleContext.jsx'

const PROVIDERS = [
  {
    id: 'gemini',
    label: 'Google Gemini',
    defaultModel: 'gemini-2.5-flash',
    placeholder: 'AIzaSy...',
    hint: 'Supports Vision (Photo Headcount), Video/Transcript Lecture Summaries, and Homework Reviews.',
  },
  {
    id: 'claude',
    label: 'Anthropic Claude',
    defaultModel: 'claude-sonnet-4-20250514',
    placeholder: 'sk-ant-...',
    hint: 'Supports Vision (Photo Headcount), Transcript Lecture Summaries, and Deep Homework Reviews.',
  },
  {
    id: 'openai',
    label: 'OpenAI GPT',
    defaultModel: 'gpt-4o-mini',
    placeholder: 'sk-proj-...',
    hint: 'Supports Vision (Photo Headcount), Transcript Lecture Summaries, and Deep Homework Reviews.',
  },
]

export default function AISettingsModal() {
  const { aiSettings, saveAiSettings, aiSettingsOpen, setAiSettingsOpen } = useRole()
  const [draft, setDraft] = useState(aiSettings)
  const [models, setModels] = useState([])
  const [fetchingModels, setFetchingModels] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null) // { tone, text }

  const fetchAvailableModels = useCallback(
    async (providerToFetch, apiKeyToFetch, currentModel, { forceRefresh = false, silent = false } = {}) => {
      const cleanKey = (apiKeyToFetch || '').trim()
      if (!cleanKey) {
        setModels([])
        return null
      }
      setFetchingModels(true)
      if (!silent) setTestResult(null)
      try {
        const res = await api.get(
          `/api/ai/models?provider=${encodeURIComponent(providerToFetch)}&refresh=${forceRefresh ? 'true' : 'false'}`,
          {
            headers: {
              'X-AI-Provider': providerToFetch,
              'X-AI-Api-Key': cleanKey,
            },
          },
        )
        const list = Array.isArray(res.models) ? res.models : []
        setModels(list)

        const chosenModel =
          currentModel && list.some((m) => m.id === currentModel)
            ? currentModel
            : res.default_model || list[0]?.id || ''

        if (chosenModel) {
          setDraft((prev) => {
            const updated = { ...prev, provider: providerToFetch, apiKey: cleanKey, model: chosenModel }
            saveAiSettings(updated)
            return updated
          })
        }
        if (!silent && list.length > 0) {
          setTestResult({
            tone: 'success',
            text: `✅ Loaded ${list.length} available models from ${providerToFetch.toUpperCase()}. Active model: ${chosenModel}`,
          })
        }
        return chosenModel
      } catch (err) {
        setModels([])
        if (!silent) {
          setTestResult({
            tone: 'danger',
            text: `❌ Could not fetch models: ${err.message}`,
          })
        }
        return null
      } finally {
        setFetchingModels(false)
      }
    },
    [saveAiSettings],
  )

  useEffect(() => {
    if (aiSettingsOpen) {
      setDraft(aiSettings)
      setTestResult(null)
      if (aiSettings.apiKey) {
        fetchAvailableModels(aiSettings.provider, aiSettings.apiKey, aiSettings.model, { silent: true })
      } else {
        setModels([])
      }
    }
  }, [aiSettingsOpen])

  if (!aiSettingsOpen) return null

  const activeProvider = PROVIDERS.find((p) => p.id === draft.provider) || PROVIDERS[0]

  const handleSave = async (event) => {
    event.preventDefault()
    let modelToSave = draft.model
    if (draft.apiKey && (!modelToSave || models.length === 0)) {
      const resolved = await fetchAvailableModels(draft.provider, draft.apiKey, draft.model, { silent: true })
      if (resolved) modelToSave = resolved
    }
    saveAiSettings({
      ...draft,
      model: modelToSave || activeProvider.defaultModel,
    })
    setAiSettingsOpen(false)
  }

  const handleTest = async () => {
    let modelToTest = draft.model
    if (draft.apiKey && models.length === 0) {
      const resolved = await fetchAvailableModels(draft.provider, draft.apiKey, draft.model, { silent: true })
      if (resolved) modelToTest = resolved
    }
    const nextSettings = {
      ...draft,
      model: modelToTest || activeProvider.defaultModel,
    }
    saveAiSettings(nextSettings)
    setDraft(nextSettings)
    setTesting(true)
    setTestResult(null)
    try {
      const res = await api.post('/api/ai/test', {
        provider: nextSettings.provider,
        api_key: nextSettings.apiKey,
        model: nextSettings.model || null,
      })
      setTestResult({
        tone: 'success',
        text: `✅ Connected to ${res.provider.toUpperCase()} (model: ${res.model}) — Response: "${res.reply}"`,
      })
    } catch (err) {
      setTestResult({
        tone: 'danger',
        text: `❌ Connection failed: ${err.message}`,
      })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={() => setAiSettingsOpen(false)}>
      <div className="modal-card modal-card--wide card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="card__header">
          <div>
            <h2>⚙️ Multi-Provider AI Settings</h2>
            <p className="muted">
              Configure your LLM provider, API key, and model for Classroom Photo Headcount, Lecture Video Summary, and Deep Homework Review.
            </p>
          </div>
          <button
            type="button"
            className="notice__close"
            onClick={() => setAiSettingsOpen(false)}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <form className="modal-form" onSubmit={handleSave}>
          <div className="provider-tabs" role="radiogroup" aria-label="AI Provider">
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`provider-tab ${draft.provider === p.id ? 'is-active' : ''}`}
                onClick={() => {
                  const nextKey = draft.provider === p.id ? draft.apiKey : ''
                  setDraft((d) => ({ ...d, provider: p.id, model: p.defaultModel }))
                  setModels([])
                  setTestResult(null)
                  if (nextKey) {
                    fetchAvailableModels(p.id, nextKey, p.defaultModel, { silent: true })
                  }
                }}
              >
                <strong>{p.label}</strong>
                <small>{p.defaultModel}</small>
              </button>
            ))}
          </div>

          <p className="muted">{activeProvider.hint}</p>

          <div className="field">
            <span>{activeProvider.label} API Key</span>
            <div className="context-group__controls" style={{ display: 'flex', gap: '0.5rem' }}>
              <input
                type="password"
                style={{ flex: 1 }}
                placeholder={activeProvider.placeholder}
                value={draft.apiKey}
                onChange={(e) => setDraft((d) => ({ ...d, apiKey: e.target.value }))}
                onBlur={() => {
                  if (draft.apiKey.trim() && models.length === 0) {
                    fetchAvailableModels(draft.provider, draft.apiKey, draft.model, { silent: false })
                  }
                }}
                autoComplete="off"
              />
              <button
                type="button"
                className="btn btn--secondary"
                disabled={!draft.apiKey.trim() || fetchingModels}
                onClick={() =>
                  fetchAvailableModels(draft.provider, draft.apiKey, draft.model, {
                    forceRefresh: true,
                    silent: false,
                  })
                }
              >
                {fetchingModels ? '⏳ Fetching…' : '🔄 Fetch / Refresh Models'}
              </button>
            </div>
          </div>

          <label className="field">
            <span>
              AI Model{' '}
              <span className="optional">
                {models.length > 0
                  ? `(${models.length} live models fetched from ${activeProvider.label})`
                  : `(click "Fetch / Refresh Models" after entering your API key)`}
              </span>
            </span>
            <select
              className="select-input"
              value={draft.model || activeProvider.defaultModel}
              onChange={(e) => {
                const nextModel = e.target.value
                setDraft((d) => {
                  const updated = { ...d, model: nextModel }
                  saveAiSettings(updated)
                  return updated
                })
              }}
            >
              {models.length > 0 ? (
                models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))
              ) : (
                <option value={draft.model || activeProvider.defaultModel}>
                  {draft.model || activeProvider.defaultModel} (Default)
                </option>
              )}
            </select>
          </label>

          {testResult && (
            <div className={`notice notice--${testResult.tone}`} role="status">
              {testResult.text}
            </div>
          )}

          <div className="modal-actions">
            <button
              type="button"
              className="btn btn--secondary"
              onClick={handleTest}
              disabled={testing || fetchingModels}
            >
              {testing ? '⏳ Testing…' : '🔌 Test Connection'}
            </button>
            <div className="modal-actions__right">
              <button type="button" className="btn btn--ghost" onClick={() => setAiSettingsOpen(false)}>
                Close
              </button>
              <button type="submit" className="btn btn--primary">
                Save Settings
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
