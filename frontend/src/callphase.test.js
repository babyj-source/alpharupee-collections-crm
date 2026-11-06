import { describe, expect, it } from "vitest";
import { clock, startCall, step } from "./callphase.js";

const none = { state: "none", seconds: 0 };

describe("call phase", () => {
  it("goes starting -> ringing -> connected -> ended", () => {
    let s = startCall(0);
    s = step(s, none, 2000);
    expect(s.phase).toBe("starting");
    s = step(s, { state: "ringing", seconds: 1 }, 4000);
    expect(s.phase).toBe("ringing");
    s = step(s, { state: "connected", seconds: 16 }, 6000);
    expect([s.phase, s.secs]).toEqual(["connected", 16]);
    s = step(s, none, 8000);
    expect(s.done).toBe(false);             // one empty answer is not the end (the list can blink)
    s = step(s, none, 10000);
    expect([s.phase, s.done]).toEqual(["ended", true]);
  });
  it("a blip between two live answers resets the count", () => {
    let s = startCall(0);
    s = step(s, { state: "connected", seconds: 3 }, 1000);
    s = step(s, none, 2000);
    s = step(s, { state: "connected", seconds: 8 }, 3000);
    s = step(s, none, 4000);
    expect(s.done).toBe(false);
  });
  it("a call that never shows up is reported after 40 seconds", () => {
    let s = startCall(0);
    s = step(s, none, 39000);
    expect(s.done).toBe(false);
    s = step(s, none, 41000);
    expect([s.phase, s.done]).toEqual(["noshow", true]);
  });
  it("formats the timer", () => {
    expect([clock(0), clock(16), clock(75), clock(3725)]).toEqual(["00:00", "00:16", "01:15", "62:05"]);
  });
});
