/**
 * Meridian Pro — light, professional single-page application.
 *
 * Section order: hero → journey → workspace → phase 3 trips → phase 4 production → substrate → vision.
 */
import { AgentBridgeProvider } from './context/AgentBridge';
import { useScrollY } from './hooks/useScrollY';
import { StickyNav } from './components/StickyNav';
import { HeroSection } from './sections/HeroSection';
import { HowItWorksSection } from './sections/HowItWorksSection';
import { AgentSection } from './sections/AgentSection';
import { MemorySection } from './sections/MemorySection';
import { ProductsSection } from './sections/ProductsSection';
import { SystemSection } from './sections/SystemSection';
import { Vision2026Section } from './sections/Vision2026Section';
import { PhaseMatrixSection } from './sections/PhaseMatrixSection';
import { Footer } from './components/Footer';

export default function App() {
  const scrollY = useScrollY();

  return (
    <AgentBridgeProvider>
      <div style={{ background: 'var(--mp-bg)', minHeight: '100vh' }}>
        <StickyNav scrollY={scrollY} />
        <HeroSection scrollY={scrollY} />
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
