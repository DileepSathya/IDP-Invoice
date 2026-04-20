import React, { useState } from "react";
import { chat } from "../api";

export const Chat: React.FC = () => {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!question.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const res = await chat(question.trim());
      setAnswer(res.answer);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chat failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="panel">
      <h2>Invoice Knowledge Chatbot</h2>
      <p>
        Ask questions over the invoices stored in MongoDB. The backend uses
        LlamaIndex to query OCR and structured JSON.
      </p>

      <form className="chat-form" onSubmit={handleSubmit}>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Example: Show me invoices with HSN 9983 in the last month."
          rows={4}
        />
        <button type="submit" disabled={loading}>
          {loading ? "Thinking…" : "Ask"}
        </button>
      </form>

      {error && <div className="alert alert-error">{error}</div>}

      {answer && (
        <section className="chat-answer">
          <h3>Answer</h3>
          <pre>{answer}</pre>
        </section>
      )}
    </div>
  );
};

