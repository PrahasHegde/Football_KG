import {
  ArrowUp,
  Brain,
  ChevronRight,
  Database,
  Eye,
  Loader2,
  RotateCcw,
  Route,
  Search,
  Sparkles,
  Square,
  TriangleAlert,
  UserRound,
} from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import Markdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, streamChat } from "../api";
import type { AgentEvent, ChatMessage, ChatPart, Meta } from "../types";

const SUGGESTIONS = [
  "Who are the top 10 scorers at FIFA World Cups?",
  "How has Brazil fared against Argentina over the decades?",
  "How are Pelé and Lionel Messi connected through matches they scored in?",
  "Which teams have won the most penalty shootouts?",
  "What are the biggest wins in World Cup history?",
  "Which country has hosted the most international matches?",
];

const TOOL_META: Record<string, { label: string; icon: typeof Search }> = {
  search_entities: { label: "Search", icon: Search },
  get_entity: { label: "Look up", icon: UserRound },
  run_sql: { label: "Query", icon: Database },
  find_path: { label: "Find path", icon: Route },
  show_in_graph: { label: "Draw graph", icon: Eye },
};

interface Props {
  meta: Meta | null;
  onGraph: (e: Extract<AgentEvent, { type: "graph" }>) => void;
  onEntity: (id: string) => void;
}

const uid = () => Math.random().toString(36).slice(2, 10);

export function ChatPanel({ meta, onGraph, onEntity }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversation = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const update = (id: string, fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)));

  const appendPart = (m: ChatMessage, kind: "text" | "thinking", delta: string): ChatMessage => {
    const parts = [...m.parts];
    const last = parts[parts.length - 1];
    if (last && last.kind === kind) parts[parts.length - 1] = { ...last, text: last.text + delta };
    else parts.push({ kind, text: delta });
    return { ...m, parts };
  };

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const aid = uid();
    setMessages((ms) => [
      ...ms,
      { id: uid(), role: "user", text: q, parts: [] },
      { id: aid, role: "assistant", parts: [], pending: true },
    ]);
    setInput("");
    setBusy(true);
    stick.current = true;
    const ctrl = new AbortController();
    abort.current = ctrl;

    const onEvent = (e: AgentEvent) => {
      switch (e.type) {
        case "conversation":
          conversation.current = e.id;
          break;
        case "text":
        case "thinking":
          update(aid, (m) => appendPart(m, e.type, e.delta));
          break;
        case "tool_start":
          update(aid, (m) => ({
            ...m,
            parts: [...m.parts, { kind: "tool", id: e.id, name: e.name, input: e.input, status: "running" }],
          }));
          break;
        case "tool_result":
          update(aid, (m) => ({
            ...m,
            parts: m.parts.map((p) =>
              p.kind === "tool" && p.id === e.id
                ? { ...p, status: e.ok ? "ok" : "error", summary: e.summary, result: e.result }
                : p,
            ),
          }));
          break;
        case "graph":
          onGraph(e);
          update(aid, (m) => ({ ...m, parts: [...m.parts, { kind: "graph", caption: e.caption, nodes: e.nodes.length }] }));
          break;
        case "error":
          update(aid, (m) => ({ ...m, error: e.message }));
          break;
        case "done":
          break;
      }
    };

    try {
      await streamChat({ message: q, conversation_id: conversation.current }, onEvent, ctrl.signal);
    } catch (err) {
      if (!ctrl.signal.aborted) update(aid, (m) => ({ ...m, error: String((err as Error).message ?? err) }));
    } finally {
      update(aid, (m) => ({ ...m, pending: false }));
      setBusy(false);
      abort.current = null;
    }
  }

  const reset = () => {
    abort.current?.abort();
    if (conversation.current) api.resetChat(conversation.current);
    conversation.current = null;
    setMessages([]);
  };

  return (
    <div className="chat">
      <header className="chat__head">
        <div>
          <p className="muted small">
            <Sparkles size={12} className="chat__spark" />
            {meta ? `${meta.agent.provider ?? "LLM"} · ${meta.agent.model}` : "…"}
            {meta && !meta.agent.api_key_set && <span className="warn"> · API key missing</span>}
          </p>
        </div>
        {messages.length > 0 && (
          <button className="icon-btn" onClick={reset} title="New conversation" aria-label="New conversation">
            <RotateCcw size={15} />
          </button>
        )}
      </header>

      <div
        className="chat__scroll"
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}
      >
        {messages.length === 0 ? (
          <div className="chat__empty">
            <p>
              Ask anything about 150 years of international football. The agent searches the graph, writes SQL, finds
              paths and draws its answers on the canvas.
            </p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="msg msg--user">
                {m.text}
              </div>
            ) : (
              <AssistantMessage key={m.id} m={m} onEntity={onEntity} />
            ),
          )
        )}
      </div>

      <form
        className="chat__input"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <textarea
          value={input}
          rows={1}
          placeholder="Ask about teams, players, matches…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
        />
        {busy ? (
          <button type="button" className="send" onClick={() => abort.current?.abort()} aria-label="Stop">
            <Square size={14} />
          </button>
        ) : (
          <button type="submit" className="send" disabled={!input.trim()} aria-label="Send">
            <ArrowUp size={16} />
          </button>
        )}
      </form>
    </div>
  );
}

const AssistantMessage = memo(function AssistantMessage({ m, onEntity }: { m: ChatMessage; onEntity: (id: string) => void }) {
  const hasText = m.parts.some((p) => p.kind === "text" && p.text.trim());
  return (
    <div className="msg msg--assistant">
      {m.parts.map((p, i) => (
        <Part key={i} p={p} onEntity={onEntity} />
      ))}
      {m.pending && !hasText && (
        <div className="working">
          <Loader2 size={14} className="spin" /> Working…
        </div>
      )}
      {m.error && (
        <div className="msg__error">
          <TriangleAlert size={14} /> {m.error}
        </div>
      )}
    </div>
  );
});

/** Some models paste graph-tool JSON into their prose; the canvas already shows it, so hide it. */
function stripToolJson(text: string) {
  return text.replace(/```[a-z]*\s*\{[^`]*?"node_ids"[\s\S]*?(```|$)/g, "").trimEnd();
}

function Part({ p, onEntity }: { p: ChatPart; onEntity: (id: string) => void }) {
  if (p.kind === "text") {
    return (
      <div className="md">
        <Markdown
          remarkPlugins={[remarkGfm]}
          urlTransform={(url) => (url.startsWith("entity:") ? url : defaultUrlTransform(url))}
          components={{
            a: ({ href, children }) =>
              href?.startsWith("entity:") ? (
                <button className="entity-link" onClick={() => onEntity(decodeURIComponent(href.slice(7)))}>
                  {children}
                </button>
              ) : (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              ),
          }}
        >
          {stripToolJson(p.text)}
        </Markdown>
      </div>
    );
  }
  if (p.kind === "thinking") {
    return (
      <details className="step step--thinking">
        <summary>
          <Brain size={13} /> Reasoning <ChevronRight size={13} className="chev" />
        </summary>
        <p>{p.text}</p>
      </details>
    );
  }
  if (p.kind === "graph") {
    return (
      <div className="step step--graph">
        <Eye size={13} /> Drew <b>{p.caption}</b> ({p.nodes} nodes)
      </div>
    );
  }
  const meta = TOOL_META[p.name] ?? { label: p.name, icon: Database };
  const Icon = meta.icon;
  const detail = p.name === "run_sql" ? String(p.input.sql ?? "") : JSON.stringify(p.input, null, 1);
  return (
    <details className={`step step--${p.status}`}>
      <summary>
        {p.status === "running" ? <Loader2 size={13} className="spin" /> : <Icon size={13} />}
        <span className="step__label">{meta.label}</span>
        <span className="step__summary">{p.summary ?? "running…"}</span>
        <ChevronRight size={13} className="chev" />
      </summary>
      <pre className="code">{detail}</pre>
      {p.result && p.result.rows.length > 0 && (
        <div className="result-table">
          <table>
            <thead>
              <tr>{p.result.columns.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {p.result.rows.slice(0, 15).map((r, i) => (
                <tr key={i}>{r.map((v, j) => <td key={j}>{v === null ? "–" : String(v)}</td>)}</tr>
              ))}
            </tbody>
          </table>
          {p.result.rows.length > 15 && <p className="muted small">+{p.result.rows.length - 15} more rows</p>}
        </div>
      )}
    </details>
  );
}
