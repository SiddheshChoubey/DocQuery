import { ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings } from '@langchain/google-genai';

function apiKey() {
  const key = process.env.GEMINI_API_KEY?.trim();
  if (!key) throw new Error('Set GEMINI_API_KEY in server/.env before starting.');
  return key;
}

// Embedding 2 uses text prefixes instead of the older taskType parameter.
// Send individual requests so each chunk has its own vector and failures surface.
export class PdfEmbeddings extends GoogleGenerativeAIEmbeddings {
  constructor(onProgress) {
    super({
      apiKey: apiKey(),
      model: 'gemini-embedding-2',
      maxConcurrency: 1,
      maxRetries: 2,
    });
    this.onProgress = onProgress;
  }

  embedQuery(text) {
    return super.embedQuery(`task: question answering | query: ${text}`);
  }

  async embedDocuments(texts) {
    const vectors = [];
    for (const text of texts) {
      vectors.push(await super.embedQuery(`title: none | text: ${text}`));
      await this.onProgress?.(vectors.length, texts.length);
    }
    return vectors;
  }
}

export function createChatModel() {
  return new ChatGoogleGenerativeAI({
    apiKey: apiKey(),
    model: process.env.GEMINI_CHAT_MODEL || 'gemini-3.5-flash-lite',
    maxRetries: 2,
  });
}

export const vectorStoreConfig = {
  url: 'http://localhost:6333',
  collectionName: 'pdf-rag-gemini-embedding-2',
};
