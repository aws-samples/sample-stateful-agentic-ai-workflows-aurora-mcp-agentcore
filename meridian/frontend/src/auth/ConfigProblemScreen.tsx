import '../showcase/signIn.css';

export function ConfigProblemScreen() {
  return (
    <main className="mds-signin" aria-labelledby="signin-title">
      <div className="mds-signin-card">
        <h1 id="signin-title">Sign-in is not configured correctly</h1>
        <p>
          Meridian cannot offer sign-in right now. Ask the person who runs this site to check
          its sign-in settings.
        </p>
      </div>
    </main>
  );
}
