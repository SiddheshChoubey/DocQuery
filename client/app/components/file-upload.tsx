'use client';
import * as React from 'react';
import { Upload } from 'lucide-react';

const API = 'http://localhost:8000';
const STORAGE_KEY = 'pdf-rag-upload-job';

const FileUploadComponent: React.FC<{
  disabled?: boolean;
  onBusyChange: (busy: boolean) => void;
  onDocumentReady: (documentId: string) => void;
}> = ({ disabled = false, onBusyChange, onDocumentReady }) => {
  const input = React.useRef<HTMLInputElement>(null);
  const request = React.useRef<XMLHttpRequest | null>(null);
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const [filename, setFilename] = React.useState('');
  const [message, setMessage] = React.useState('Upload a PDF or paste text to make it searchable.');
  const [percent, setPercent] = React.useState<number | undefined>();
  const [error, setError] = React.useState('');
  const [mode, setMode] = React.useState<'pdf' | 'text'>('pdf');
  const [title, setTitle] = React.useState('');
  const [text, setText] = React.useState('');
  const processing = uploading || Boolean(jobId);
  const busy = processing || disabled;

  React.useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null');
      if (saved?.jobId) {
        setJobId(saved.jobId);
        setFilename(saved.filename || 'PDF');
        setMessage('Checking indexing status…');
      }
    } catch { /* Session storage is optional. */ }
    return () => request.current?.abort();
  }, []);

  React.useEffect(() => { onBusyChange(processing); }, [processing, onBusyChange]);

  React.useEffect(() => {
    if (!jobId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const response = await fetch(`${API}/upload/status/${encodeURIComponent(jobId)}`, {
          signal: controller.signal, cache: 'no-store',
        });
        const data = await response.json();
        if (disposed) return;
        if (!response.ok && response.status !== 404) throw new Error('status unavailable');
        if (data.state === 'completed' || data.state === 'failed' || response.status === 404) {
          setJobId(null);
          try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Optional storage. */ }
          const ready = data.state === 'completed';
          setPercent(ready ? 100 : undefined);
          setMessage(ready ? 'Document ready! You can now ask questions.' : 'Document is not ready.');
          setError(ready ? '' : data.message || 'Indexing failed. Try uploading again.');
          if (ready && data.documentId) onDocumentReady(data.documentId);
          return;
        }
        setError('');
        const progress = data.progress;
        if (data.state !== 'active') {
          setMessage('Queued — waiting for the indexing worker. Keep the worker terminal running.');
          setPercent(undefined);
        } else if (progress?.phase === 'embedding') {
          setMessage(`Indexing document: ${progress.completed} of ${progress.total} chunks. Large documents can take several minutes.`);
          setPercent(progress.total ? Math.round(progress.completed / progress.total * 100) : 0);
        } else if (progress?.phase === 'saving' || progress?.phase === 'ready') {
          setMessage('Saving searchable text…');
          setPercent(undefined);
        } else {
          setMessage('Reading and indexing document… Please wait for the ready message.');
          setPercent(undefined);
        }
      } catch {
        if (disposed) return;
        setError('Cannot check progress. Make sure the backend is running; retrying automatically.');
      }
      if (!disposed) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { disposed = true; controller.abort(); clearTimeout(timer); };
  }, [jobId, onDocumentReady]);

  const upload = (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('Please select a PDF file.');
      return;
    }
    const form = new FormData();
    form.append('pdf', file);
    submit('/upload/pdf', form, file.name);
  };

  const submit = (endpoint: string, body: FormData | string, name: string) => {
    setFilename(name);
    setError('');
    setUploading(true);
    setPercent(0);
    setMessage('Submitting document…');
    const xhr = new XMLHttpRequest();
    request.current = xhr;
    xhr.open('POST', `${API}${endpoint}`);
    if (typeof body === 'string') xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.timeout = 120000;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) setPercent(Math.round(event.loaded / event.total * 100));
    };
    const fail = (reason: string) => {
      setUploading(false);
      setPercent(undefined);
      setMessage('Upload failed.');
      setError(reason);
    };
    xhr.onerror = () => fail('Cannot reach the backend at localhost:8000.');
    xhr.ontimeout = () => fail('Upload timed out. Check the backend and try again.');
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status < 200 || xhr.status >= 300 || !data.jobId) {
          fail(data.message || 'Upload failed.');
          return;
        }
        setJobId(String(data.jobId));
        setUploading(false);
        setPercent(undefined);
        setMessage('Uploaded. Waiting for indexing…');
        try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId: String(data.jobId), filename: name })); } catch { /* Optional storage. */ }
      } catch { fail('Unexpected server response. Restart the backend and try again.'); }
    };
    xhr.send(body);
  };

  return (
    <div className="bg-slate-900 text-white shadow-2xl p-4 rounded-lg border-white border-2 w-full space-y-4">
      <div className="flex gap-4" aria-label="Document input type">
        <button type="button" disabled={busy} aria-pressed={mode === 'pdf'} onClick={() => setMode('pdf')} className={mode === 'pdf' ? 'underline font-semibold' : ''}>PDF</button>
        <button type="button" disabled={busy} aria-pressed={mode === 'text'} onClick={() => setMode('text')} className={mode === 'text' ? 'underline font-semibold' : ''}>Paste text</button>
      </div>
      <input ref={input} type="file" accept="application/pdf,.pdf" className="hidden" onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file && !busy) upload(file);
      }} />
      {mode === 'pdf' && <button type="button" disabled={busy} onClick={() => input.current?.click()}
        className="flex items-center justify-center gap-2 w-full disabled:opacity-50 disabled:cursor-not-allowed">
        <Upload size={20} /> {busy ? 'Processing…' : 'Upload PDF File'}
      </button>}
      {mode === 'text' && <form className="space-y-3" onSubmit={(event) => {
        event.preventDefault();
        if (!busy && title.trim() && text.trim()) submit('/upload/text', JSON.stringify({ title, text }), title.trim());
      }}>
        <label className="block text-sm">Title
          <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} required disabled={busy}
            placeholder="My study notes" className="mt-1 w-full rounded border border-slate-500 bg-slate-800 p-2" />
        </label>
        <label className="block text-sm">Text
          <textarea value={text} onChange={(event) => setText(event.target.value)} maxLength={50000} rows={6} required disabled={busy}
            placeholder="Paste your notes here…" className="mt-1 w-full rounded border border-slate-500 bg-slate-800 p-2" />
        </label>
        <p className="text-xs text-slate-300">{text.length.toLocaleString()} / 50,000 characters</p>
        <button type="submit" disabled={busy || !title.trim() || !text.trim()} className="w-full rounded bg-blue-600 p-2 disabled:opacity-50">
          {busy ? 'Processing…' : 'Add text'}
        </button>
      </form>}
      {filename && <p className="text-sm break-all">{filename}</p>}
      <p role="status" aria-live="polite" className="text-sm">{message}</p>
      {processing && <progress aria-label={uploading ? 'Upload progress' : 'Indexing progress'} max={100} value={percent} className="w-full" />}
      {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    </div>
  );
};

export default FileUploadComponent;
