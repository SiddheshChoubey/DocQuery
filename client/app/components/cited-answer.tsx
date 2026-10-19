'use client';

import React, { useId, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkCitations from '../../lib/remark-citations';

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

  return (
    <div className="min-w-0 space-y-3">
      <div className="answer-markdown min-w-0 break-words leading-relaxed">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkCitations]} skipHtml components={{
          a: ({ href, children }) => {
            const citation = href?.match(/^#docquery-citation-(\d+)$/);
            if (citation) {
              const id = Number(citation[1]);
              const source = sources.find((item) => item.id === id);
              if (!source) return <span className="text-amber-700 dark:text-amber-400" title="This reference has no retrieved source">[{id}]</span>;
              const label = `${source.filename}, ${source.sourceType === 'text' ? 'pasted text' : source.page ? `page ${source.page}` : 'page unavailable'}`;
              return <a href={`#${sourceId(id)}`} title={label}
                aria-label={`Show source ${id}: ${label}`}
                className="mx-0.5 rounded text-blue-700 dark:text-blue-300 font-semibold underline underline-offset-2 focus-visible:outline-2"
                onClick={(event) => { event.preventDefault(); openSource(id); }}>[{id}]</a>;
            }
            return href ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-700 dark:text-blue-300">{children}</a> : <span>{children}</span>;
          },
          table: ({ children }) => <div className="my-3 max-w-full overflow-x-auto"><table>{children}</table></div>,
          // Answers should not silently load images from model-generated URLs.
          img: ({ alt }) => <span>{alt}</span>,
        }}>{content}</ReactMarkdown>
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
