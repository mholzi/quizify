/**
 * Quizify — Reusable TimerBar component.
 *
 * Usage:
 *   const timer = new TimerBar('timer-bar', 15);
 *   timer.onExpired(() => console.log('time up!'));
 *   timer.start(15);
 */

class TimerBar {
    /**
     * @param {string} elementId  ID of the container element (should contain
     *   .timer-bar-fill, and optionally a sibling .timer-text).
     * @param {number} duration   Default duration in seconds.
     */
    constructor(elementId, duration) {
        this.container = document.getElementById(elementId);
        this.fill = this.container ? this.container.querySelector('.timer-bar-fill') : null;
        this.textEl = document.getElementById(elementId + '-text');
        this.duration = duration || 15;
        this.remaining = this.duration;
        this._raf = null;
        this._startTime = null;
        this._expiredCb = null;
        this._running = false;
    }

    /** Start (or restart) the countdown. */
    start(duration) {
        this.stop();
        this.duration = duration != null ? duration : this.duration;
        this.remaining = this.duration;
        this._running = true;
        this._startTime = performance.now();
        this._tick();
    }

    /** Update remaining time directly (from server timer_tick). */
    update(remaining) {
        this.remaining = Math.max(0, remaining);
        this._render(this.remaining);
        if (this.remaining <= 0 && this._running) {
            this._running = false;
            this._fireExpired();
        }
    }

    /** Stop the timer without firing expired. */
    stop() {
        this._running = false;
        if (this._raf) {
            cancelAnimationFrame(this._raf);
            this._raf = null;
        }
    }

    /** Register a callback for when time runs out. */
    onExpired(callback) {
        this._expiredCb = callback;
    }

    /* ---- internal ---- */

    _tick() {
        if (!this._running) return;
        const elapsed = (performance.now() - this._startTime) / 1000;
        this.remaining = Math.max(0, this.duration - elapsed);
        this._render(this.remaining);

        if (this.remaining <= 0) {
            this._running = false;
            this._fireExpired();
            return;
        }
        this._raf = requestAnimationFrame(() => this._tick());
    }

    _render(remaining) {
        const pct = this.duration > 0 ? (remaining / this.duration) * 100 : 0;

        if (this.fill) {
            this.fill.style.width = pct + '%';
            this.fill.classList.toggle('warning', pct <= 40 && pct > 15);
            this.fill.classList.toggle('critical', pct <= 15);
        }

        if (this.textEl) {
            this.textEl.textContent = Math.ceil(remaining) + 's';
            this.textEl.classList.toggle('warning', pct <= 40 && pct > 15);
            this.textEl.classList.toggle('critical', pct <= 15);
        }
    }

    _fireExpired() {
        if (this._expiredCb) this._expiredCb();
    }
}
