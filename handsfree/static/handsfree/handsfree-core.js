(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.HandsfreeCore = api;
})(globalThis, function () {
  "use strict";

  function smoothPoint(previous, next, alpha = 0.35) {
    if (!previous) return { x: next.x, y: next.y };
    const weight = Math.max(0, Math.min(1, alpha));
    return {
      x: previous.x + (next.x - previous.x) * weight,
      y: previous.y + (next.y - previous.y) * weight,
    };
  }

  function snapTarget(point, targets, radius, directTarget = null) {
    if (directTarget) {
      const direct = targets.find((target) => target.element === directTarget);
      if (direct) return direct;
    }
    let nearest = null;
    let shortest = radius;
    for (const target of targets) {
      const distance = Math.hypot(target.x - point.x, target.y - point.y);
      if (distance <= shortest) {
        nearest = target;
        shortest = distance;
      }
    }
    return nearest;
  }

  class DwellTracker {
    constructor(duration = 1400) {
      this.duration = duration;
      this.target = null;
      this.startedAt = null;
    }

    update(target, now) {
      if (!target) {
        this.reset();
        return { progress: 0, clicked: false };
      }
      if (target !== this.target) {
        this.target = target;
        this.startedAt = now;
      }
      const elapsed = now - this.startedAt;
      const progress = Math.max(0, Math.min(1, elapsed / this.duration));
      if (elapsed < this.duration) return { progress, clicked: false };
      this.reset();
      return { progress: 1, clicked: true };
    }

    reset() {
      this.target = null;
      this.startedAt = null;
    }
  }

  class PinchDetector {
    constructor(closeThreshold = 0.05, openThreshold = 0.08, debounceMs = 150) {
      this.closeThreshold = closeThreshold;
      this.openThreshold = openThreshold;
      this.debounceMs = debounceMs;
      this.pressed = false;
      this.candidate = null;
      this.candidateSince = 0;
    }

    update(distance, now) {
      const desired = this.pressed
        ? (distance > this.openThreshold ? false : true)
        : (distance < this.closeThreshold ? true : false);
      if (desired === this.pressed) {
        this.candidate = null;
        return { pressed: this.pressed, started: false, released: false };
      }
      if (desired !== this.candidate) {
        this.candidate = desired;
        this.candidateSince = now;
        return { pressed: this.pressed, started: false, released: false };
      }
      if (now - this.candidateSince < this.debounceMs) {
        return { pressed: this.pressed, started: false, released: false };
      }
      this.pressed = desired;
      this.candidate = null;
      return {
        pressed: this.pressed,
        started: this.pressed,
        released: !this.pressed,
      };
    }

    reset() {
      this.pressed = false;
      this.candidate = null;
      this.candidateSince = 0;
    }
  }

  class PalmPauseDetector {
    constructor(holdMs = 1000) {
      this.holdMs = holdMs;
      this.startedAt = null;
      this.latched = false;
    }

    update(openPalm, now) {
      if (!openPalm) {
        this.startedAt = null;
        this.latched = false;
        return false;
      }
      if (this.startedAt === null) this.startedAt = now;
      if (!this.latched && now - this.startedAt >= this.holdMs) {
        this.latched = true;
        return true;
      }
      return false;
    }

    reset() {
      this.startedAt = null;
      this.latched = false;
    }
  }

  function scrollDrag(previousY, currentY, sensitivity = 1) {
    return (previousY - currentY) * sensitivity;
  }

  return { smoothPoint, snapTarget, DwellTracker, PinchDetector, PalmPauseDetector, scrollDrag };
});
