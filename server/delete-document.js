import { lstat, realpath, unlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { legacyDocumentId } from './documents.js';
import { vectorStoreConfig } from './ai.js';

const serverDirectory = fileURLToPath(new URL('.', import.meta.url));
const uploadsDirectory = path.join(serverDirectory, 'uploads');

function conflict(message) {
  return Object.assign(new Error(message), { status: 409, publicMessage: message });
}

function isInside(directory, file) {
  const relative = path.relative(directory, file);
  return relative && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

// Resolve only paths obtained from stored metadata/jobs, never from the request.
// No directory or recursive deletion is allowed.
export async function storedPdfPath(source, directory = uploadsDirectory) {
  const file = path.resolve(path.dirname(directory), source);
  if (!isInside(directory, file)) throw conflict('The stored PDF path is outside uploads. Remove it manually after checking the file.');
  try {
    const info = await lstat(file);
    if (!info.isFile() || info.isSymbolicLink()) throw conflict('The stored PDF is not a regular file. Check it manually.');
    const [realDirectory, realFile] = await Promise.all([realpath(directory), realpath(file)]);
    if (!isInside(realDirectory, realFile)) throw conflict('The stored PDF path is outside uploads. Check it manually.');
    return file;
  } catch (error) {
    if (error.code === 'ENOENT') return null; // Previously removed PDFs do not block cleanup.
    throw error;
  }
}

export async function deleteDocument(document, documents, queue, config = vectorStoreConfig) {
  const jobs = await queue.getJobs(['waiting', 'active', 'delayed', 'prioritized', 'waiting-children', 'completed', 'failed']);
  const matchingJobs = jobs.filter((job) => {
    const data = JSON.parse(job.data);
    if (data.ownerId !== document.ownerId) return false;
    return (data.documentId || (data.path ? legacyDocumentId(data.path) : null)) === document.id;
  });
  for (const job of matchingJobs) {
    if (!['completed', 'failed'].includes(await job.getState())) {
      throw conflict('This document is still indexing. Wait until indexing finishes before deleting it.');
    }
  }
  const sources = new Set(document.sourcePaths);
  for (const job of matchingJobs) {
    const data = JSON.parse(job.data);
    if (data.path) sources.add(data.path);
  }
  const files = [];
  for (const source of sources) {
    const file = path.resolve(serverDirectory, source);
    if (documents.some((other) => other.id !== document.id && other.sourcePaths.some((value) => path.resolve(serverDirectory, value) === file))) {
      throw conflict('Another document shares this stored file. Check the uploads before deleting it.');
    }
    const safePath = await storedPdfPath(source);
    if (safePath) files.push(safePath);
  }
  // Keep the document listed until cleanup succeeds, so a failed request can be retried.
  // Removing completed/failed jobs also removes the retained pasted text from Valkey.
  for (const job of matchingJobs) await job.remove();
  for (const file of files) {
    try { await unlink(file); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const response = await fetch(`${config.url}/collections/${encodeURIComponent(config.collectionName)}/points/delete?wait=true`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filter: document.filter }), signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error('Unable to remove document vectors.');
}
