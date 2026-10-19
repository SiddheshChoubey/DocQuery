import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import CitedAnswer, { type Source } from '../app/components/cited-answer';

const sources: Source[] = [
  { id: 1, filename: 'Guide.pdf', page: 3, passage: 'Original PDF passage.', cited: true },
  { id: 2, filename: 'Study notes', sourceType: 'text', page: null, passage: 'Original notes.', cited: true },
];
const render = (content: string) => renderToStaticMarkup(<CitedAnswer content={content} sources={sources} />);

test('answers render Markdown blocks and references inside formatted content', () => {
  const html = render(`# Summary

**Bold** and *emphasized* text.

- A point [1]
- Another point

1. First step
2. Second step

> A quoted passage.

| Item | Source |
| --- | --- |
| Note | [2] |

~~~js
const value = items[9];
~~~`);
  for (const tag of ['h1', 'strong', 'em', 'ul', 'ol', 'blockquote', 'table', 'th', 'td', 'pre', 'code']) {
    assert.match(html, new RegExp(`<${tag}[\\s>]`), tag);
  }
  assert.match(html, /Show source 1: Guide.pdf, page 3/);
  assert.match(html, /Show source 2: Study notes, pasted text/);
  assert.match(html, /class="language-js"/);
  assert.doesNotMatch(html, /Show source 9/);
});

test('grouped and repeated citations retain exact IDs; unknown sources are not linked', () => {
  const html = render('Both **[1, 2]**, again [1], and unknown [99].');
  assert.equal((html.match(/aria-label="Show source 1:/g) || []).length, 2);
  assert.equal((html.match(/aria-label="Show source 2:/g) || []).length, 1);
  assert.doesNotMatch(html, /Show source 99/);
  assert.match(html, /This reference has no retrieved source/);
  const anchors = [...html.matchAll(/href="#([^"]+)"/g)].map((match) => match[1]);
  const cards = [...html.matchAll(/<article[^>]*id="([^"]+)"/g)].map((match) => match[1]);
  assert.ok(anchors.every((id) => cards.includes(id)), 'Each citation points to its own source card');
});

test('inline code, fenced code, and Markdown link labels are not rewritten as citations', () => {
  const html = render('`items[1]`\n\n```js\nitems[2]\n```\n\n[Read [1]](https://example.com)');
  assert.doesNotMatch(html, /aria-label="Show source/);
  assert.match(html, /href="https:\/\/example.com"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, /Read \[1\]/);
});

test('model output cannot inject raw HTML, unsafe links, or remote images', () => {
  const html = render('<script>alert(1)</script>\n\n[Bad](javascript:alert%281%29)\n\n![Image description](https://example.com/pixel.png)');
  assert.doesNotMatch(html, /<script|<img|href="javascript:/i);
  assert.match(html, /Image description/);
  assert.match(html, /Original PDF passage\./);
});

test('separate answers have distinct citation targets and escape original source passages', () => {
  const unsafeSource = { ...sources[0], passage: '<script>source text</script>' };
  const html = renderToStaticMarkup(<div>
    <CitedAnswer content="First [1]." sources={[unsafeSource]} />
    <CitedAnswer content="Second [1]." sources={sources.slice(0, 1)} />
  </div>);
  const targets = [...html.matchAll(/href="#([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(targets).size, 2);
  assert.match(html, /&lt;script&gt;source text&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});
