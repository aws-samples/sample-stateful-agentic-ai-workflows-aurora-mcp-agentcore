import { useEffect, useState } from 'react';
import { parseDatabaseTime } from '../journey/evidence';

export function useHoldClock(expiresAt?: string | null, observedAt?: string | null, receivedAt?: number) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const serverTime = parseDatabaseTime(observedAt);
  const hasServerClock = Number.isFinite(serverTime) && receivedAt != null;
  const clockNow = hasServerClock ? serverTime + Math.max(0, now - receivedAt) : now;
  const expiry = parseDatabaseTime(expiresAt);
  const remaining = Math.max(0, expiry - clockNow);
  return { remaining, expired: Number.isFinite(expiry) && remaining === 0, knownExpiry: Number.isFinite(expiry), hasServerClock };
}
