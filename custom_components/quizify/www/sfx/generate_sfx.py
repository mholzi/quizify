"""Synthesize the four House-Plays-Along SFX cues from scratch.

Pure additive synthesis with the stdlib — the output is an original work,
so there is no licensing question at all (no third-party CC0 sourcing needed).
Game-show flavour: bright bell-ish stabs, short, all under ~1.6 s.
"""
import array
import math
import wave

SR = 44100


def env(t, dur, attack=0.005, release=0.25):
    """Percussive envelope: fast attack, exponential-ish decay."""
    if t < attack:
        return t / attack
    x = (t - attack) / max(dur - attack, 1e-6)
    return max(0.0, (1.0 - x) ** 2) * math.exp(-release * 12 * x)


def tone(buf, freq, start, dur, amp=0.5, harmonics=(1.0, 0.35, 0.12), detune=0.0):
    """Add a bell-ish tone (fundamental + a couple of partials) into buf."""
    n0 = int(start * SR)
    n = int(dur * SR)
    for i in range(n):
        t = i / SR
        e = env(t, dur)
        s = 0.0
        for k, h in enumerate(harmonics, start=1):
            s += h * math.sin(2 * math.pi * freq * k * (1 + detune) * t)
        idx = n0 + i
        if idx < len(buf):
            buf[idx] += amp * e * s / sum(harmonics)


def noise_hit(buf, start, dur, amp=0.25, seed=1):
    """Cheap deterministic noise burst (for the 'wrong' buzz body)."""
    n0 = int(start * SR)
    n = int(dur * SR)
    x = seed
    for i in range(n):
        x = (1103515245 * x + 12345) % (2**31)
        r = (x / (2**31)) * 2 - 1
        t = i / SR
        e = env(t, dur, attack=0.002, release=0.5)
        idx = n0 + i
        if idx < len(buf):
            buf[idx] += amp * e * r


def write(name, buf):
    peak = max(abs(v) for v in buf) or 1.0
    data = array.array(
        "h", (int(max(-1.0, min(1.0, v / peak * 0.89)) * 32767) for v in buf)
    )
    with wave.open(name, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    print(f"{name}: {len(buf) / SR:.2f}s")


# --- correct: bright rising major arpeggio (C6-E6-G6-C7), celebratory ping
dur = 0.85
buf = [0.0] * int(dur * SR)
for i, f in enumerate([1046.50, 1318.51, 1567.98, 2093.00]):
    tone(buf, f, 0.06 * i, 0.55 - 0.05 * i, amp=0.55)
write("correct.wav", buf)

# --- wrong: descending minor-second buzz, dull and short
dur = 0.7
buf = [0.0] * int(dur * SR)
tone(buf, 233.08, 0.0, 0.32, amp=0.6, harmonics=(1.0, 0.6, 0.4, 0.25))
tone(buf, 220.00, 0.16, 0.42, amp=0.6, harmonics=(1.0, 0.6, 0.4, 0.25))
noise_hit(buf, 0.0, 0.18, amp=0.12)
write("wrong.wav", buf)

# --- streak: three quick rising blips, tight and eager
dur = 0.75
buf = [0.0] * int(dur * SR)
for i, f in enumerate([880.00, 1174.66, 1567.98]):
    tone(buf, f, 0.10 * i, 0.3, amp=0.5, harmonics=(1.0, 0.25))
write("streak.wav", buf)

# --- winner: short fanfare — two stabs then a held major chord
dur = 1.6
buf = [0.0] * int(dur * SR)
for f in (523.25, 659.26, 783.99):          # C major stab
    tone(buf, f, 0.0, 0.28, amp=0.42)
for f in (587.33, 739.99, 880.00):          # D major stab
    tone(buf, f, 0.22, 0.28, amp=0.42)
for f in (659.26, 830.61, 987.77, 1318.51):  # E major, held + shimmer
    tone(buf, f, 0.45, 1.05, amp=0.45, harmonics=(1.0, 0.4, 0.2, 0.08))
write("winner.wav", buf)
