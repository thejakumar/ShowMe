// Hands-free voice: short spoken commands ("next", "repeat", "check") while both hands are busy,
// and one-shot dictation for questions. Uses the browser's speech recognition (Chrome, Edge, Android;
// needs https or localhost). Listening pauses while the app itself is speaking.

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

export const voiceSupported = () => Boolean(SR) && window.isSecureContext;

// First match wins; only short utterances count as commands, so normal talk doesn't trigger them.
const COMMANDS = [
  ["where", /\b(where am i|find my place|what('?s| is) next|what now)\b/],
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

const NUMBERS = { one: 1, two: 2, to: 2, too: 2, three: 3, four: 4, for: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9,
  ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
  nineteen: 19, twenty: 20 };

export function matchCommand(text) {
  const t = String(text || "").toLowerCase().trim();
  if (!t || t.split(/\s+/).length > 5) return null;
  const go = t.match(/\b(?:go to |jump to |open )?step (\d{1,2}|[a-z]+)\b/);
  if (go) {
    const n = /^\d+$/.test(go[1]) ? Number(go[1]) : NUMBERS[go[1]];
    if (n) return `goto:${n}`;
  }
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
