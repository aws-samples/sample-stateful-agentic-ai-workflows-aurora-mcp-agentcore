/**
 * TravelerIntentCard — left column on the Demo Stage.
 *
 * Encodes the human side of the keynote thesis: a real traveler, real
 * preferences, and the prompt that triggered the agent. Highlights when a
 * memory span is active.
 */
import { useState } from 'react';
import type { StageTraveler } from '../types';
import { ALEX_IMAGE_URL, ALEX_NAME } from '../../showcase/lib/personas';

// "Build on AWS" booth mark. Drop the sticker art at this path; if it's
// missing we fall back to a clean text wordmark so the footer never breaks.
const BUILD_ON_AWS_SRC = '/kiosk/build-on-aws.png';

interface TravelerIntentCardProps {
  traveler: StageTraveler;
  prompt: string;
  memoryActive: boolean;
}

export function TravelerIntentCard({ traveler, prompt, memoryActive }: TravelerIntentCardProps) {
  const [awsMarkMissing, setAwsMarkMissing] = useState(false);
  return (
    <aside className="ds-panel" aria-label="Traveler intent">
      <div className="ds-panel-head">
        <b>Traveler intent</b>
        <span>Aurora memory</span>
      </div>
      <div className="ds-panel-body ds-traveler">
        <div className="ds-traveler-prompt" aria-label="Traveler prompt">
          <div className="ds-traveler-prompt-label">Prompt · natural language</div>
          <div className="ds-traveler-prompt-text">
            <em>{prompt}</em>
          </div>
        </div>

        <div className="ds-traveler-profile">
          <div className="ds-traveler-avatar is-photo" aria-hidden="true">
            <img src={ALEX_IMAGE_URL} alt={ALEX_NAME} loading="lazy" />
          </div>
          <div>
            <div className="ds-traveler-name">{traveler.name}</div>
            <div className="ds-traveler-id">{traveler.id}</div>
          </div>
        </div>

        <div className={`ds-fact-section${memoryActive ? ' is-active' : ''}`}>
          <div className="ds-fact-label">
            <span>Memory facts</span>
            <b>{traveler.facts.length} grounded</b>
          </div>
          <div className="ds-fact-tags">
            {traveler.facts.map((f) => (
              <span className="ds-fact-tag" key={f}>{f}</span>
            ))}
          </div>
        </div>

        <div className="ds-traveler-meta">
          <div className="ds-meta-tile">
            <span>Home</span>
            <b>{traveler.origin}</b>
          </div>
          <div className="ds-meta-tile">
            <span>Cap</span>
            <b>${traveler.budgetCapUsd.toLocaleString()}</b>
          </div>
        </div>

        {/* Booth brand footer — anchors the left pane with the AWS mark. */}
        <div className="ds-aws-mark" aria-label="Build on AWS">
          {awsMarkMissing ? (
            <span className="ds-aws-mark-text">
              Build on <b>aws</b>
            </span>
          ) : (
            <img
              src={BUILD_ON_AWS_SRC}
              alt="Build on AWS"
              loading="lazy"
              onError={() => setAwsMarkMissing(true)}
            />
          )}
        </div>
      </div>
    </aside>
  );
}
