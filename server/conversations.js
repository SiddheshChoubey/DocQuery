import { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export function chatError(status, message) {
  return Object.assign(new Error(message), { status, publicMessage: message });
}

export function conversationContext(turns) {
  return turns.filter((turn) => turn.status === 'completed').slice(-6).flatMap((turn) => [
    { role: 'user', content: turn.question.slice(0, 2000) },
    { role: 'assistant', content: turn.answer.slice(0, 2000) },
  ]);
}

export function contextualQuery(question, history) {
  if (!history.length) return question;
  const recent = history.slice(-2).map((message) => `${message.role}: ${message.content}`).join('\n');
  return `Recent conversation for context:\n${recent}\n\nCurrent question: ${question}`;
}

export class ConversationStore {
  constructor(filename = fileURLToPath(new URL('./data/conversations.sqlite', import.meta.url))) {
    if (filename !== ':memory:') mkdirSync(path.dirname(filename), { recursive: true });
    this.db = new DatabaseSync(filename);
    this.db.exec(`
      PRAGMA foreign_keys = ON;
      PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, documentId TEXT NOT NULL, title TEXT NOT NULL,
        createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY, conversationId TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]',
        citationNote TEXT, error TEXT, createdAt TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed', 'stopped'))
      );
      CREATE INDEX IF NOT EXISTS conversations_document ON conversations(documentId);
      CREATE INDEX IF NOT EXISTS turns_conversation ON turns(conversationId);
    `);
    // Additive migration: existing questions keep the original balanced behavior.
    if (!this.db.prepare('PRAGMA table_info(turns)').all().some((column) => column.name === 'answerMode')) {
      this.db.exec("ALTER TABLE turns ADD COLUMN answerMode TEXT NOT NULL DEFAULT 'balanced' CHECK(answerMode IN ('balanced', 'detailed'))");
    }
    // One API process owns this store. A restart cannot resume an old provider call.
    this.db.prepare("UPDATE turns SET status = 'stopped', error = ? WHERE status = 'pending'")
      .run('The server restarted before this answer finished. Retry the question.');
  }

  close() { this.db.close(); }

  list(documentId) {
    return this.db.prepare('SELECT * FROM conversations WHERE documentId = ? ORDER BY updatedAt DESC, rowid DESC').all(documentId);
  }

  create(documentId) {
    const id = randomUUID();
    const now = new Date().toISOString();
    this.db.prepare('INSERT INTO conversations VALUES (?, ?, ?, ?, ?)').run(id, documentId, 'New chat', now, now);
    return this.get(id, documentId);
  }

  get(id, documentId) {
    const conversation = this.db.prepare('SELECT * FROM conversations WHERE id = ? AND documentId = ?').get(id, documentId);
    if (!conversation) throw chatError(404, 'Conversation not found for this document.');
    const turns = this.db.prepare('SELECT * FROM turns WHERE conversationId = ? ORDER BY rowid').all(id)
      .map((turn) => ({ ...turn, sources: JSON.parse(turn.sources) }));
    return { ...conversation, turns };
  }

  beginTurn(id, documentId, { turnId, question, retry = false, answerMode = 'balanced' }) {
    if (!['balanced', 'detailed'].includes(answerMode)) throw chatError(400, 'Choose Balanced or Detailed answer mode.');
    const conversation = this.get(id, documentId);
    if (conversation.turns.some((turn) => turn.status === 'pending')) throw chatError(409, 'An answer is already being generated in this chat.');
    if (retry) {
      const last = conversation.turns.at(-1);
      if (!last || last.id !== turnId || !['failed', 'stopped'].includes(last.status)) {
        throw chatError(409, 'Only the latest failed or stopped question can be retried.');
      }
      this.db.prepare("UPDATE turns SET status = 'pending', error = NULL, answer = '', sources = '[]', citationNote = NULL WHERE id = ?").run(turnId);
    } else {
      if (typeof question !== 'string' || !question.trim() || question.length > 4000) {
        throw chatError(400, 'Enter a question of at most 4,000 characters.');
      }
      if (typeof turnId !== 'string' || !/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(turnId)) throw chatError(400, 'Invalid message ID.');
      if (this.db.prepare('SELECT id FROM turns WHERE id = ?').get(turnId)) throw chatError(409, 'This message was already submitted. Reopen the chat to check its status.');
      if (conversation.turns.length >= 200) throw chatError(400, 'This conversation has 200 questions. Start a new chat.');
      this.db.prepare("INSERT INTO turns (id, conversationId, question, createdAt, status, answerMode) VALUES (?, ?, ?, ?, 'pending', ?)")
        .run(turnId, id, question.trim(), new Date().toISOString(), answerMode);
    }
    const title = conversation.turns.length ? conversation.title : question.trim().replace(/\s+/g, ' ').slice(0, 80);
    this.db.prepare('UPDATE conversations SET title = ?, updatedAt = ? WHERE id = ?').run(title, new Date().toISOString(), id);
    return this.get(id, documentId).turns.at(-1);
  }

  finishTurn(turnId, { status, answer = '', sources = [], citationNote = null, error = null }) {
    this.db.prepare("UPDATE turns SET status = ?, answer = ?, sources = ?, citationNote = ?, error = ? WHERE id = ? AND status = 'pending'")
      .run(status, answer, JSON.stringify(sources), citationNote, error, turnId);
    this.db.prepare('UPDATE conversations SET updatedAt = ? WHERE id = (SELECT conversationId FROM turns WHERE id = ?)')
      .run(new Date().toISOString(), turnId);
  }

  remove(id, documentId) {
    const conversation = this.get(id, documentId);
    if (conversation.turns.some((turn) => turn.status === 'pending')) throw chatError(409, 'Stop the active answer before deleting this chat.');
    this.db.prepare('DELETE FROM conversations WHERE id = ? AND documentId = ?').run(id, documentId);
  }

  removeDocument(documentId) {
    this.db.prepare('DELETE FROM conversations WHERE documentId = ?').run(documentId);
  }
}
