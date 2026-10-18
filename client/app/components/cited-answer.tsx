'use client';

import { useId, useRef, useState } from 'react';

export interface Source {
  id: number;
  filename: string;
  sourceType?: 'pdf' | 'text';
  page: number | null;
  passage: string;
  cited: boolean;
}

export default function CitedAnswer({ content, sources, citationNote }: {
  content: string;
  sources: Source[];
  citationNote?: string;
}) {
  const answerId = useId();
  const details = useRef<HTMLDetailsElement>(null);
  const cards = useRef(new Map<number, HTMLElement>());
  const [activeSource, setActiveSource] = useState<number | null>(null);
  const sourceId = (id: number) => `${answerId}-source-${id}`;

  const openSource = (id: number) => {
    if (details.current) details.current.open = true;
    setActiveSource(id);
    requestAnimationFrame(() => {
      const card = cards.current.get(id);
      card?.focus({ preventScroll: true });
      card?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  };

  // Preserve text and code without interpreting model output as HTML.
  // Only references that map to this answer's sources become links.
  const parts = content.split(/(```[\s\S]*?```|`[^`\n]*`|\[\d+(?:\s*,\s*\d+)*\])/g);

  return (
    <div className="space-y-3">
      <div className="whitespace-pre-wrap break-words leading-relaxed">
        {parts.map((part, index) => {
          if (part.startsWith('```')) {
            const code = part.slice(3, -3).replace(/^[\w+-]*\r?\n/, '');
            return <pre key={index} className="my-3 overflow-x-auto rounded-md bg-muted p-3 text-sm whitespace-pre"><code>{code}</code></pre>;
          }
          if (part.startsWith('`') && part.endsWith('`')) {
            return <code key={index} className="rounded bg-muted px-1 text-sm">{part.slice(1, -1)}</code>;
          }
          if (/^\[\d+(?:\s*,\s*\d+)*\]$/.test(part)) {
            return <span key={index}>{part.slice(1, -1).split(',').map((number, referenceIndex) => {
              const id = Number(number.trim());
              const source = sources.find((item) => item.id === id);
              if (!source) return <span key={referenceIndex} className="text-amber-700 dark:text-amber-400" title="This reference has no retrieved source">[{id}]</span>;
              const label = `${source.filename}, ${source.sourceType === 'text' ? 'pasted text' : source.page ? `page ${source.page}` : 'page unavailable'}`;
              return <a key={referenceIndex} href={`#${sourceId(id)}`} title={label}
                aria-label={`Show source ${id}: ${label}`}
                className="mx-0.5 rounded text-blue-700 dark:text-blue-300 font-semibold underline underline-offset-2 focus-visible:outline-2"
                onClick={(event) => { event.preventDefault(); openSource(id); }}>[{id}]</a>;
            })}</span>;
          }
          return part;
        })}
      </div>
      {citationNote && <p role="status" className="text-sm text-muted-foreground">{citationNote}</p>}
      {!!sources.length && <details ref={details} className="rounded-md border p-3 text-sm">
        <summary className="cursor-pointer font-medium">Retrieved sources ({sources.length})</summary>
        <p className="mt-2 text-muted-foreground">These are the original passages supplied for this answer. Click a reference to inspect its passage.</p>
        <div className="mt-3 space-y-3">
          {sources.map((source) => <article key={source.id} id={sourceId(source.id)} tabIndex={-1}
            ref={(element) => { if (element) cards.current.set(source.id, element); else cards.current.delete(source.id); }}
            aria-label={`Source ${source.id}`}
            className={`scroll-mt-4 rounded-md border p-3 focus:outline-none ${activeSource === source.id ? 'ring-2 ring-blue-500' : ''}`}>
            <p className="font-semibold break-words">[{source.id}] {source.filename}</p>
            <p className="mt-1 text-muted-foreground">
              {source.sourceType === 'text' ? 'Pasted text' : source.page ? `PDF page ${source.page}` : 'Page number unavailable'} · {source.cited ? 'Cited in answer' : 'Retrieved, not cited'}
            </p>
            <blockquote className="mt-3 border-l-2 pl-3 whitespace-pre-wrap break-words">{source.passage}</blockquote>
          </article>)}
        </div>
      </details>}
    </div>
  );
}
