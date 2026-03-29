/**
 * Quizify — FunFactReveal component.
 *
 * Usage:
 *   const ff = new FunFactReveal('fun-fact');
 *   ff.show('Bananas are berries, but strawberries are not!');
 *   ff.hide();
 */

class FunFactReveal {
    /**
     * @param {string} elementId  ID of the .fun-fact-card element.
     */
    constructor(elementId) {
        this.el = document.getElementById(elementId);
        this._textEl = this.el ? this.el.querySelector('.fun-fact-text') : null;
        this._timeout = null;
    }

    /**
     * Show the fun fact with a slide-in animation.
     * @param {string} text   The fun fact text.
     * @param {number} delay  Delay in ms before showing (default 500).
     */
    show(text, delay) {
        if (!this.el) return;
        if (this._textEl) this._textEl.textContent = text;
        this.el.classList.remove('hidden');

        // Start hidden for animation
        this.el.classList.remove('visible');

        clearTimeout(this._timeout);
        this._timeout = setTimeout(() => {
            this.el.classList.add('visible');
        }, delay != null ? delay : 500);
    }

    /** Hide the fun fact card. */
    hide() {
        if (!this.el) return;
        this.el.classList.remove('visible');
        clearTimeout(this._timeout);
        this._timeout = setTimeout(() => {
            this.el.classList.add('hidden');
        }, 500); // wait for transition to finish
    }
}
