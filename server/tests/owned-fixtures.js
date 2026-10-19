import { ConversationStore as Store } from '../conversations.js';
import { ChatService as Service } from '../chat-service.js';

// Existing behavior tests use one explicit synthetic owner. Production requires one.
export class ConversationStore extends Store {
  create(doc, owner = 'user_test') { return super.create(doc, owner); }
  get(id, doc, owner = 'user_test') { return super.get(id, doc, owner); }
  list(doc, owner = 'user_test') { return super.list(doc, owner); }
  beginTurn(id, doc, input, owner = 'user_test') { return super.beginTurn(id, doc, input, owner); }
  remove(id, doc, owner = 'user_test') { return super.remove(id, doc, owner); }
  removeDocument(doc, owner = 'user_test') { return super.removeDocument(doc, owner); }
}
export class ChatService extends Service {
  send(id, doc, input, signal, emit, owner = 'user_test') { return super.send(id, doc, input, signal, emit, owner); }
  stop(id, doc, owner = 'user_test') { return super.stop(id, doc, owner); }
}
