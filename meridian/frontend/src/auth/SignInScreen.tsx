import { useEffect, useRef } from 'react';
import { ArrowRight, LockKeyhole, Plane } from 'lucide-react';
import { MeridianMark } from '../components/MeridianMark';
import '../showcase/signIn.css';

interface SignInScreenProps {
  /** Why the person is back here when it was not their choice. */
  message?: string | null;
  /** Sign-in is in progress, so the button waits. */
  busy?: boolean;
  onSignIn?: () => void;
}

export function SignInScreen({ message = null, busy = false, onSignIn }: SignInScreenProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (message) heading.current?.focus();
  }, [message]);

  return (
    <main className="mds-signin mds-signin--welcome" aria-labelledby="signin-title">
      <div className="mds-signin-shell">
        <header className="mds-signin-brand">
          <MeridianMark variant="stage" />
          <span>Meridian</span>
        </header>
        <section className="mds-signin-welcome" aria-labelledby="signin-title">
          <h1 id="signin-title" ref={heading} tabIndex={-1}>Sign in to Meridian</h1>
          <p className="mds-signin-intro">
            Your trips, preferences and holds belong to you.
            Sign in so Meridian knows who is asking.
          </p>
          {message && <p className="mds-signin-message" role="alert">{message}</p>}
          <p role="status" className="mds-visually-hidden">{busy ? 'Signing you in' : ''}</p>
          <button type="button" className="mds-signin-button" onClick={onSignIn} disabled={busy}>
            {busy ? 'Signing you in' : 'Sign in'}
            <ArrowRight size={20} aria-hidden="true" />
          </button>
          <p className="mds-signin-assurance"><LockKeyhole size={16} aria-hidden="true" />Continue to secure sign-in</p>
          <div className="mds-signin-departure">
            <Plane size={22} aria-hidden="true" />
            <p>Your journey, all in one place.<span>Trips, preferences and saved plans.</span></p>
          </div>
        </section>
        <figure className="mds-signin-destination" data-theme="dark">
          <img src="/travel/recovery-flight.jpg" alt="An aircraft approaching the runway at sunset" width={1920} height={1168} />
          <figcaption>
            <p>Plan. Fly. Land.</p>
            <span>A little more certainty for the journey ahead.</span>
            <a href="https://commons.wikimedia.org/wiki/File:ANA_777_landing_at_NRT_(7185678977)_(2).jpg" target="_blank" rel="noreferrer">Photo: Angelo DeSantis, CC BY 2.0</a>
          </figcaption>
        </figure>
      </div>
    </main>
  );
}
