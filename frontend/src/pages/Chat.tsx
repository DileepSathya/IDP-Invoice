import React, { useEffect, useRef, useState } from "react";
import {
  chat,
  fetchChatSession,
  fetchChatSuggestions,
  getChatSessionId,
  setChatSessionId,
  type ChatMessage,
} from "../api";

const FALLBACK_SUGGESTIONS = [
  "How many invoices were processed today?",
  "How many invoices in the past 2 days and what's their sum?",
  "Show invoices pending HITL review",
];

function formatMessageTime(timestamp?: string | null): string {
  if (!timestamp) return "";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export const Chat: React.FC = () => {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>(FALLBACK_SUGGESTIONS);
  const [sessionId, setSessionId] = useState<string | null>(getChatSessionId());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      try {
        const loadedSuggestions = await fetchChatSuggestions();
        if (!cancelled && loadedSuggestions.length > 0) {
          setSuggestions(loadedSuggestions);
        }
      } catch {
        /* keep fallback suggestions */
      }

      const existingSession = getChatSessionId();
      if (!existingSession || cancelled) return;

      try {
        const session = await fetchChatSession(existingSession);
        if (!cancelled && Array.isArray(session.messages)) {
          setMessages(session.messages);
          setSessionId(session.session_id);
        }
      } catch {
        setChatSessionId("");
        setSessionId(null);
      }
    };

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const node = messagesRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [messages, loading, error]);

  const askQuestion = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    const optimisticUser: ChatMessage = {
      role: "user",
      content: trimmed,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, optimisticUser]);
    setDraft("");
    setLoading(true);
    setError(null);

    try {
      const res = await chat(trimmed, sessionId);
      setSessionId(res.session_id);
      setMessages(res.messages ?? []);
      if (res.suggestions?.length) {
        setSuggestions(res.suggestions);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chat failed");
      setMessages((prev) => {
        if (
          prev.length > 0 &&
          prev[prev.length - 1]?.role === "user" &&
          prev[prev.length - 1]?.content === trimmed
        ) {
          return prev.slice(0, -1);
        }
        return prev;
      });
      setDraft(trimmed);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleSubmit = async (event?: React.FormEvent) => {
    event?.preventDefault();
    await askQuestion(draft);
  };

  const handleComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  const handleNewSession = () => {
    setChatSessionId("");
    setSessionId(null);
    setMessages([]);
    setDraft("");
    setError(null);
    inputRef.current?.focus();
  };

  const showSuggestions = messages.length === 0 && !loading;

  return (
    <div className="chat-page">
      <header className="chat-toolbar">
        <div className="chat-toolbar-title">
          <h2>Invoice Assistant</h2>
          <span className="chat-toolbar-subtitle">Searches your full invoice database</span>
        </div>
        <button type="button" className="chat-toolbar-action" onClick={handleNewSession}>
          New chat
        </button>
      </header>

      <div className="chat-messages" ref={messagesRef} aria-live="polite">
        {showSuggestions && (
          <div className="chat-message chat-message--assistant">
            <div className="chat-message-bubble">
              <p className="chat-message-text">
                Ask about invoice counts, date ranges, totals, HITL queue, or search across every
                stored invoice.
              </p>
            </div>
          </div>
        )}

        {messages.map((msg, index) => {
          const isUser = msg.role === "user";
          return (
            <div
              key={`${msg.timestamp ?? "msg"}-${index}`}
              className={`chat-message chat-message--${isUser ? "user" : "assistant"}`}
            >
              <div className="chat-message-bubble">
                {isUser ? (
                  <pre className="chat-message-text">{msg.content}</pre>
                ) : msg.content.trimStart().startsWith("<") ? (
                  <div
                    className="chat-message-text"
                    // eslint-disable-next-line react/no-danger
                    dangerouslySetInnerHTML={{ __html: msg.content }}
                  />
                ) : (
                  <pre className="chat-message-text">{msg.content}</pre>
                )}
                {msg.timestamp && (
                  <time className="chat-message-time">{formatMessageTime(msg.timestamp)}</time>
                )}
              </div>
            </div>
          );
        })}

        {loading && (
          <div className="chat-message chat-message--assistant">
            <div className="chat-message-bubble chat-message-bubble--typing">
              <span className="chat-typing-dots" aria-label="Assistant is typing">
                <span />
                <span />
                <span />
              </span>
            </div>
          </div>
        )}

        {error && (
          <div className="chat-message chat-message--assistant">
            <div className="chat-message-bubble chat-message-bubble--error">
              <p className="chat-message-text">{error}</p>
            </div>
          </div>
        )}
      </div>

      <footer className="chat-composer">
        {showSuggestions && (
          <div className="chat-suggestions">
            {suggestions.slice(0, 4).map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="chat-suggestion-chip"
                disabled={loading}
                onClick={() => void askQuestion(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}

        <form className="chat-composer-form" onSubmit={handleSubmit}>
          <textarea
            ref={inputRef}
            className="chat-composer-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder="Message Invoice Assistant…"
            rows={1}
            disabled={loading}
            aria-label="Chat message"
          />
          <button
            type="submit"
            className="chat-composer-send"
            disabled={loading || !draft.trim()}
            aria-label="Send message"
          >
            Send
          </button>
        </form>
      </footer>
    </div>
  );
};
