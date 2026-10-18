// Never send raw provider errors (which may include request URLs/keys) to the UI.
export function uploadFailureMessage(reason = '') {
  if (/429|quota|RESOURCE_EXHAUSTED/i.test(reason)) {
    return 'Gemini quota was reached while indexing. Wait for your quota to reset, then upload again.';
  }
  if (/API.key|401|403|permission|UNAUTHENTICATED/i.test(reason)) {
    return 'Gemini rejected the request. Check your API key and model access.';
  }
  if (/404|not found|not supported/i.test(reason)) {
    return 'A required model or resource was unavailable. Check the worker and Gemini model access.';
  }
  if (/No extractable text/i.test(reason)) {
    return 'This PDF has no extractable text. Scanned PDFs need OCR first.';
  }
  if (/Invalid PDF|password|encrypted/i.test(reason)) {
    return 'This PDF could not be read. Try an unencrypted, text-based PDF.';
  }
  return 'Indexing failed. Check the PDF worker, Gemini access, and Qdrant, then upload again.';
}
