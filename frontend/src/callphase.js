// What the call panel shows while a click-to-call is in progress. `r` is the answer of /dialer/call/status.
// A call that was seen and then is gone for two checks in a row has ended; one that was never seen within 40 s did not start.
export const START_WAIT_MS = 40000;

export function startCall(now) {
  return { phase: "starting", seen: false, nones: 0, secs: 0, startedAt: now, done: false };
}

export function step(s, r, now) {
  if (r.state === "ringing" || r.state === "connected") {
    return { ...s, phase: r.state, seen: true, nones: 0, secs: r.seconds || 0, done: false };
  }
  const nones = s.nones + 1;
  if (s.seen && nones >= 2) return { ...s, phase: "ended", nones, done: true };
  if (!s.seen && now - s.startedAt > START_WAIT_MS) return { ...s, phase: "noshow", nones, done: true };
  return { ...s, nones };
}

export function clock(secs) {
  const m = Math.floor(secs / 60), s = secs % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
