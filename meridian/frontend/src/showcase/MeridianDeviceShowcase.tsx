import './meridianShowcase.css';

export function MeridianDeviceShowcase() {
  return (
    <div className="mds-wrap">
      <main className="mds-stage" aria-label="Cinematic Meridian app showcase with laptop and mobile app side by side">
        <div className="grain" />
        <div className="ambient-line" />
        <div className="reflection-laptop" />
        <div className="reflection-phone" />

        <section className="device-row">
          <div className="laptop-wrap">
            <div className="laptop" aria-label="MacBook style desktop app mockup">
              <div className="laptop-lid">
                <div className="laptop-screen">
                  <div className="screen-notch" />
                  <div className="desktop-app">
                    <aside className="desktop-sidebar">
                      <div className="brand"><span className="brand-mark" /> Meridian</div>
                      <nav className="nav-items" aria-label="Desktop navigation">
                        <div className="nav-item active"><span className="nav-icon" /> Concierge</div>
                        <div className="nav-item"><span className="nav-icon" /> Trips</div>
                        <div className="nav-item"><span className="nav-icon pin" /> Discover</div>
                        <div className="nav-item"><span className="nav-icon user" /> Profile</div>
                        <div className="nav-item"><span className="nav-icon gear" /> Preferences</div>
                        <div className="nav-item"><span className="nav-icon msg" /> Messages</div>
                      </nav>
                      <div className="sidebar-spacer" />
                      <div className="nav-item"><span className="nav-icon gear" /> Settings</div>
                      <div className="account-mini">
                        <div className="avatar" />
                        <div className="account-copy"><strong>Alex Morgan</strong><span>Explorer</span></div>
                      </div>
                    </aside>

                    <main className="desktop-main">
                      <div className="top-actions"><span>VIP</span><span>USD</span><span>...</span></div>
                      <h1>Good morning, Alex.</h1>
                      <div className="subhead">Where would you like to go next?</div>
                      <div className="chat-prompt">I&apos;m looking for a long weekend in wine country in November. Boutique, walkable towns, great food, and relaxing spa options.</div>
                      <div className="assistant-line">Perfect. I&apos;ve found a few places that match your style.</div>

                      <div className="rec-grid">
                        <article className="trip-card">
                          <div className="trip-art vineyard art-willamette" />
                          <div className="trip-body">
                            <div className="trip-title">Willamette Valley, Oregon</div>
                            <div className="trip-meta">Nov 7 - 10</div>
                            <div className="trip-price"><span>From $1,950</span><span className="circle-arrow">›</span></div>
                          </div>
                        </article>
                        <article className="trip-card">
                          <div className="trip-art napa art-napa" />
                          <div className="trip-body">
                            <div className="trip-title">Napa Valley, California</div>
                            <div className="trip-meta">Nov 14 - 17</div>
                            <div className="trip-price"><span>From $2,450</span><span className="circle-arrow">›</span></div>
                          </div>
                        </article>
                        <article className="trip-card">
                          <div className="trip-art mendoza art-mendoza" />
                          <div className="trip-body">
                            <div className="trip-title">Mendoza, Argentina</div>
                            <div className="trip-meta">Nov 21 - 24</div>
                            <div className="trip-price"><span>From $1,850</span><span className="circle-arrow">›</span></div>
                          </div>
                        </article>
                      </div>
                      <button className="more-button">View more recommendations</button>

                      <div className="composer-desktop">
                        <div className="input-shell"><span>Ask Meridian anything...</span><span className="send-dot">↗</span></div>
                        <div className="quick-actions"><span>Add travelers</span><span>Change dates</span><span>Add spa</span><span>Direct flights</span></div>
                      </div>
                    </main>

                    <aside className="desktop-right">
                      <section className="info-panel">
                        <div className="panel-head"><strong>Traveler context</strong><span>Edit</span></div>
                        <div className="profile-line"><div className="avatar" /><div><strong>Alex Morgan</strong><small>alex.morgan@gmail.com</small></div></div>
                        <div className="facts">
                          <div className="fact-row"><span>Profile</span><span>Explorer</span></div>
                          <div className="fact-row"><span>Travel style</span><span>Boutique, immersive, relaxed</span></div>
                          <div className="fact-row"><span>Interests</span><span>Wine, food, architecture, wellness</span></div>
                          <div className="fact-row"><span>Loyalty programs</span><span>Marriott Bonvoy, Delta SkyMiles</span></div>
                          <div className="fact-row"><span>Recent trips</span><span>Tuscany, Kyoto, Palm Springs</span></div>
                        </div>
                        <div className="linkish">View all</div>
                      </section>

                      <section className="info-panel activity">
                        <div className="panel-head"><strong>Meridian activity</strong><span className="live-dot">Live</span></div>
                        <div className="activity-item"><span className="check">✓</span><span>Understanding your request</span></div>
                        <div className="activity-item"><span className="check">✓</span><span>Searching preference-matched destinations</span></div>
                        <div className="activity-item"><span className="check">✓</span><span>Checking availability & pricing</span></div>
                        <div className="activity-item"><span className="check">✓</span><span>Curating personalized recommendations</span></div>
                        <div className="activity-item"><span className="check live">◌</span><span>Optimizing your itinerary</span></div>
                      </section>
                    </aside>
                  </div>
                </div>
              </div>
              <div className="laptop-base"><div className="ports"><span /><span /><span /></div></div>
            </div>
          </div>

          <div className="phone-wrap">
            <div className="phone" aria-label="Mobile app mockup">
              <div className="phone-screen">
                <div className="dynamic-island" />
                <div className="phone-status"><span>9:41</span><span>5G</span></div>
                <div className="phone-app">
                  <div className="phone-top"><span className="hamburger" /><div className="phone-title">Meridian</div><span className="bell" /></div>
                  <div className="mobile-profile">
                    <div className="avatar" />
                    <div><strong>Alex Morgan</strong><span>Explorer</span></div>
                    <button className="profile-button">View profile</button>
                  </div>
                  <div className="mobile-user-bubble">I&apos;m looking for a long weekend in wine country in November. Boutique, walkable towns, great food, and relaxing spa options.</div>
                  <div className="mobile-copy">Here are a few recommendations I think you&apos;ll love.</div>
                  <div className="mobile-cards">
                    <article className="mobile-trip hero">
                      <div className="mobile-art art-willamette" />
                      <div className="mobile-trip-copy"><span className="trend">Trending</span><strong>Willamette Valley, Oregon</strong><span>Nov 7 - 10 · 3 nights<br />From $1,950</span><span className="chev">›</span></div>
                    </article>
                    <article className="mobile-trip">
                      <div className="mobile-art art-napa" />
                      <div className="mobile-trip-copy"><strong>Napa Valley, California</strong><span>Nov 14 - 17 · 3 nights<br />From $2,450</span><span className="chev">›</span></div>
                    </article>
                  </div>
                  <div className="mobile-composer"><span>Ask Meridian anything...</span><span className="mic" /></div>
                  <nav className="bottom-nav" aria-label="Mobile navigation">
                    <div className="active"><span className="nav-dot" />Concierge</div>
                    <div><span className="nav-dot" />Trips</div>
                    <div><span className="nav-dot" />Discover</div>
                    <div><span className="nav-dot" />Messages</div>
                    <div><span className="nav-dot" />Profile</div>
                  </nav>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default MeridianDeviceShowcase;
