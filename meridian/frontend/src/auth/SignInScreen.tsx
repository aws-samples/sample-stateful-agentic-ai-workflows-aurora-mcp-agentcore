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
  return (
    <main className="mds-signin" aria-labelledby="signin-title">
      <div className="mds-signin-card">
        <MeridianMark variant="stage" />
        <h1 id="signin-title">Sign in to Meridian</h1>
        <p>
          Your trips, preferences and holds belong to you.
          Sign in so Meridian knows who is asking.
        </p>
        {message && <p className="mds-signin-message" role="alert">{message}</p>}
        <button type="button" className="mds-signin-button" onClick={onSignIn} disabled={busy}>
          {busy ? 'Signing you in' : 'Sign in'}
        </button>
      </div>
    </main>
  );
}
