/**
 * Meridian Pro — light, professional single-page application.
 *
 * Section order: hero → journey → workspace → phase 3 trips → phase 4 production → substrate → vision.
 */
import { AgentBridgeProvider } from './context/AgentBridge';
import { useScrollY } from './hooks/useScrollY';
import { useEffect, useState } from 'react';
import { StickyNav } from './components/StickyNav';
import { HeroSection } from './sections/HeroSection';
import { SignatureExperienceSection } from './sections/SignatureExperienceSection';
import { HowItWorksSection } from './sections/HowItWorksSection';
import { ConciergeStudio as AgentSection } from './sections/ConciergeStudio';
import { MemorySection } from './sections/MemorySection';
import { ProductsSection } from './sections/ProductsSection';
import { SystemSection } from './sections/SystemSection';
import { Vision2026Section } from './sections/Vision2026Section';
import { PhaseMatrixSection } from './sections/PhaseMatrixSection';
import { Footer } from './components/Footer';

type ThemeMode = 'light' | 'dark';
const THEME_STORAGE_KEY = 'meridian_theme_mode';

function initialTheme(): ThemeMode {
  if (typeof window === 'undefined') return 'light';
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export default function App() {
  const scrollY = useScrollY();
  const [themeMode, setThemeMode] = useState<ThemeMode>(initialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', themeMode);
    window.localStorage.setItem(THEME_STORAGE_KEY, themeMode);
  }, [themeMode]);

  return (
    <AgentBridgeProvider>
      <div style={{ background: 'var(--mp-bg)', minHeight: '100vh' }}>
        <StickyNav
          scrollY={scrollY}
          themeMode={themeMode}
          onToggleTheme={() => setThemeMode((mode) => (mode === 'light' ? 'dark' : 'light'))}
        />
        <HeroSection scrollY={scrollY} />
        <SignatureExperienceSection />
        <HowItWorksSection />
        <AgentSection />
        <ProductsSection />
        <MemorySection />
        <SystemSection />
        <Vision2026Section />
        <PhaseMatrixSection />
        <Footer />
      </div>
    </AgentBridgeProvider>
  );
}
