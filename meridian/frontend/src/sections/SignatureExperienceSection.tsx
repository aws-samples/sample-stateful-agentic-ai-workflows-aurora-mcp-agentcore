import { FadeIn } from '../components/FadeIn';

const destinationCards = [
  { title: 'Valmonts · Italy Escape', tag: 'Wine' },
  { title: 'New Valley Getaway', tag: 'Coastal' },
  { title: 'Mountain Rhythmline', tag: 'Retreat' },
];

const activityItems = [
  'Understanding your intent',
  'Shortlisting recommendations',
  'Checking availability',
  'Capturing preferences',
  'Opening your itinerary',
];

export function SignatureExperienceSection() {
  return (
    <section id="experience" className="mp-signature">
      <FadeIn>
        <div className="mp-signature-head">
          <div className="mp-label-row">New visual direction · cinematic concierge</div>
          <h2>Premium desktop + mobile experience, inspired by modern travel ops studios.</h2>
          <p>
            A dark glass command center with rich destination cards, persistent traveler context, and a
            companion mobile surface for on-the-go continuity.
          </p>
        </div>
      </FadeIn>

      <FadeIn delay={0.06}>
        <div className="mp-signature-stage">
          <article className="mp-device-laptop" aria-label="Desktop concierge concept">
            <header className="mp-device-top">
              <div className="mp-device-brand">Meridian Concierge</div>
              <div className="mp-device-meta">Traveler context · Live</div>
            </header>

            <div className="mp-device-shell">
              <aside className="mp-device-nav">
                <span className="is-on">Concierge</span>
                <span>Trips</span>
                <span>Discover</span>
                <span>Profile</span>
                <span>Messages</span>
              </aside>

              <main className="mp-device-main">
                <div className="mp-device-greeting">
                  <h3>Good morning, Alex.</h3>
                  <p>Who’s traveling this week?</p>
                </div>

                <div className="mp-device-cards">
                  {destinationCards.map((card) => (
                    <div key={card.title} className="mp-device-card">
                      <div className="mp-device-card-art" />
                      <strong>{card.title}</strong>
                      <span>{card.tag}</span>
                    </div>
                  ))}
                </div>

                <div className="mp-device-chat">
                  Ask Meridian anything…
                </div>
              </main>

              <aside className="mp-device-rail">
                <h4>Today’s context</h4>
                <div className="mp-device-rail-user">Alex Morgan · Traveler</div>
                <ul>
                  {activityItems.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </aside>
            </div>
          </article>

        </div>
      </FadeIn>
    </section>
  );
}
