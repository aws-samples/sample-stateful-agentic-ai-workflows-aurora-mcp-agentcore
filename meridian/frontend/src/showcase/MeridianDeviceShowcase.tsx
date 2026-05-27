import { DesktopMeridianApp } from './DesktopMeridianApp';
import { useMeridianShowcase } from './hooks/useMeridianShowcase';
import './meridianShowcase.css';

export function MeridianDeviceShowcase() {
  const state = useMeridianShowcase();

  return (
    <main className="mds-root mds-fullbleed-route" aria-label="Meridian product showcase">
      <DesktopMeridianApp state={state} />
    </main>
  );
}

export default MeridianDeviceShowcase;
