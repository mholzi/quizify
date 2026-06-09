/**
 * Quizify Player - Sound Module
 *
 * Short audio cues played on the player's own phone:
 *   - correct answer  → rising two-note chime
 *   - wrong answer     → low buzzer
 *   - last-5s ticking  → soft single tick per second
 *
 * All cues are synthesised with the Web Audio API (oscillators +
 * gain envelopes) so the integration ships no audio assets and the
 * page weight is unchanged.
 *
 * Mobile browsers require the AudioContext to be created/resumed from
 * a user gesture. We lazily create it and resume it on the first tap
 * anywhere (the player always taps to join / answer), so by the time a
 * cue fires the context is unlocked. If the user never interacts (or
 * the browser has no Web Audio support) every call is a silent no-op.
 *
 * A mute preference is persisted in localStorage and toggled via the
 * speaker button in the player header.
 */
(function () {
    'use strict';

    var MUTE_KEY = 'quizify_sound_muted';

    var ctx = null;          // lazily-created AudioContext
    var muted = false;       // user preference
    var _lastTickSecond = null;

    try {
        muted = window.localStorage.getItem(MUTE_KEY) === '1';
    } catch (e) {
        muted = false;
    }

    function supported() {
        return typeof (window.AudioContext || window.webkitAudioContext) === 'function';
    }

    function ensureCtx() {
        if (muted || !supported()) return null;
        if (!ctx) {
            var Ctor = window.AudioContext || window.webkitAudioContext;
            try {
                ctx = new Ctor();
            } catch (e) {
                ctx = null;
                return null;
            }
        }
        // Autoplay policy: context may start/become suspended; resume is
        // a no-op if already running and safe to call repeatedly.
        if (ctx.state === 'suspended' && typeof ctx.resume === 'function') {
            ctx.resume().catch(function () { /* ignore */ });
        }
        return ctx;
    }

    // Unlock audio on the first user gesture so later programmatic cues
    // are allowed to play on mobile.
    function unlock() {
        ensureCtx();
    }
    document.addEventListener('pointerdown', unlock, { once: false, passive: true });
    document.addEventListener('keydown', unlock, { once: false, passive: true });

    /**
     * Play a single tone with a short attack/decay envelope.
     * @param {number} freq   - frequency in Hz
     * @param {number} start  - delay before the tone starts (seconds)
     * @param {number} dur    - tone duration (seconds)
     * @param {string} type   - oscillator type
     * @param {number} peak   - peak gain (0..1)
     */
    function tone(freq, start, dur, type, peak) {
        var ac = ensureCtx();
        if (!ac) return;
        var t0 = ac.currentTime + start;
        var osc = ac.createOscillator();
        var gain = ac.createGain();
        osc.type = type || 'sine';
        osc.frequency.value = freq;
        // Click-free envelope.
        gain.gain.setValueAtTime(0.0001, t0);
        gain.gain.exponentialRampToValueAtTime(peak || 0.18, t0 + 0.012);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        osc.connect(gain);
        gain.connect(ac.destination);
        osc.start(t0);
        osc.stop(t0 + dur + 0.02);
    }

    /** Rising two-note "correct" chime. */
    function playCorrect() {
        if (muted) return;
        tone(660, 0, 0.14, 'sine', 0.18);   // E5
        tone(990, 0.11, 0.20, 'sine', 0.18); // B5
    }

    /** Low "wrong" buzzer. */
    function playWrong() {
        if (muted) return;
        tone(196, 0, 0.26, 'sawtooth', 0.12); // G3
        tone(146, 0.05, 0.26, 'sawtooth', 0.10); // D3
    }

    /** Soft tick for the time-running-out countdown (last 5 seconds). */
    function playTick() {
        if (muted) return;
        tone(880, 0, 0.05, 'square', 0.06);
    }

    /**
     * Drive the last-5s ticking from a remaining-seconds value. Plays at
     * most one tick per whole second and only inside the 1..5s window.
     * Reset must be called when a new question starts.
     * @param {number} remaining - seconds left
     */
    function tickFromRemaining(remaining) {
        if (muted) return;
        var sec = Math.ceil(remaining);
        if (sec <= 0 || sec > 5) return;
        if (sec === _lastTickSecond) return;
        _lastTickSecond = sec;
        playTick();
    }

    /** Clear the per-question tick guard so the next round can tick again. */
    function resetTick() {
        _lastTickSecond = null;
    }

    function isMuted() {
        return muted;
    }

    function setMuted(value) {
        muted = !!value;
        try {
            window.localStorage.setItem(MUTE_KEY, muted ? '1' : '0');
        } catch (e) { /* ignore */ }
        if (!muted) ensureCtx(); // warm up on un-mute (still a gesture)
        return muted;
    }

    function toggleMute() {
        return setMuted(!muted);
    }

    window.QuizifyPlayerSound = {
        playCorrect: playCorrect,
        playWrong: playWrong,
        playTick: playTick,
        tickFromRemaining: tickFromRemaining,
        resetTick: resetTick,
        isMuted: isMuted,
        setMuted: setMuted,
        toggleMute: toggleMute
    };
})();
