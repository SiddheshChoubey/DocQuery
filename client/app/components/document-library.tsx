'use client';

import { useState } from 'react';

export interface IndexedDocument {
  id: string;
  filename: string;
  chunkCount: number;
  sourceType: 'pdf' | 'text';
  uploadedAt: string | null;
}

export default function DocumentLibrary({ documents, selectedId, busy, deletingId, onSelect, onDelete }: {
  documents: IndexedDocument[];
  selectedId: string;
  busy: boolean;
  deletingId: string | null;
  onSelect: (id: string) => void;
  onDelete: (document: IndexedDocument) => Promise<void>;
}) {
  const [confirmId, setConfirmId] = useState<string | null>(null);
  return <section aria-labelledby="library-heading" className="space-y-3">
    <h2 id="library-heading" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Document library ({documents.length})</h2>
    {!documents.length && <p className="text-sm text-muted-foreground">Upload a PDF or paste notes to add your first document.</p>}
    <ul className="max-h-80 overflow-y-auto space-y-3">
      {documents.map((document) => <li key={document.id} className={`rounded-xl border p-3 space-y-2 transition-colors ${selectedId === document.id ? 'border-indigo-300 bg-indigo-50/60 dark:border-indigo-700 dark:bg-indigo-950/30' : 'border-border bg-card hover:bg-muted/40'}`}>
        <button type="button" disabled={busy} onClick={() => onSelect(document.id)}
          aria-pressed={selectedId === document.id} className="block w-full text-left text-sm font-medium break-words hover:text-indigo-600 focus-visible:outline-2 focus-visible:outline-indigo-500 disabled:opacity-50">
          {document.filename}
          {documents.filter((item) => item.filename === document.filename).length > 1
            ? ` (copy ${documents.filter((item) => item.filename === document.filename).findIndex((item) => item.id === document.id) + 1})` : ''}
        </button>
        <p className="text-xs text-muted-foreground">{document.sourceType === 'text' ? 'Pasted text' : 'PDF'} · {document.chunkCount} {document.chunkCount === 1 ? 'chunk' : 'chunks'}</p>
        <p className="text-xs text-muted-foreground">Uploaded: {document.uploadedAt
          ? <time dateTime={document.uploadedAt}>{new Date(document.uploadedAt).toLocaleString()}</time> : 'Date unavailable'}</p>
        {confirmId === document.id ? <div className="space-y-2">
          <p className="text-sm">Delete this document, its searchable text{document.sourceType === 'pdf' ? ', and stored PDF' : ''}? This cannot be undone.</p>
          <div className="flex gap-4 text-sm">
            <button type="button" disabled={busy} onClick={() => void onDelete(document)} className="text-red-600 dark:text-red-400 underline disabled:opacity-50">
              {deletingId === document.id ? 'Deleting…' : 'Confirm delete'}
            </button>
            <button type="button" disabled={busy} onClick={() => setConfirmId(null)} className="underline disabled:opacity-50">Cancel</button>
          </div>
        </div> : <button type="button" disabled={busy} onClick={() => setConfirmId(document.id)}
          aria-label={`Delete ${document.filename}`} className="text-xs text-muted-foreground hover:text-red-600 focus-visible:outline-2 disabled:opacity-50">Delete</button>}
      </li>)}
    </ul>
  </section>;
}
