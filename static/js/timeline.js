/* Timeline / animation engine for the radar frames.
 * Manages the combined observed→forecast track, the play/pause/step controls,
 * and the "NOW" divider. Calls onFrame(frame) whenever the selection changes.
 */
(function (global) {
  "use strict";

  class Timeline {
    constructor(opts) {
      this.container = opts.container;
      this.onFrame = opts.onFrame || (() => {});
      this.onLive = opts.onLive || (() => {});
      this.btnPlay = document.getElementById("tl-play");
      this.timeEl = document.getElementById("tl-time");
      this.kindEl = document.getElementById("tl-kind");
      this.trackEl = document.getElementById("tl-track");
      this.speedSel = document.getElementById("tl-speed");

      this.frames = [];          // combined [{time,url,kind,lead_min,...}]
      this.index = -1;
      this.live = true;
      this.playing = false;
      this._timer = null;

      document.getElementById("tl-step-back").addEventListener("click", () => this.step(-1));
      document.getElementById("tl-step-fwd").addEventListener("click", () => this.step(1));
      this.btnPlay.addEventListener("click", () => (this.playing ? this.pause() : this.play()));
      this.speedSel.addEventListener("change", () => { if (this.playing) { this.pause(); this.play(); } });
      document.getElementById("tl-live").addEventListener("click", () => this.goLive());
    }

    setFrames(observed, forecast) {
      const prevLive = this.live;
      this.frames = []
        .concat((observed || []).map(f => ({ ...f, kind: "observed" })))
        .concat((forecast || []).map(f => ({ ...f, kind: "forecast" })));
      this._render();

      if (prevLive || this.index < 0 || this.index >= this.frames.length) {
        this.goLive();
      } else {
        this._select(this.index);
      }
    }

    _render() {
      const n = this.frames.length;
      this.trackEl.innerHTML = "";
      if (!n) return;
      const obsCount = this.frames.filter(f => f.kind === "observed").length;

      this.frames.forEach((f, i) => {
        const tick = document.createElement("div");
        tick.className = "tl-tick " + (f.kind === "observed" ? "obs" : "fct");
        tick.style.left = (i / n) * 100 + "%";
        tick.title = new Date(f.time).toLocaleString();
        tick.addEventListener("click", () => this._select(i, false));
        this.trackEl.appendChild(tick);
      });

      // NOW divider at the observed/forecast boundary
      if (obsCount > 0 && obsCount < n) {
        const now = document.createElement("div");
        now.className = "tl-nowline";
        now.style.left = (obsCount / n) * 100 + "%";
        this.trackEl.appendChild(now);
      }

      const thumb = document.createElement("div");
      thumb.className = "tl-thumb";
      thumb.id = "tl-thumb";
      this.trackEl.appendChild(thumb);
    }

    _select(i, autoLiveOk = true) {
      const n = this.frames.length;
      if (!n) return;
      i = Math.max(0, Math.min(n - 1, i));
      this.index = i;
      const f = this.frames[i];
      const obsCount = this.frames.filter(x => x.kind === "observed").length;
      // "live" means the latest observed frame is selected
      this.live = autoLiveOk === false ? false : (i === obsCount - 1);
      this.onLive(this.live);

      this.onFrame(f);
      this.timeEl.textContent = fmtTime(f.time);
      this.kindEl.textContent = f.kind === "forecast" ? `FORECAST +${f.lead_min} min` : "OBSERVED";

      const thumb = document.getElementById("tl-thumb");
      if (thumb) thumb.style.left = (i / n) * 100 + "%";
    }

    step(dir) { this.pause(); this._select(this.index + dir, false); }
    goLive() {
      this.pause();
      const obsCount = this.frames.filter(x => x.kind === "observed").length;
      if (obsCount > 0) this._select(obsCount - 1, true);
    }
    play() {
      if (!this.frames.length) return;
      this.playing = true;
      this.btnPlay.textContent = "⏸";
      const step = () => {
        if (!this.playing) return;
        let next = this.index + 1;
        if (next >= this.frames.length) next = 0;       // loop
        this._select(next, false);
        this._timer = setTimeout(step, parseInt(this.speedSel.value, 10) || 500);
      };
      this._timer = setTimeout(step, parseInt(this.speedSel.value, 10) || 500);
    }
    pause() {
      this.playing = false;
      this.btnPlay.textContent = "▶";
      if (this._timer) clearTimeout(this._timer);
      this._timer = null;
    }
  }

  function fmtTime(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Prague" })
        + " · " + d.toLocaleDateString("cs-CZ", { day: "2-digit", month: "2-digit", timeZone: "Europe/Prague" });
    } catch { return iso; }
  }

  global.Timeline = Timeline;
})(window);
