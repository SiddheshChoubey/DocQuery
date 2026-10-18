import test from 'node:test';
import assert from 'node:assert/strict';
import { uploadFailureMessage } from '../upload-status.js';

test('upload failures provide actionable messages without exposing provider details', () => {
  assert.match(uploadFailureMessage('429 RESOURCE_EXHAUSTED key=secret'), /quota/);
  assert.match(uploadFailureMessage('403 permission denied key=secret'), /API key/);
  assert.match(uploadFailureMessage('No extractable text found'), /OCR/);
  assert.match(uploadFailureMessage('Invalid PDF structure'), /could not be read/);
  assert.match(uploadFailureMessage('404 model not found key=secret'), /unavailable/);
  for (const reason of ['unexpected secret', '429 key=secret', '403 secret', '404 secret']) {
    assert.ok(!uploadFailureMessage(reason).includes('secret'));
  }
});
