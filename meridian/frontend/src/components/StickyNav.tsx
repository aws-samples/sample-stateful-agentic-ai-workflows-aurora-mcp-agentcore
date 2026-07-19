/**
 * StickyNav — Meridian Pro top bar with brand mark, segmented nav, status dot
 */
import { useEffect, useState } from 'react';
import { MeridianMark } from './MeridianMark';

interface StickyNavProps {
  scrollY: number;
  themeMode: 'light' | 'dark';
  onToggleTheme: () => void;
  showThemeToggle?: boolean;
}

interface NavLink {
  label: string;
  target: string;
  href?: string;
  external?: boolean;
}

const navLinks: NavLink[] = [
  { label: 'Architecture', target: 'howitworks' },
  { label: 'Trips', target: 'products' },
  { label: 'Memory', target: 'memory' },
  { label: 'System', target: 'system' },
  {
    label: 'Docs',
    target: 'docs',
    href: 'https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore#readme',
    external: true,
  },
];

type Health = 'healthy' | 'checking' | 'down';

export function StickyNav({ scrollY: _scrollY, themeMode, onToggleTheme, showThemeToggle = true }: StickyNavProps) {
  const [active, setActive] = useState<string>('howitworks');
  const [health, setHealth] = useState<Health>('checking');

  // Backend health ping
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;

    const ping = async () => {
      try {
        const res = await fetch('http://localhost:8000/health');
        setHealth(res.ok ? 'healthy' : 'down');
      } catch {
        setHealth('down');
      }
    };

    ping();
    timer = setInterval(ping, 30000);
    return () => {
      if (timer) clearInterval(timer);
    };
  }, []);

  // Active section observer
  useEffect(() => {
    const ids = navLinks.filter((n) => !n.external).map((n) => n.target);
    const sections = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => Boolean(el));

    if (sections.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible) setActive(visible.target.id);
      },
      { rootMargin: '-30% 0px -60% 0px', threshold: [0.1, 0.4, 0.7] },
    );

    sections.forEach((s) => observer.observe(s));
    return () => observer.disconnect();
  }, []);

  const scrollTo = (id: string) =>
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });

  const statusLabel =
    health === 'healthy' ? 'Aurora · pgvector OK' : health === 'checking' ? 'Checking…' : 'Backend offline';
  const statusClass = health === 'healthy' ? '' : health === 'checking' ? 'warn' : 'err';

  return (
    <nav className="mp-topnav">
      <div className="mp-topnav-inner">
        <div className="mp-brand">
          <MeridianMark variant="nav" />
          Meridian
          <span className="mp-brand-build">Pro · 2026.1</span>
        </div>

        <div className="mp-nav-center">
          {navLinks.map((n) =>
            n.external && n.href ? (
              <a
                key={n.target}
                className="mp-nav-link"
                href={n.href}
                target="_blank"
                rel="noreferrer"
              >
                {n.label} ↗
              </a>
            ) : (
              <button
                key={n.target}
                type="button"
                className={`mp-nav-link${active === n.target ? ' active' : ''}`}
                onClick={() => scrollTo(n.target)}
              >
                {n.label}
              </button>
            ),
          )}
        </div>

        <div className="mp-nav-right">
          {showThemeToggle && (
            <button
              type="button"
              className="mp-theme-toggle"
              onClick={onToggleTheme}
              aria-label={`Switch to ${themeMode === 'light' ? 'dark' : 'light'} mode`}
            >
              {themeMode === 'light' ? 'Dark' : 'Light'}
            </button>
          )}
          <div className="mp-nav-meta" title={statusLabel}>
            <span className={`mp-nav-status-dot ${statusClass}`.trim()} /> {statusLabel}
          </div>
          <button className="mp-cta" onClick={() => window.location.assign('/showcase')}>
            Open showcase →
          </button>
        </div>
      </div>
    </nav>
  );
}
