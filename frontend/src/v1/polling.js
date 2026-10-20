// One completed GET schedules the next. stop() invalidates even a response ignoring AbortSignal.
export class PollLoop {
  constructor({ read, value, error, hidden = () => false, setTimer = (callback, delay) => setTimeout(callback, delay), clearTimer = identifier => clearTimeout(identifier) }) {
    Object.assign(this, { read, value, error, hidden, setTimer, clearTimer }); this.epoch = 0; this.active = false; this.busy = false; this.failures = 0;
  }
  start() { this.stop(); this.active = true; this.failures = 0; this.tick(); }
  stop() { this.active = false; this.epoch += 1; this.controller?.abort(); this.clearTimer(this.timer); this.timer = null; this.busy = false; }
  refresh() { if (!this.active) { this.start(); return; } this.clearTimer(this.timer); if (this.busy) this.immediate = true; else this.tick(); }
  async tick() {
    if (!this.active || this.busy) return;
    this.busy = true; const epoch = this.epoch; this.controller = new AbortController(); let terminal = false;
    try {
      const result = await this.read(this.controller.signal);
      if (!this.active || epoch !== this.epoch) return;
      this.failures = 0; this.value(result); terminal = ['completed', 'failed', 'imported'].includes(result.status);
    } catch (failure) {
      if (!this.active || epoch !== this.epoch || failure.name === 'AbortError') return;
      this.failures += 1; this.error(failure); terminal = [400, 401, 403, 404, 422].includes(failure.status);
    } finally {
      if (this.active && epoch === this.epoch) {
        this.busy = false;
        if (terminal) this.active = false;
        else {
          const delay = this.immediate ? 0 : this.hidden() ? 15000 : this.failures ? Math.min(15000, 2000 * 2 ** (this.failures - 1)) : 2000;
          this.immediate = false; this.timer = this.setTimer(() => this.tick(), delay);
        }
      }
    }
  }
}
