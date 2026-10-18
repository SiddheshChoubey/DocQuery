import { PDFLoader } from '@langchain/community/document_loaders/fs/pdf';
import { RecursiveCharacterTextSplitter } from '@langchain/textsplitters';
import { legacyDocumentId } from './documents.js';

export function validateTextInput(body) {
  if (typeof body?.title !== 'string' || !body.title.trim()) return 'Enter a title.';
  if (body.title.trim().length > 120) return 'Keep the title within 120 characters.';
  if (typeof body?.text !== 'string' || !body.text.trim()) return 'Paste some text to index.';
  if (body.text.length > 50000) return 'Keep pasted text within 50,000 characters.';
  return null;
}

export async function prepareDocuments(data) {
  const isText = data.sourceType === 'text';
  const pages = isText
    ? [{ pageContent: data.text, metadata: {} }]
    : await new PDFLoader(data.path).load();
  const splitter = new RecursiveCharacterTextSplitter({ chunkSize: 1500, chunkOverlap: 200 });
  const docs = await splitter.splitDocuments(pages.filter((page) => page.pageContent.trim()));
  if (!docs.length) throw new Error('No extractable text found. Scanned PDFs need OCR.');
  const documentId = data.documentId || legacyDocumentId(data.path);
  for (const doc of docs) {
    doc.metadata = { ...doc.metadata, documentId, fileName: data.filename, sourceType: isText ? 'text' : 'pdf',
      ...(data.uploadedAt ? { uploadedAt: data.uploadedAt } : {}) };
  }
  return docs;
}
