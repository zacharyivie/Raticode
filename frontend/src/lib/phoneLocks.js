// Phones the suspicious-request guard locked (src/gofer/devices/phone_guard.py).
// Only the user unlocks them, in Settings > Devices > Paired devices.

export function phoneLocks(status) {
  const names = new Map((status?.peers || []).map(peer => [peer.device_id, peer.name]));
  return (status?.phone_guard?.locked || []).map(lock => ({
    deviceId: lock.device_id,
    name: names.get(lock.device_id) || "Your phone",
    lockedAt: lock.locked_at,
    reasons: lock.reasons || [],
  }));
}

export function phoneLockNotice(locks) {
  if (!locks?.length) return "";
  if (locks.length > 1) return `${locks.length} phones are locked after suspicious requests. Review them and unlock the phones in Settings > Devices.`;
  return `${locks[0].name} is locked: Raticode stopped a suspicious request from it. Review it and unlock the phone in Settings > Devices.`;
}
