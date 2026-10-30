// Hands-free voice: short spoken commands ("next", "repeat", "check") while both hands are busy,
// and one-shot dictation for questions. Uses the browser's speech recognition (Chrome, Edge, Android;
// needs https or localhost). Listening pauses while the app itself is speaking.

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

export const voiceSupported = () => Boolean(SR) && window.isSecureContext;

// First match wins; only short utterances count as commands, so normal talk doesn't trigger them.
const COMMANDS = [
  ["skip", /\bskip\b/],
  ["back", /\b(go back|back|previous)\b/],
  ["next", /\b(next( step)?|i'?m done|done|finished|continue)\b/],
  ["repeat", /\b(repeat|say (that|it) again|again)\b/],
  ["check", /\b(check( it| my work)?|look at (it|this)|watch)\b/],
  ["stop", /\b(stop|quiet|camera off)\b/],
  ["show", /\b(show me|show|3d|animation)\b/],
  ["parts", /\b(parts|what do i need|which parts)\b/],
  ["help", /\b(help|commands)\b/],
];

export function matchCommand(text) {
  const t = String(text || "").toLowerCase().trim();
  if (!t || t.split(/\s+/).length > 5) return null;
  for (const [name, re] of COMMANDS) if (re.test(t)) return name;
  return null;
}

// Continuous listening for commands. Returns a controller, or null when the browser can't listen.
export function listen({ onCommand, onHeard, onState } = {}) {
  if (!voiceSupported()) return null;
  const rec = new SR();
  rec.lang = navigator.language || "en-US";
  rec.continuous = true;
  rec.interimResults = false;
  let on = true, paused = false;
  rec.onresult = (e) => {
    const text = e.results[e.results.length - 1][0].transcript;
    onHeard?.(text);
    const cmd = matchCommand(text);
    if (cmd) onCommand?.(cmd, text);
  };
  rec.onstart = () => onState?.("listening");
  rec.onend = () => {
    if (on && !paused) { try { rec.start(); } catch { /* already starting */ } }
    else onState?.(paused ? "paused" : "off");
  };
  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") { on = false; onState?.("blocked"); }
  };
  try { rec.start(); } catch { /* ignore */ }
  return {
    stop() { on = false; try { rec.abort(); } catch { /* ignore */ } },
    pause() { if (!on) return; paused = true; try { rec.abort(); } catch { /* ignore */ } },
    resume() { if (!on || !paused) return; paused = false; try { rec.start(); } catch { /* ignore */ } },
  };
}

// One question, spoken: resolves with the words, or "" if nothing was heard.
export function dictate() {
  return new Promise((resolve) => {
    if (!voiceSupported()) return resolve("");
    const rec = new SR();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = false;
    let said = "";
    rec.onresult = (e) => { said = e.results[0][0].transcript; };
    rec.onend = () => resolve(said);
    rec.onerror = () => resolve(said);
    try { rec.start(); } catch { resolve(""); }
  });
}
