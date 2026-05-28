/**
 * Meridian Pro — light, professional single-page application.
 *
 * Section order: hero → architecture → trips → production memory → system → vision.
 */
import { useScrollY } from './hooks/useScrollY';
import { useEffect } from 'react';
import { AgentBridgeProvider } from './context/AgentBridge';
import { StickyNav } from './components/StickyNav';
import { HeroSection } from './sections/HeroSection';
import { HowItWorksSection } from './sections/HowItWorksSection';
import { MemorySection } from './sections/MemorySection';
import { ProductsSection } from './sections/ProductsSection';
import { SystemSection } from './sections/SystemSection';
import { Vision2026Section } from './sections/Vision2026Section';
import { PhaseMatrixSection } from './sections/PhaseMatrixSection';
import { Footer } from './components/Footer';

export default function App() {
  const scrollY = useScrollY();

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', 'light');
  }, []);

  return (
    <AgentBridgeProvider>
      <div style={{ background: 'var(--mp-bg)', minHeight: '100vh' }}>
        <StickyNav
          scrollY={scrollY}
          themeMode="light"
          onToggleTheme={() => {}}
          showThemeToggle={false}
        />
        <HeroSection scrollY={scrollY} />
        <HowItWorksSection />
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
