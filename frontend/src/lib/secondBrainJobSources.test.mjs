import assert from "node:assert/strict";
import test from "node:test";
import { defaultJobSourceRules, describeJobSources, jobScopeLabel, jobSourceRules, jobSourceRulesError } from "./secondBrainJobSources.js";

test("new jobs use all locations with a successful-run checkpoint; old jobs retain their scope", () => {
  const rules = defaultJobSourceRules();
  assert.equal(rules.location.kind, "all");
  assert.equal(rules.time.mode, "since-success");
  assert.equal(jobSourceRules({ scope: "Inbox folder" }).location.kind, "inbox");
  const weekly = jobSourceRules({ scope: "Documents added in the last 7 days" });
  assert.deepEqual(weekly.time, { mode: "window", field: "added", count: 7, unit: "days" });
  assert.equal(weekly.location.kind, "all");
});

test("collection and saved-search identities survive renaming and deleted references stay invalid", () => {
  const collections = [{ id: "col-a", name: "Before" }];
  const rules = jobSourceRules({ scope: "Collection: Before" }, collections);
  assert.equal(rules.location.id, "col-a");
  collections[0].name = "After";
  assert.equal(jobScopeLabel(rules, collections), "Collection: After");
  assert.equal(jobSourceRulesError(rules, collections, []), "");
  assert.match(jobSourceRulesError(rules, [], []), /unavailable/);
  const saved = jobSourceRules({ scope: "Saved search: Dots" }, [], [{ id: "search-a", name: "Dots" }]);
  assert.equal(saved.location.id, "search-a");
  assert.equal(saved.location.kind, "saved-search");
});

test("readable descriptions include the combined source constraints", () => {
  const rules = defaultJobSourceRules();
  rules.query = "OpenAI Dots";
  rules.time = { mode: "window", field: "added", count: 24, unit: "hours" };
  rules.tags = ["research"];
  rules.excludeTags = ["archived"];
  rules.unreadOnly = true;
  const description = describeJobSources(rules);
  assert.match(description, /all words in "OpenAI Dots"/);
  assert.match(description, /added in the last 24 hours/);
  assert.match(description, /excluding tags: archived/);
  assert.match(description, /unread only/);
});

test("unsafe folders and fractional or empty time windows cannot be saved", () => {
  const rules = defaultJobSourceRules();
  rules.location = { kind: "folder", path: "../other", recursive: true };
  assert.match(jobSourceRulesError(rules), /relative folder/);
  rules.location.path = "Research/OpenAI";
  assert.equal(jobSourceRulesError(rules), "");
  rules.time.mode = "window";
  for (const count of [0, 1.5, 87601]) {
    rules.time.count = count;
    assert.match(jobSourceRulesError(rules), /whole hours or days/);
  }
});
