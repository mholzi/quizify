/**
 * Quizify — AnswerButtons component.
 *
 * Usage:
 *   const answers = new AnswerButtons('answers-container');
 *   answers.setAnswers([{text: 'Paris', index: 0}, ...]);
 *   answers.onAnswer(idx => ws.send({type: 'submit_answer', answer_index: idx}));
 *   answers.showResult(0);  // highlight correct = index 0
 */

class AnswerButtons {
    /**
     * @param {string} containerId  ID of the container element.
     */
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this._callback = null;
        this._selectedIndex = null;
        this._disabled = false;
        this._answers = [];
    }

    static LABELS = ['A', 'B', 'C', 'D'];

    /**
     * Render answer buttons.
     * @param {Array<{text: string, index: number}>} answers
     */
    setAnswers(answers) {
        this._answers = answers;
        this._selectedIndex = null;
        this._disabled = false;
        if (!this.container) return;

        this.container.innerHTML = '';
        answers.forEach((a, i) => {
            const btn = document.createElement('button');
            btn.className = 'answer-btn';
            btn.dataset.index = a.index;
            btn.innerHTML = `
                <span class="answer-label">${AnswerButtons.LABELS[i] || i + 1}</span>
                <span class="answer-text">${this._escapeHtml(a.text)}</span>
            `;
            btn.addEventListener('click', () => this._handleClick(a.index));
            this.container.appendChild(btn);
        });
    }

    /** Register answer callback. */
    onAnswer(callback) {
        this._callback = callback;
    }

    /**
     * Show correct/wrong highlighting.
     * @param {number} correctIndex  The correct answer's index.
     */
    showResult(correctIndex) {
        this.disable();
        if (!this.container) return;

        const btns = this.container.querySelectorAll('.answer-btn');
        btns.forEach(btn => {
            const idx = parseInt(btn.dataset.index, 10);
            if (idx === correctIndex) {
                btn.classList.add('correct');
            } else if (idx === this._selectedIndex) {
                btn.classList.add('wrong');
            } else {
                btn.classList.add('dimmed');
            }
        });
    }

    /** Disable all buttons (after submitting). */
    disable() {
        this._disabled = true;
        if (!this.container) return;
        this.container.querySelectorAll('.answer-btn').forEach(btn => {
            btn.disabled = true;
        });
    }

    /** Remove an answer (joker power-up). */
    eliminate(index) {
        if (!this.container) return;
        const btn = this.container.querySelector(`[data-index="${index}"]`);
        if (btn) {
            btn.classList.add('eliminated');
            btn.disabled = true;
        }
    }

    /** Reset for a new round. */
    reset() {
        this._selectedIndex = null;
        this._disabled = false;
        if (this.container) this.container.innerHTML = '';
    }

    /* ---- internal ---- */

    _handleClick(index) {
        if (this._disabled || this._selectedIndex !== null) return;
        this._selectedIndex = index;

        // Highlight selected
        if (this.container) {
            this.container.querySelectorAll('.answer-btn').forEach(btn => {
                const idx = parseInt(btn.dataset.index, 10);
                btn.classList.toggle('selected', idx === index);
            });
        }

        this.disable();

        if (this._callback) this._callback(index);
    }

    _escapeHtml(text) {
        const el = document.createElement('span');
        el.textContent = text;
        return el.innerHTML;
    }
}
