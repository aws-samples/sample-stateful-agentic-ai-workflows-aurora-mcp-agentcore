import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Eye, EyeOff } from 'lucide-react';

interface CredentialFormProps {
  busy: boolean;
  onSubmit: (email: string, password: string) => void | Promise<void>;
  /** The id of the visible error, so both inputs can point at it. */
  errorId?: string;
}

export function CredentialForm({ busy, onSubmit, errorId }: CredentialFormProps) {
  const emailInput = useRef<HTMLInputElement>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [visible, setVisible] = useState(false);
  useEffect(() => { emailInput.current?.focus(); }, []);
  const errorProps = errorId ? { 'aria-invalid': true, 'aria-describedby': errorId } : {};

  return (
    <form className="mds-signin-form" onSubmit={event => {
      event.preventDefault();
      if (busy) return;
      const submitted = password;
      setPassword('');
      setVisible(false);
      void onSubmit(email, submitted);
    }}>
      <label className="mds-signin-field" htmlFor="signin-email">
        Email
        <input ref={emailInput} id="signin-email" name="username" type="email"
          autoComplete="username" autoCapitalize="none" spellCheck={false} required
          value={email} onChange={event => setEmail(event.target.value)} disabled={busy}
          {...errorProps} />
      </label>
      <div className="mds-signin-field">
        <label htmlFor="signin-password">Password</label>
        <div className="mds-signin-password">
          <input id="signin-password" name="password" type={visible ? 'text' : 'password'}
            autoComplete="current-password" required value={password}
            onChange={event => setPassword(event.target.value)} disabled={busy}
            {...errorProps} />
          <button type="button" className="mds-signin-reveal" disabled={busy}
            aria-label="Show password" aria-pressed={visible}
            onClick={() => setVisible(!visible)}>
            {visible ? <EyeOff size={20} aria-hidden="true" /> : <Eye size={20} aria-hidden="true" />}
          </button>
        </div>
      </div>
      <button type="submit" className="mds-signin-button" disabled={busy}>
        {busy ? 'Signing you in' : 'Sign in'}<ArrowRight size={20} aria-hidden="true" />
      </button>
    </form>
  );
}
