import { GlobalWindow } from 'happy-dom';

// Browser APIs for local React interaction tests. No real browser/network is used.
export const browser = new GlobalWindow({ url: 'http://localhost:3000' });
Object.assign(globalThis, {
  window: browser,
  document: browser.document,
  HTMLElement: browser.HTMLElement,
  Node: browser.Node,
  MutationObserver: browser.MutationObserver,
  localStorage: browser.localStorage,
  getComputedStyle: browser.getComputedStyle.bind(browser),
  requestAnimationFrame: browser.requestAnimationFrame.bind(browser),
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: browser.navigator });
