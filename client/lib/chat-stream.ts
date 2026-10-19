// Network reads can split a JSON line or even a UTF-8 character anywhere.
export async function readChatStream(response: Response, onEvent: (event: Record<string, unknown>) => void) {
  if (!response.ok) {
    const data = await response.json();
    throw new Error(data.message || 'The request failed. Try again.');
  }
  if (!response.body || !response.headers.get('content-type')?.includes('application/x-ndjson')) {
    throw new Error('Streaming is unavailable. Restart the API and try again.');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;
  try {
    while (!finished) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        onEvent(event);
        if (event.type === 'done') { finished = true; break; }
      }
      if (done && !finished) throw new Error('The answer stream was interrupted. Reloading saved chat.');
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
