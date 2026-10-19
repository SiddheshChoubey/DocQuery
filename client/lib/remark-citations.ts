// Markdown nodes contain text or child nodes. Only plain text gets citations;
// existing links and code must keep their original meaning.
interface MarkdownNode {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
}

export default function remarkCitations() {
  return (tree: MarkdownNode) => {
    function walk(node: MarkdownNode) {
      if (!node.children || ['link', 'linkReference', 'code', 'inlineCode'].includes(node.type)) return;

      node.children = node.children.flatMap((child) => {
        if (child.type !== 'text' || !child.value) {
          walk(child);
          return [child];
        }
        const parts: MarkdownNode[] = [];
        let offset = 0;
        for (const match of child.value.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
          if (match.index > offset) parts.push({ type: 'text', value: child.value.slice(offset, match.index) });
          for (const number of match[1].split(',')) {
            const id = Number(number.trim());
            parts.push({ type: 'link', url: `#docquery-citation-${id}`, children: [{ type: 'text', value: `[${id}]` }] });
          }
          offset = match.index + match[0].length;
        }
        if (!parts.length) return [child];
        if (offset < child.value.length) parts.push({ type: 'text', value: child.value.slice(offset) });
        return parts;
      });
    }
    walk(tree);
  };
}
