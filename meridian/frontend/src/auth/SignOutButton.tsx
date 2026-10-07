import { useSession } from './SessionContext';
import '../showcase/signIn.css';

/** Ends the session. It renders nothing when this build has no sign-in of its own. */
export function SignOutButton() {
  const { signOut } = useSession();
  return signOut
    ? <button type="button" className="mds-signout" onClick={signOut}>Sign out</button>
    : null;
}
