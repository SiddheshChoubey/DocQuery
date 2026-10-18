'use client';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import * as React from 'react';
import CitedAnswer, { type Source } from './cited-answer';

interface IMessage {
  role: 'assistant' | 'user';
  content?: string;
  sources?: Source[];
  citationNote?: string;
}

const ChatComponent: React.FC<{
  uploadBusy: boolean;
  documentId: string;
  documentName?: string;
  documentsLoading: boolean;
}> = ({ uploadBusy, documentId, documentName, documentsLoading }) => {
  const [message, setMessage] = React.useState<string>('');
  const [messages, setMessages] = React.useState<IMessage[]>([]);

  const [sending, setSending] = React.useState(false);
  const [error, setError] = React.useState('');
  const request = React.useRef<AbortController | null>(null);
  React.useEffect(() => () => request.current?.abort(), []);
  const disabled = uploadBusy || sending || !documentId || documentsLoading;

  const handleSendChatMessage = async () => {
    if (!message.trim() || disabled) return;
    const question = message.trim();
    setSending(true);
    setError('');
    setMessage('');
    setMessages((prev) => [...prev, { role: 'user', content: question }]);
    const controller = new AbortController();
    request.current = controller;
    try {
      const params = new URLSearchParams({ message: question, documentId });
      const res = await fetch(`http://localhost:8000/chat?${params}`, { signal: controller.signal });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'The request failed. Try again.');
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: data?.message,
          sources: data?.sources || [],
          citationNote: data?.citationNote,
        },
      ]);
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to reach the backend.');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="p-4 space-y-4">
      <p className="font-semibold break-words">{documentName ? `Chat with ${documentName}` : 'Select an indexed document to start chatting.'}</p>
      <div className="space-y-4 pb-20">
        {messages.map((message, index) => (
          <div key={index} className="rounded-lg border p-3">
            <p className="text-sm font-semibold mb-2">{message.role === 'user' ? 'You' : 'Assistant'}</p>
            {message.role === 'assistant'
              ? <CitedAnswer content={message.content || ''} sources={message.sources || []} citationNote={message.citationNote} />
              : <p className="whitespace-pre-wrap break-words">{message.content}</p>}
          </div>
        ))}
      </div>
      {uploadBusy && <p role="status">Your document is still processing. Chat will be available when it is ready.</p>}
      {error && <p role="alert" className="text-red-500">{error}</p>}
      <div className="fixed bottom-4 right-4 w-[calc(70vw-2rem)] flex gap-3">
        <Input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Type your message here"
          disabled={disabled}
          onKeyDown={(event) => { if (event.key === 'Enter') void handleSendChatMessage(); }}
        />
        <Button onClick={handleSendChatMessage} disabled={!message.trim() || disabled}>
          {sending ? 'Thinking…' : 'Send'}
        </Button>
      </div>
    </div>
  );
};
export default ChatComponent;
