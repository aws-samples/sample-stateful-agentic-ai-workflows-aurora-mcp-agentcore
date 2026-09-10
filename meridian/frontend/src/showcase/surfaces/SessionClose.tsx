import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, ExternalLink, Workflow } from 'lucide-react';
import { ServiceMark } from '../components/ServiceMark';

export function SessionClose({ onEvidence, onConcierge }: {
  onEvidence: () => void;
  onConcierge: () => void;
}) {
  const [questions, setQuestions] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  // Reveal the new heading when a long takeaway page becomes a shorter Q&A page.
  useEffect(() => { heading.current?.focus(); }, [questions]);

  return <section className={`mc-session-close${questions ? ' is-questions' : ''}`} aria-label={questions ? 'Questions and discussion' : 'Session takeaways'}>
    <div className="mc-session-close-main">
      <h1 ref={heading} tabIndex={-1}>{questions ? 'What would you build\nfor your customers?' : 'A canceled flight.\nA clear way forward.'}</h1>
      <p className="mc-session-lead">{questions ? 'Your questions on Aurora, AgentCore, and bringing these patterns to your application.' : 'Help Alex find relevant alternatives, carry preferences forward, and hold a package while deciding.'}</p>
      {questions ? <p className="mc-session-discussion">From disrupted trips to delayed orders, which customer journey needs a better way forward?</p> : <dl className="mc-session-patterns">
        <div>
          <dt><ServiceMark name="aurora" size={36} /><span>Amazon Aurora</span></dt>
          <dd><strong>Find the options. Keep the plan.</strong><p>One PostgreSQL database for hybrid search, traveler context, checkpoints, and holds.</p></dd>
        </div>
        <div>
          <dt><ServiceMark name="agentcore" size={36} /><span>Amazon Bedrock AgentCore</span></dt>
          <dd><strong>Run the concierge. Carry context forward.</strong><p>Managed runtime, session memory, and workload identity. Traveler grants and RLS stay in Aurora.</p></dd>
        </div>
        <div>
          <dt><Workflow size={30} aria-hidden="true" /><span>MCP + LangGraph</span></dt>
          <dd><strong>Resume from the saved step.</strong><p>MCP connects reusable tools. LangGraph resumes Aurora checkpoints; stable request IDs protect hold retries.</p></dd>
        </div>
      </dl>}
      <div className="mc-session-actions">
        {questions ? <button type="button" className="mc-session-primary" onClick={onEvidence}>Explore the live evidence <ArrowRight size={18} aria-hidden="true" /></button> : <button type="button" className="mc-session-primary" onClick={() => setQuestions(true)}>Open for questions <ArrowRight size={18} aria-hidden="true" /></button>}
        <button type="button" className="mc-session-link" onClick={questions ? () => setQuestions(false) : onEvidence}><ArrowLeft size={17} aria-hidden="true" />{questions ? 'Back to takeaways' : 'Back to evidence'}</button>
      </div>
    </div>
    <footer className="mc-session-close-foot">
      <div><a href="https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore" target="_blank" rel="noreferrer">Build from the sample <ExternalLink size={16} aria-hidden="true" /></a><button type="button" className="mc-session-link" onClick={onConcierge}>Return to Meridian <ArrowRight size={17} aria-hidden="true" /></button></div>
    </footer>
  </section>;
}
