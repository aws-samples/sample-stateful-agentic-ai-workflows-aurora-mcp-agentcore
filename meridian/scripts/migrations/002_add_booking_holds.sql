-- Adds explicit expiry metadata for Meridian courtesy holds.
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS hold_expires_at TIMESTAMPTZ;
