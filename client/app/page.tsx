'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import FileUploadComponent from './components/file-upload';
import ChatComponent from './components/chat';
import DocumentLibrary, { type IndexedDocument } from './components/document-library';

const SELECTION_KEY = 'pdf-rag-selected-document';

export default function Home() {
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
      const response = await fetch('http://localhost:8000/documents', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('Could not load documents. Check that the backend and Qdrant are running.');
      const data = await response.json();
      const indexed: IndexedDocument[] = data.documents;
      setDocuments(indexed);
      let saved = '';
      try { saved = sessionStorage.getItem(SELECTION_KEY) || ''; } catch { /* Optional storage. */ }
      setSelectedId((current) => {
        const next = [preferredId, current, saved].find((id) => indexed.some((doc) => doc.id === id)) || indexed[0]?.id || '';
        return next;
      });
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load documents.');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshDocuments();
    return () => listRequest.current?.abort();
  }, [refreshDocuments]);

  useEffect(() => {
    try {
      if (selectedId) sessionStorage.setItem(SELECTION_KEY, selectedId);
      else if (!loading) sessionStorage.removeItem(SELECTION_KEY);
    } catch { /* Optional storage. */ }
  }, [selectedId, loading]);

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
      const response = await fetch(`http://localhost:8000/documents/${encodeURIComponent(document.id)}`, { method: 'DELETE' });
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
      <div className="min-h-screen w-screen flex">
        <div className="w-[30vw] min-h-screen p-4 flex flex-col justify-center gap-6">
          <div className="space-y-3">
            <label htmlFor="document-selection" className="block font-semibold">Ask questions about</label>
            <select id="document-selection" value={selectedId} disabled={loading || busy || !documents.length}
              onChange={(event) => setSelectedId(event.target.value)} className="w-full border rounded-md p-2 bg-background">
              {!documents.length && <option value="">{loading ? 'Loading documents…' : 'No indexed documents yet'}</option>}
              {documents.map((doc) => <option key={doc.id} value={doc.id}>
                {doc.filename}{documents.filter((item) => item.filename === doc.filename).length > 1
                  ? ` (copy ${documents.filter((item) => item.filename === doc.filename).findIndex((item) => item.id === doc.id) + 1})`
                  : ''}{doc.sourceType === 'text' ? ' [Text]' : ''}
              </option>)}
            </select>
            <button type="button" disabled={loading || busy} onClick={() => void refreshDocuments()}
              className="text-sm underline disabled:opacity-50">{loading ? 'Loading…' : 'Refresh document list'}</button>
            <p className="text-sm text-muted-foreground">Answers use only the selected document. Switching documents starts a fresh chat view.</p>
            {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
          </div>
          <FileUploadComponent disabled={Boolean(deletingId)} onBusyChange={setUploadBusy} onDocumentReady={onDocumentReady} />
          <DocumentLibrary documents={documents} selectedId={selectedId} busy={busy || loading} deletingId={deletingId}
            onSelect={setSelectedId} onDelete={removeDocument} />
          {libraryMessage && <p role="status" className="text-sm">{libraryMessage}</p>}
          {deleteError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{deleteError}</p>}
        </div>
        <div className="w-[70vw] min-h-screen border-l-2">
          <ChatComponent key={selectedId} uploadBusy={uploadBusy} documentId={selectedId}
            documentName={selectedDocument?.filename} documentsLoading={loading || Boolean(error) || Boolean(deletingId)} />
        </div>
      </div>
    </div>
  );
}
