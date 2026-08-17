import { useState } from 'react'
import ReactDOM from 'react-dom/client'
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle2,
  Clock3,
  Compass,
  Database,
  Headphones,
  Heart,
  Hotel,
  MapPin,
  Plane,
  Route,
  Search,
  ShieldCheck,
  Sparkles,
  Utensils,
  Wine,
} from 'lucide-react'
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import './meridian-design-md.css'

type Mode = 'discovery' | 'recovery'

type DiscoveryOption = {
  id: string
  image: string
  location: string
  title: string
  description: string
  price: string
  availability: string
  signals: string[]
  icon: typeof Wine
}

const discoveryOptions: DiscoveryOption[] = [
  {
    id: 'tuscany',
    image: '/travel/catalog/WEL-005.jpg',
    location: 'Val d’Orcia, Tuscany',
    title: 'Tuscany, by instinct',
    description:
      'Seven unhurried days between Florence and Val d’Orcia, shaped around vineyard stays and room to wander.',
    price: '$3,840',
    availability: '6 departure windows',
    signals: ['Boutique stays', 'Slow travel', 'Wine country'],
    icon: Wine,
  },
  {
    id: 'tokyo-stay',
    image: '/travel/catalog/TKY-003.jpg',
    location: 'Aoyama, Tokyo',
    title: 'Tokyo, quietly',
    description:
      'A considered city stay with calm mornings, design-led hotels, and direct rail access.',
    price: '$2,960',
    availability: '4 stays available',
    signals: ['Quiet hotel', 'Transit ease', 'Hotel Platinum'],
    icon: Hotel,
  },
  {
    id: 'tokyo-table',
    image: '/travel/catalog/CTY-002.jpg',
    location: 'Tokyo after dark',
    title: 'A table worth the flight',
    description:
      'A food-led long weekend balanced between neighborhood counters and late-night city energy.',
    price: '$2,420',
    availability: '3 weekend windows',
    signals: ['Dining focus', 'No red-eye', 'Central stay'],
    icon: Utensils,
  },
]

const recoveryOptions = [
  {
    id: 'protected',
    label: 'Best overall',
    title: 'Protect the trip, depart tomorrow',
    route: 'JFK 13:10 → HND 17:05 +1',
    detail: '1 stop · 16h 55m · Same cabin',
    price: 'No fare increase',
  },
  {
    id: 'earlier',
    label: 'Earlier arrival',
    title: 'Morning departure via the West Coast',
    route: 'JFK 08:35 → NRT 14:20 +1',
    detail: '1 stop · 17h 45m · Airport transfer needed',
    price: '+$180',
  },
  {
    id: 'direct',
    label: 'Fewer changes',
    title: 'Wait for the next nonstop',
    route: 'JFK 11:45 → HND 15:50 +1',
    detail: 'Nonstop · 14h 05m · Departs in 2 days',
    price: '+$320',
  },
]

function MeridianMark() {
  return (
    <span className="mdm-brand">
      <img src="/brand/meridian-mark.jpg" alt="" width="36" height="36" />
      <span>Meridian</span>
    </span>
  )
}

function AppHeader({
  mode,
  onModeChange,
}: {
  mode: Mode
  onModeChange: (mode: Mode) => void
}) {
  return (
    <header className="mdm-header">
      <MeridianMark />
      <div className="mdm-mode-switch" role="tablist" aria-label="Meridian concept views">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'discovery'}
          className={mode === 'discovery' ? 'is-active' : ''}
          onClick={() => onModeChange('discovery')}
        >
          <Compass size={16} aria-hidden="true" />
          Discovery
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'recovery'}
          className={mode === 'recovery' ? 'is-active' : ''}
          onClick={() => onModeChange('recovery')}
        >
          <Route size={16} aria-hidden="true" />
          Recovery
        </button>
      </div>
      <div className="mdm-account">
        <div>
          <strong>Alex Morgan</strong>
          <span>Hotel Platinum · Airline Premier</span>
        </div>
        <img src="/travel/alex-morgan.jpg" alt="Alex Morgan" width="40" height="40" />
      </div>
    </header>
  )
}

function ContextSignal() {
  return (
    <div className="mdm-context-signal">
      <Sparkles size={16} aria-hidden="true" />
      <span>
        <strong>Recalled for this search</strong>
        Quiet hotels · aisle seats · no red-eyes
      </span>
    </div>
  )
}

function DiscoveryCard({
  option,
  featured,
  onSelect,
}: {
  option: DiscoveryOption
  featured: boolean
  onSelect: () => void
}) {
  const Icon = option.icon

  return (
    <article className={`mdm-discovery-card${featured ? ' is-featured' : ''}`}>
      <img src={option.image} alt="" width="1600" height="900" />
      <span className="mdm-photo-scrim" aria-hidden="true" />
      <div className="mdm-card-topline">
        <span>
          <MapPin size={14} aria-hidden="true" />
          {option.location}
        </span>
        <button type="button" onClick={onSelect} aria-label={`Save ${option.title}`}>
          <Heart size={17} aria-hidden="true" />
        </button>
      </div>
      <div className="mdm-discovery-copy">
        <Icon size={featured ? 22 : 18} aria-hidden="true" />
        <h2>{option.title}</h2>
        {featured && <p>{option.description}</p>}
        <div className="mdm-signal-row">
          {option.signals.map((signal) => (
            <span key={signal}>
              <Check size={12} aria-hidden="true" />
              {signal}
            </span>
          ))}
        </div>
        <div className="mdm-card-footer">
          <span>
            <small>From · illustrative</small>
            <strong>{option.price}</strong>
          </span>
          <span className="mdm-availability">
            <Clock3 size={14} aria-hidden="true" />
            {option.availability}
          </span>
          <button type="button" onClick={onSelect}>
            {featured ? 'View itinerary' : 'Feature this trip'}
            <ArrowUpRight size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
    </article>
  )
}

function DiscoveryView() {
  const [featuredId, setFeaturedId] = useState('tuscany')
  const featured =
    discoveryOptions.find((option) => option.id === featuredId) ?? discoveryOptions[0]
  const supporting = discoveryOptions.filter((option) => option.id !== featured.id)

  return (
    <main className="mdm-discovery">
      <div className="mdm-discovery-heading">
        <div>
          <h1>Good afternoon, Alex.</h1>
          <p>Three journeys, considered around the way you already travel.</p>
        </div>
        <ContextSignal />
      </div>
      <section className="mdm-discovery-grid" aria-label="Recommended journeys">
        <DiscoveryCard
          option={featured}
          featured
          onSelect={() => setFeaturedId(featured.id)}
        />
        {supporting.map((option) => (
          <DiscoveryCard
            key={option.id}
            option={option}
            featured={false}
            onSelect={() => setFeaturedId(option.id)}
          />
        ))}
      </section>
    </main>
  )
}

function RoutePlan({ selected }: { selected: string }) {
  const option =
    recoveryOptions.find((candidate) => candidate.id === selected) ?? recoveryOptions[0]

  return (
    <article className="mdm-primary-plan">
      <header className="mdm-plan-header">
        <span>
          <Sparkles size={17} aria-hidden="true" />
          Recommended recovery plan
        </span>
        <span className="mdm-ready-badge">
          <CheckCircle2 size={14} aria-hidden="true" />
          Plan ready
        </span>
      </header>

      <div className="mdm-plan-title">
        <div>
          <h2>{option.title}</h2>
          <p>
            Preserves Alex’s cabin preference and keeps the Haneda arrival without
            an overnight airport change.
          </p>
        </div>
        <span>
          <small>Fare change</small>
          <strong>{option.price}</strong>
        </span>
      </div>

      <div className="mdm-route-line" aria-label={option.route}>
        <div>
          <strong>JFK</strong>
          <span>{option.route.split('→')[0].replace('JFK', '').trim()}</span>
        </div>
        <div className="mdm-route-track" aria-hidden="true">
          <span />
          <Plane size={18} />
          <span />
        </div>
        <div>
          <strong>{selected === 'earlier' ? 'NRT' : 'HND'}</strong>
          <span>{option.route.split('→')[1].replace(/HND|NRT/, '').trim()}</span>
        </div>
      </div>

      <div className="mdm-plan-facts">
        <span>
          <Clock3 size={15} aria-hidden="true" />
          {option.detail}
        </span>
        <span>
          <ShieldCheck size={15} aria-hidden="true" />
          Airline Premier retained
        </span>
        <span>
          <CheckCircle2 size={15} aria-hidden="true" />
          Inventory step complete
        </span>
      </div>

      <div className="mdm-why-row">
        <strong>Why this fits Alex</strong>
        <span>No red-eye</span>
        <span>Aisle seat</span>
        <span>Haneda preferred</span>
      </div>
    </article>
  )
}

function RecoveryView() {
  const [selected, setSelected] = useState('protected')
  const [held, setHeld] = useState(false)

  return (
    <main className="mdm-recovery">
      <div className="mdm-recovery-heading">
        <div>
          <span className="mdm-disruption-badge">
            <Plane size={15} aria-hidden="true" />
            Flight canceled
          </span>
          <h1>Alex’s JFK to Tokyo recovery</h1>
          <p>Traveler approval remains required before any reservation changes.</p>
        </div>
        <div className="mdm-checkpoint-summary">
          <Database size={17} aria-hidden="true" />
          <span>
            <strong>Checkpoint saved in Aurora</strong>
            Thread mrd-7f3a · safe to resume
          </span>
        </div>
      </div>

      <div className="mdm-recovery-layout">
        <section className="mdm-decision-column" aria-label="Recovery plan options">
          <RoutePlan selected={selected} />

          <div className="mdm-plan-actions">
            <button type="button" className="is-primary" onClick={() => setHeld(true)}>
              <ShieldCheck size={17} aria-hidden="true" />
              {held ? 'Plan held for review' : 'Hold this plan'}
            </button>
            <button type="button" className="is-secondary">
              Compare details
              <ArrowRight size={16} aria-hidden="true" />
            </button>
            <span role="status" aria-live="polite">
              {held ? 'Illustrative hold only · no reservation changed' : 'No reservation action'}
            </span>
          </div>

          <section className="mdm-alternatives" aria-labelledby="alternative-heading">
            <header>
              <div>
                <h2 id="alternative-heading">Other viable paths</h2>
                <p>Ranked from the same illustrative recovery search.</p>
              </div>
              <span>3 options</span>
            </header>
            <div className="mdm-alternative-list">
              {recoveryOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className={selected === option.id ? 'is-selected' : ''}
                  onClick={() => {
                    setSelected(option.id)
                    setHeld(false)
                  }}
                  aria-pressed={selected === option.id}
                >
                  <span className="mdm-option-check">
                    {selected === option.id ? (
                      <Check size={13} aria-hidden="true" />
                    ) : (
                      <span aria-hidden="true" />
                    )}
                  </span>
                  <span>
                    <small>{option.label}</small>
                    <strong>{option.title}</strong>
                    <em>{option.route}</em>
                  </span>
                  <span>
                    <strong>{option.price}</strong>
                    <small>{option.detail}</small>
                  </span>
                </button>
              ))}
            </div>
          </section>
        </section>

        <aside className="mdm-support-rail" aria-label="Recovery support">
          <article className="mdm-concierge-panel">
            <header>
              <span>
                <Headphones size={17} aria-hidden="true" />
                Concierge assistance
              </span>
              <Sparkles size={16} aria-hidden="true" />
            </header>
            <img
              src="/travel/haneda-hotel.jpg"
              alt="Airport hotel room overlooking Haneda runways"
              width="1600"
              height="900"
            />
            <div>
              <span>Haneda · arrival support</span>
              <h2>Keep the first night flexible</h2>
              <p>Shortlist a quiet airport-area stay with lounge access and an easy transfer.</p>
            </div>
            <button type="button">
              <Hotel size={16} aria-hidden="true" />
              Find hotel options
            </button>
          </article>

          <article className="mdm-proof-panel">
            <header>
              <span>
                <ShieldCheck size={17} aria-hidden="true" />
                Agent proof
              </span>
              <small>Current concept state</small>
            </header>
            <ul>
              <li>
                <Check size={12} aria-hidden="true" />
                <span>
                  <strong>Alternatives ranked</strong>
                  <small>3 illustrative options</small>
                </span>
              </li>
              <li>
                <Check size={12} aria-hidden="true" />
                <span>
                  <strong>Traveler context applied</strong>
                  <small>Seat, hotel, and timing preferences</small>
                </span>
              </li>
              <li>
                <Check size={12} aria-hidden="true" />
                <span>
                  <strong>Workflow checkpointed</strong>
                  <small>Recovery state represented as durable</small>
                </span>
              </li>
            </ul>
            <button type="button">
              View system proof
              <ArrowRight size={15} aria-hidden="true" />
            </button>
          </article>

          <article className="mdm-progress-panel">
            <header>
              <span>
                <Database size={17} aria-hidden="true" />
                Checkpointed plan
              </span>
              <small>Ready</small>
            </header>
            <ol>
              {['Disruption', 'Search', 'Rank', 'Save', 'Verify'].map((step) => (
                <li key={step}>
                  <Check size={11} aria-hidden="true" />
                  {step}
                </li>
              ))}
            </ol>
            <p>All represented steps can resume from the saved workflow state.</p>
          </article>
        </aside>
      </div>
    </main>
  )
}

export function MeridianDesignMdMockup() {
  const [mode, setMode] = useState<Mode>('discovery')

  return (
    <div className={`mdm-shell is-${mode}`}>
      <AppHeader mode={mode} onModeChange={setMode} />
      {mode === 'discovery' ? <DiscoveryView /> : <RecoveryView />}
      <div className="mdm-quick-search">
        <Search size={15} aria-hidden="true" />
        <span>{mode === 'discovery' ? 'Ask Meridian about this trip' : 'Ask about this recovery'}</span>
      </div>
    </div>
  )
}

const rootElement = document.getElementById('meridian-design-md-root')

if (rootElement) {
  ReactDOM.createRoot(rootElement).render(<MeridianDesignMdMockup />)
}
