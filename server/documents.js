import { createHash } from 'node:crypto';
import { requireOwner } from './conversations.js';
import { vectorStoreConfig } from './ai.js';

// Older uploads already have a unique source path. Keep them selectable without
// modifying their vectors or paying to embed them again.
export function legacyDocumentId(source) {
  return `legacy-${createHash('sha256').update(source).digest('hex')}`;
}

export function documentFromMetadata(metadata = {}) {
  const source = metadata.source;
  if (!metadata.documentId && !source) return null;
  const id = metadata.documentId || legacyDocumentId(source);
  const filename = metadata.fileName || (source ? source.split(/[\\/]/).pop().replace(/^\d+-\d+-/, '') : 'PDF');
  const legacyTimestamp = source?.split(/[\\/]/).pop().match(/^(\d{13})-\d+-/)?.[1];
  const timestamp = metadata.uploadedAt ? Date.parse(metadata.uploadedAt) : legacyTimestamp ? Number(legacyTimestamp) : NaN;
  return {
    id,
    ownerId: metadata.ownerId,
    filename,
    sourceType: metadata.sourceType === 'text' ? 'text' : 'pdf',
    uploadedAt: Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null,
    sourcePaths: source ? [source] : [],
    chunkCount: 0,
    filter: { must: [{
      key: metadata.documentId ? 'metadata.documentId' : 'metadata.source',
      match: { value: metadata.documentId || source },
    }, ...(metadata.ownerId ? [{ key: 'metadata.ownerId', match: { value: metadata.ownerId } }] : [])] },
  };
}

// Qdrant remains the source of truth: no extra database or document registry.
// Only metadata is read, never PDF text or vectors, when building the dropdown.
export async function listDocuments(config = vectorStoreConfig, ownerId) {
  if (ownerId !== undefined) requireOwner(ownerId);
  const documents = new Map();
  let offset;
  do {
    const response = await fetch(`${config.url}/collections/${encodeURIComponent(config.collectionName)}/points/scroll`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(10000),
      body: JSON.stringify({
        limit: 256, offset, with_vector: false,
        with_payload: ['metadata.documentId', 'metadata.fileName', 'metadata.source', 'metadata.sourceType', 'metadata.uploadedAt', 'metadata.ownerId'],
        ...(ownerId ? { filter: { must: [{ key: 'metadata.ownerId', match: { value: ownerId } }] } } : {}),
      }),
    });
    if (response.status === 404) return []; // No PDF has been indexed yet.
    if (!response.ok) throw new Error('Unable to list indexed PDFs from Qdrant.');
    const { result } = await response.json();
    for (const point of result.points) {
      const document = documentFromMetadata(point.payload?.metadata);
      if (!document || (ownerId && document.ownerId !== ownerId)) continue;
      if (!documents.has(document.id)) documents.set(document.id, document);
      const existing = documents.get(document.id);
      existing.chunkCount++;
      existing.sourcePaths = [...new Set([...existing.sourcePaths, ...document.sourcePaths])];
    }
    offset = result.next_page_offset;
  } while (offset !== null && offset !== undefined);
  return [...documents.values()].sort((a, b) => a.filename.localeCompare(b.filename) || a.id.localeCompare(b.id));
}
