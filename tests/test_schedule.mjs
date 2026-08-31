/*
 * Date-resolution tests. Runs in plain node.
 *
 *     node tests/test_schedule.mjs
 *
 * The bug these exist to prevent: the Today button walked backwards. It asked
 * the build which day it was, and a build that last ran at 10pm Eastern kept
 * answering "yesterday" all through the small hours.
 */
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const S = require("../docs/schedule.js");

let fails = 0;
const check = (name, cond, detail = "") => {
  if (cond) console.log(`  PASS  ${name}`);
  else { console.log(`  FAIL  ${name}  ${detail}`); fails++; }
};
const at = iso => new Date(iso);

console.log("\n[eastern date from any clock]");
check("mid-afternoon UTC", S.easternDate(at("2026-08-24T16:00:00Z")) === "2026-08-24");
check("late evening Eastern is still that day",
  S.easternDate(at("2026-08-25T03:30:00Z")) === "2026-08-24",
  S.easternDate(at("2026-08-25T03:30:00Z")));
check("just past Eastern midnight is the new day",
  S.easternDate(at("2026-08-25T04:30:00Z")) === "2026-08-25",
  S.easternDate(at("2026-08-25T04:30:00Z")));
check("winter keeps daylight saving straight",
  S.easternDate(at("2026-11-15T04:30:00Z")) === "2026-11-14",
  S.easternDate(at("2026-11-15T04:30:00Z")));
check("summer keeps daylight saving straight",
  S.easternDate(at("2026-07-15T03:30:00Z")) === "2026-07-14",
  S.easternDate(at("2026-07-15T03:30:00Z")));

console.log("\n[picking the slate to open]");
const built = ["2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24"];
check("opens on today when today exists",
  S.resolveDate(built, "2026-08-23").date === "2026-08-23");
check("a pinned date wins",
  S.resolveDate(built, "2026-08-23", "2026-08-21").date === "2026-08-21");
check("a pinned date that was never built is ignored",
  S.resolveDate(built, "2026-08-23", "2019-04-01").date === "2026-08-23");

// the actual bug: it is 1am and the overnight build has not landed
const behind = S.resolveDate(built, "2026-08-25");
check("falls back to the newest built day, not an empty page",
  behind.date === "2026-08-24", behind.date);
check("and says it is behind", behind.stale === true && behind.reason === "behind");
check("never walks backwards past the newest day",
  S.resolveDate(built, "2026-08-30").date === "2026-08-24");
check("handles an empty feed without throwing",
  S.resolveDate([], "2026-08-23").date === "2026-08-23");
check("opens forward if every slate is in the future",
  S.resolveDate(["2026-09-01"], "2026-08-23").date === "2026-09-01");

console.log("\n[staying current]");
check("age of a six-hour-old build",
  Math.abs(S.ageHours("2026-08-24T10:00:00Z", at("2026-08-24T16:00:00Z")) - 6) < 1e-6);
check("no timestamp, no age", S.ageHours(null) === null);
check("garbage timestamp, no age", S.ageHours("not a date") === null);

const sitting = { generatedAt: "2026-08-24T10:00:00Z", date: "2026-08-24", onToday: true };
check("a new build triggers a reload",
  S.shouldRefresh(sitting, { generated_at: "2026-08-24T14:00:00Z" },
                  at("2026-08-24T16:00:00Z")).reload);
check("the same build does not",
  !S.shouldRefresh(sitting, { generated_at: "2026-08-24T10:00:00Z" },
                   at("2026-08-24T16:00:00Z")).reload);
const rolled = S.shouldRefresh(sitting, { generated_at: "2026-08-24T10:00:00Z" },
                               at("2026-08-25T05:00:00Z"));
check("sitting through midnight moves to the new day",
  rolled.reload && rolled.newDate === "2026-08-25", JSON.stringify(rolled));
check("a viewer who navigated away is left where they are",
  !S.shouldRefresh({ ...sitting, onToday: false },
                   { generated_at: "2026-08-24T10:00:00Z" },
                   at("2026-08-25T05:00:00Z")).reload);

console.log("\n[day arithmetic]");
check("forward a day", S.addDays("2026-08-31", 1) === "2026-09-01");
check("back a day", S.addDays("2026-03-01", -1) === "2026-02-28");
check("across a daylight-saving change", S.addDays("2026-11-01", 1) === "2026-11-02");

console.log("\n" + "=".repeat(60));
if (fails) { console.log(`${fails} FAILURE(S)`); process.exit(1); }
console.log("all schedule checks passed");
