/*
 * Which day is it, and which slate should the page open on?
 *
 * Harder than it sounds, and it is where the "Today button goes to yesterday"
 * bug lived. Three clocks are in play and they disagree:
 *
 *   - the viewer's device clock, in whatever zone they are in
 *   - Eastern time, which is the clock a baseball schedule is written against
 *   - the build clock, whenever GitHub last ran the job
 *
 * The old code asked the build which day it was. If the last run happened at
 * 10pm Eastern, its answer stayed "yesterday" all through the small hours, so
 * tapping Today walked you backwards. The page now works out Eastern's date
 * from the viewer's own clock and only falls back to what has actually been
 * built when today's slate does not exist yet.
 *
 * Pure functions, no DOM, so they can be tested outside a browser.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MLBSchedule = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /** Today's date in Eastern time, as YYYY-MM-DD, from any device clock. */
  function easternDate(now) {
    const d = now || new Date();
    try {
      // en-CA formats as YYYY-MM-DD, and the zone handles daylight saving.
      return new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
      }).format(d);
    } catch (err) {
      // No Intl zone data: approximate. Eastern is UTC-4 or UTC-5; -5 is the
      // safe guess because being an hour early only risks showing yesterday's
      // slate just after midnight, which is what a viewer wants anyway.
      const t = new Date(d.getTime() - 5 * 3600 * 1000);
      return t.toISOString().slice(0, 10);
    }
  }

  function addDays(ds, n) {
    const d = new Date(ds + "T12:00:00Z");
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  /**
   * Pick the slate to open on.
   *
   * Prefer the viewer's own today. If nothing has been built for it - the
   * overnight run has not landed, or the job is failing - fall back to the most
   * recent day that does exist, and say so, rather than showing an empty page.
   */
  function resolveDate(dates, today, requested) {
    const have = (dates || []).slice().sort();
    if (!have.length) return { date: requested || today, reason: "none", stale: false };
    if (requested && have.includes(requested))
      return { date: requested, reason: "requested", stale: false };
    if (have.includes(today)) return { date: today, reason: "today", stale: false };

    const past = have.filter(d => d < today);
    if (past.length)
      return { date: past[past.length - 1], reason: "behind", stale: true };
    return { date: have[0], reason: "ahead", stale: false };
  }

  /** How old the newest build is, in hours. */
  function ageHours(generatedAt, now) {
    if (!generatedAt) return null;
    const t = Date.parse(generatedAt);
    if (!isFinite(t)) return null;
    return Math.max(0, ((now || new Date()).getTime() - t) / 3600000);
  }

  /**
   * Should the page reload itself? Either a newer build has been published, or
   * the viewer has sat through midnight while looking at "today".
   */
  function shouldRefresh(state, fresh, now) {
    const out = { reload: false, newDate: null, why: "" };
    if (fresh && state.generatedAt && fresh.generated_at
        && fresh.generated_at !== state.generatedAt) {
      out.reload = true;
      out.why = "a newer build was published";
    }
    const today = easternDate(now);
    if (state.onToday && state.date && state.date !== today) {
      out.reload = true;
      out.newDate = today;
      out.why = "the date rolled over";
    }
    return out;
  }

  return { easternDate, addDays, resolveDate, ageHours, shouldRefresh };
});
