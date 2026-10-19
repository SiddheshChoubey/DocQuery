'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { UserButton, useSession } from '@clerk/nextjs';
import FileUploadComponent from './components/file-upload';
import ChatComponent from './components/chat';
import DocumentLibrary, { type IndexedDocument } from './components/document-library';

import { createAuthenticatedFetch, type GetToken } from '@/lib/authenticated-fetch';

export default function Home() {
  const { session, isLoaded } = useSession();
  const getToken = useCallback(() => session ? session.getToken() : Promise.resolve(null), [session]);
  if (!isLoaded || !session) return null;
  return <Workspace key={session.id} userId={session.user.id} getToken={getToken} />;
}

function Workspace({ userId, getToken }: { userId: string; getToken: GetToken }) {
  const fetchApi = useMemo(() => createAuthenticatedFetch(getToken), [getToken]);
  const selectionKey = `pdf-rag-selected-document-${userId}`;
  const [uploadBusy, setUploadBusy] = useState(false);
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [libraryMessage, setLibraryMessage] = useState('');
  const [deleteError, setDeleteError] = useState('');
  const busy = uploadBusy || Boolean(deletingId);
  const listRequest = useRef<AbortController | null>(null);

  const refreshDocuments = useCallback(async (preferredId?: string) => {
    listRequest.current?.abort();
    const controller = new AbortController();
    listRequest.current = controller;
    setLoading(true);
    setError('');
    try {
      const response = await fetchApi('http://localhost:8000/documents', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('Could not load documents. Check that the backend and Qdrant are running.');
      const data = await response.json();
      if (controller.signal.aborted) return;
      const indexed: IndexedDocument[] = data.documents;
      setDocuments(indexed);
      let saved = '';
      try { saved = sessionStorage.getItem(selectionKey) || ''; } catch { /* Optional storage. */ }
      setSelectedId((current) => {
        const next = [preferredId, current, saved].find((id) => indexed.some((doc) => doc.id === id)) || indexed[0]?.id || '';
        return next;
      });
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load documents.');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [fetchApi, selectionKey]);

  useEffect(() => {
    void refreshDocuments();
    return () => listRequest.current?.abort();
  }, [refreshDocuments]);

  useEffect(() => {
    try {
      if (selectedId) sessionStorage.setItem(selectionKey, selectedId);
      else if (!loading) sessionStorage.removeItem(selectionKey);
    } catch { /* Optional storage. */ }
  }, [selectedId, loading, selectionKey]);

  const onDocumentReady = useCallback((documentId: string) => {
    void refreshDocuments(documentId);
  }, [refreshDocuments]);

  const selectedDocument = documents.find((doc) => doc.id === selectedId);
  const removeDocument = async (document: IndexedDocument) => {
    if (busy || loading) return;
    setDeletingId(document.id);
    setDeleteError('');
    setLibraryMessage('');
    try {
      const response = await fetchApi(`http://localhost:8000/documents/${encodeURIComponent(document.id)}`, { method: 'DELETE' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'Unable to delete this document.');
      setDocuments((current) => current.filter((item) => item.id !== document.id));
      setSelectedId((current) => current === document.id ? '' : current);
      setLibraryMessage(`Deleted ${document.filename}.`);
      await refreshDocuments();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Deletion failed. Check the backend and try again.');
    } finally {
      setDeletingId(null);
    }
  };
  return (
    <div>
      <div className="min-h-dvh w-full bg-muted/30 lg:grid lg:h-dvh lg:grid-cols-[320px_minmax(0,1fr)] lg:overflow-hidden">
        <div className="flex flex-col gap-6 border-b bg-background p-5 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:p-6">
          <header className="flex items-center gap-3 pb-2">
            <div aria-hidden="true" className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-sm font-semibold text-white">DQ</div>
            <div className="min-w-0 flex-1"><h1 className="text-xl font-semibold tracking-tight">DocQuery</h1>
              <p className="text-xs text-muted-foreground">Your document workspace</p></div>
            <div className="shrink-0"><UserButton /></div>
          </header>
          <div className="space-y-3">
            <label htmlFor="document-selection" className="block text-xs font-semibold uppercase tracking-wider text-muted-foreground">Selected document</label>
            <select id="document-selection" value={selectedId} disabled={loading || busy || !documents.length}
              onChange={(event) => setSelectedId(event.target.value)} className="w-full rounded-lg border bg-background p-2.5 text-sm shadow-xs focus-visible:outline-2 focus-visible:outline-indigo-500">
              {!documents.length && <option value="">{loading ? 'Loading documents…' : 'No indexed documents yet'}</option>}
              {documents.map((doc) => <option key={doc.id} value={doc.id}>
                {doc.filename}{documents.filter((item) => item.filename === doc.filename).length > 1
                  ? ` (copy ${documents.filter((item) => item.filename === doc.filename).findIndex((item) => item.id === doc.id) + 1})`
                  : ''}{doc.sourceType === 'text' ? ' [Text]' : ''}
              </option>)}
            </select>
            <button type="button" disabled={loading || busy} onClick={() => void refreshDocuments()}
              className="text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-50">{loading ? 'Loading…' : 'Refresh document list'}</button>
            <p className="text-sm text-muted-foreground">Answers use only the selected document. Each document has its own saved conversations.</p>
            {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
          </div>
          <FileUploadComponent userId={userId} fetchApi={fetchApi} getToken={getToken} disabled={Boolean(deletingId)} onBusyChange={setUploadBusy} onDocumentReady={onDocumentReady} />
          <DocumentLibrary documents={documents} selectedId={selectedId} busy={busy || loading} deletingId={deletingId}
            onSelect={setSelectedId} onDelete={removeDocument} />
          {libraryMessage && <p role="status" className="text-sm">{libraryMessage}</p>}
          {deleteError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{deleteError}</p>}
        </div>
        <div className="min-w-0 p-3 sm:p-5 lg:h-full lg:min-h-0 lg:p-6">
          <ChatComponent userId={userId} fetchApi={fetchApi} key={selectedId} uploadBusy={uploadBusy} documentId={selectedId}
            documentName={selectedDocument?.filename} documentsLoading={loading || Boolean(error) || Boolean(deletingId)} />
        </div>
      </div>
    </div>
  );
}
