import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, ExternalLink } from 'lucide-react';
import { AuroraIcon } from '../components/ServiceMark';

export function SessionClose({ onEvidence, onConcierge }: {
  onEvidence: () => void;
  onConcierge: () => void;
}) {
  const [questions, setQuestions] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [questions]);

  return <section className={`mc-session-close${questions ? ' is-questions' : ''}`} aria-label={questions ? 'Questions and discussion' : 'Session takeaways'}>
    <div className="mc-session-close-main">
      <h1 ref={heading} tabIndex={-1}>{questions ? 'Where would you use this?' : 'Keep the work.\nContinue the journey.'}</h1>
      <p className="mc-session-lead">{questions ? 'Questions, tradeoffs, and the workflows you’re building.' : 'Aurora brings search, traveler context, and workflow progress into one database.'}</p>
      {questions ? <p className="mc-session-discussion">What needs to be remembered? Who may access it? What happens when a worker stops?</p> : <dl className="mc-session-patterns">
        <div><dt>Find the right context.</dt><dd>Combine pgvector and full-text search. Rerank the results.</dd></div>
        <div><dt>Check access before use.</dt><dd>Authorize the traveler scope, apply RLS, and record the decision.</dd></div>
        <div><dt>Save enough to continue.</dt><dd>Checkpoint the next step. Use a worker lease and stable hold request ID on retry.</dd></div>
      </dl>}
      <div className="mc-session-actions">
        {questions ? <button type="button" className="mc-session-primary" onClick={onEvidence}>Explore the live evidence <ArrowRight size={18} aria-hidden="true" /></button> : <button type="button" className="mc-session-primary" onClick={() => setQuestions(true)}>Open for questions <ArrowRight size={18} aria-hidden="true" /></button>}
        <button type="button" className="mc-session-link" onClick={questions ? () => setQuestions(false) : onEvidence}><ArrowLeft size={17} aria-hidden="true" />{questions ? 'Back to takeaways' : 'Back to evidence'}</button>
      </div>
    </div>
    <footer className="mc-session-close-foot">
      <span><AuroraIcon size={26} aria-hidden="true" />Aurora PostgreSQL · MCP · Amazon Bedrock AgentCore · Strands · LangGraph</span>
      <div><a href="https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore" target="_blank" rel="noreferrer">Build from the sample <ExternalLink size={16} aria-hidden="true" /></a><button type="button" className="mc-session-link" onClick={onConcierge}>Return to Meridian <ArrowRight size={17} aria-hidden="true" /></button></div>
    </footer>
  </section>;
}
