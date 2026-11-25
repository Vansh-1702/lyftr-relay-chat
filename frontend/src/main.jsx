import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const API = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const suggestions = [
  'Explain response caching in two sentences.',
  'Give me three tips for a technical interview.',
  'Explain why APIs return HTTP 429.',
];

function newSession() {
  // getRandomValues also works when testing Docker over a local HTTP address.
  return Array.from(crypto.getRandomValues(new Uint8Array(18)), n => n.toString(16).padStart(2, '0')).join('');
}

function readableError(body, status) {
  if (status === 503) return 'All providers are unavailable right now. This message was not saved; please try again.';
  if (Array.isArray(body?.detail)) return body.detail.map(item => item.msg).join(' ') || 'Check your message and try again.';
  return typeof body?.detail === 'string' ? body.detail : 'The request failed. Please try again.';
}

function Meta({ metadata: m }) {
  return <dl className="metrics">
    <div><dt>Source</dt><dd className={m.resolution_source === 'LLM_CACHE' ? 'cached' : ''} data-testid="meta-source">{m.resolution_source}</dd></div>
    <div><dt>Model</dt><dd data-testid="meta-model">{m.model}</dd></div>
    <div><dt>Latency</dt><dd data-testid="meta-latency">{m.latency_ms} ms</dd></div>
    <div><dt>Input tokens</dt><dd data-testid="meta-input-tokens">{m.input_tokens}</dd></div>
    <div><dt>Output tokens</dt><dd data-testid="meta-output-tokens">{m.output_tokens}</dd></div>
    <div><dt>Key / failovers</dt><dd>{m.key_id ?? 'None'} / {m.fallback_attempts}</dd></div>
  </dl>;
}

function App() {
  const [sessionId, setSessionId] = useState(newSession);
  const [items, setItems] = useState([]);
  const [input, setInput] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState('Checking API');
  const [online, setOnline] = useState(false);
  const sending = useRef(false);
  const bottom = useRef(null);
  const messageInput = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(API + '/health', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw Error('health check failed');
      const body = await response.json();
      setOnline(body.status === 'ok');
      setHealth(body.status === 'ok' ? 'API online' : 'API unavailable');
    }).catch(error => {
      if (error.name !== 'AbortError') {
        setOnline(false);
        setHealth('API unavailable');
      }
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (items.length) bottom.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [items, busy]);

  async function send(value) {
    const message = value.trim();
    if (!message || sending.current || message.length > 4000) return;
    sending.current = true;
    setInput('');
    setBusy(true);
    setItems(old => [...old, { role: 'user', content: message }]);
    try {
      const prompt = systemPrompt.trim();
      const response = await fetch(API + '/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message, ...(prompt && { system_prompt: prompt }) }),
        signal: AbortSignal.timeout(120000),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok || !body?.metadata || typeof body.reply !== 'string') {
        throw Error(readableError(body, response.status));
      }
      setItems(old => [...old, { role: 'assistant', content: body.reply, metadata: body.metadata }]);
      setOnline(true);
      setHealth('API online');
    } catch (error) {
      const content = error.name === 'TimeoutError' ? 'The request timed out. Please try again.' :
        error instanceof TypeError ? 'Could not reach the backend. Check the connection and try again.' : error.message;
      setItems(old => [...old, { role: 'error', content }]);
    } finally {
      sending.current = false;
      setBusy(false);
      messageInput.current?.focus();
    }
  }

  function reset() {
    if (sending.current) return;
    setSessionId(newSession());
    setItems([]);
    setSystemPrompt('');
    setInput('');
    messageInput.current?.focus();
  }

  return <>
    <header className="topbar">
      <a href="/" className="brand" aria-label="Relay home"><span className="mark" aria-hidden="true">↗</span><span>Relay<small>RESILIENT AI CHAT</small></span></a>
      <div className="actions"><span className="health" role="status"><i className={online ? 'on' : ''}></i>{health}</span><button type="button" className="new" onClick={reset} disabled={busy}>+ New chat</button></div>
    </header>
    <main className="layout">
      <aside className="sidebar">
        <p className="eyebrow">WORKSPACE</p>
        <h2>Session controls</h2>
        <label htmlFor="system-prompt">System prompt</label>
        <textarea id="system-prompt" data-testid="system-prompt-input" rows="4" maxLength={4000} value={systemPrompt}
          onChange={event => setSystemPrompt(event.target.value)} placeholder="Optional. Give the assistant a role or response style." />
        <p className="annotation">An override applies when you send your next message. Start a new chat to reset it.</p>
        <hr />
        <p className="annotation"><strong>Session ID</strong></p><code title={sessionId}>{sessionId}</code>
        <p className="annotation privacy">Public demo. Don’t enter passwords, keys, or personal information.</p>
      </aside>
      <section className="chat" aria-label="Conversation">
        <div className="conversation" aria-live="polite" aria-relevant="additions">
          {!items.length && <div className="intro">
            <p className="eyebrow">READY TO CHAT</p>
            <h1>What’s on your mind?</h1>
            <p>Ask a question, then follow up naturally. Every reply shows whether it came from the model or the cache.</p>
            <div className="suggestions">{suggestions.map(text => <button key={text} type="button" onClick={() => send(text)} disabled={busy}>{text}<span aria-hidden="true">↗</span></button>)}</div>
          </div>}
          {items.map((item, index) => item.role === 'error' ?
            <div key={index} className="error" role="alert">{item.content}</div> :
            <article key={index} className={'message ' + item.role} data-testid={item.role === 'assistant' ? 'assistant-message' : undefined}>
              <h3>{item.role === 'user' ? 'You' : 'Relay'}</h3>
              <div className="reply">{item.content}</div>
              {item.metadata && <Meta metadata={item.metadata} />}
            </article>)}
          {busy && <p className="pending" role="status">Relay is working on your reply…</p>}
          <div ref={bottom} />
        </div>
        <form className="composer" onSubmit={event => { event.preventDefault(); send(input); }}>
          <label className="sr-only" htmlFor="chat-input">Your message</label>
          <textarea ref={messageInput} id="chat-input" data-testid="chat-input" value={input} maxLength={4000}
            onChange={event => setInput(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                send(input);
              }
            }} placeholder="Type a message…" rows="2" />
          <button data-testid="send-button" type="submit" disabled={busy || !input.trim()}>Send <span aria-hidden="true">↑</span></button>
        </form>
        <p className="tip">Enter to send · Shift + Enter for a new line</p>
      </section>
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
