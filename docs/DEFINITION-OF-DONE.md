# The definition of done — the three layers

A guide to the **Verify** page of the CODE tab (▶ VERIFY) and the `/verify` and
`/goal` chat commands: what «this is finished» means, who decides it, and how to
configure it. With the traps, which are half the subject.

«Is it done?» does not have one answer, it has three, and mixing them up is how a
turn calls a job finished when it is not.

| Layer | What it answers | Who decides | Measured? |
|---|---|---|---|
| **Exit code** | did you break it? | the command (`pytest`, `make`, `go build`) | yes, automatically |
| **Check goal** | does it reach it? | a **model judging that command's output** against your words | judged, with its why said |
| **Work goal** | is it what I asked for? | the model before starting, the reviewer when judging | no: these are words |

The three live together: the first two on the Verify page (two boxes: the
command, and what you expect to see in its output), the third as `/goal` in the
chat. The last two are both free text; the difference is WHEN they rule: the
check goal is judged against the run's output every time the command runs; the
work goal is the reviewer's bar for the whole answer.

---

## 1. The exit code — the regression gate

A command that exits 0 as long as the work still holds. It is the Command box
of the Verify page, and the same value `/verify` sets: the one the agentic loop
uses as the bar. Running a command remembers it in the «Before» list; a click
there fills the boxes and shows that run's output again, without re-running.

```
pytest -q
make -s
go build ./... && go test ./...
```

It answers «did you break it?», **not** «did it get better?». Empty is an honest
answer; a fake command is worse than none, because it gives you a green light
without having looked at anything.

**And here is its limit**, which is the reason layer 2 exists:

- `llama-bench` exits **0** at 2000 tokens/s of prefill and at 200.
- A `build` that got twice as slow is still **green**.
- A benchmark script exits **0** whether the number went up or down.

In all of those the oracle is not the status, it is a **number in the output**.

---

## 2. The check goal — words, judged against the output

Written in the box **below the command** on the Verify page. It belongs to
**that command**: pick another command from «Before» and the box shows that
one's expectation.

Write it the way you would tell a person:

```
no errors in the log
pp512 above 2000
go from 50 to 60 tok/s on llama-server
the TOTAL at 2% or better, not just the best case
```

The run's output already travels to a model, so **the model is the judge**.
When the command exits 0 and a goal is set, the output and your words go to the
judge (`POST /code/judge`, which calls `fed_agent.judge_output`, the same judge
the agentic loop asks: one reader, not two), and the verdict is **exit 0 AND
the judge saying the bar is met**, with its one-line why on the banner (🎯).

### The three honest states

- **No goal**: the verdict is the exit code alone, and the label under the box
  says so.
- **Goal, judged**: green means exit 0 **and** the judge's yes; a ✗ names why
  («1840, below the 2000 you asked for»).
- **Goal, nobody judged** (no endpoint, an unreadable answer): the verdict
  stays on the exit code and the banner **says** the goal went unjudged —
  never a green that means «I could not look», never an invented red.

### The traps that are still yours

- **The judge reads the output, not your intentions.** «Improve tokens/s» when
  the run prints nothing about speed is an honest NO: put the number in the
  output (`RESULT tok_s 61.5`) and point your words at it.
- **Ambiguity costs.** «Better performance» makes the judge guess; «pp512
  above 2000» does not. The clearer the bar, the more the verdict is worth.
- **A red command is not judged.** Red is already red; the goal only rules on
  runs that exit 0 — the judge is an LLM call and it is not spent refining a
  verdict that cannot change.

---

## 3. The work goal — words, and they are yours

Set in the chat with `/goal <text>` (`/goal` alone shows it, `/goal off` clears
it). It is for what **no command can check**: «at least 3 breeds backed by
veterinary sources», «the report must cite both contradicting sources».

- The model reads it **before starting** (finding out the bar after failing costs
  a whole loop).
- The reviewer uses it as the **acceptance criterion**. Without it, it can only
  judge coherence, so a well-written useless answer sails past.
- It is **per tab**, and travels in every turn of that tab.

It is not measured, and nothing pretends it is. That is the whole difference from
layer 2.

---

## 4. How they bind the loop (not just the button)

With an active **●** check, a HOPLITA turn in that project cannot end on a
«finished» the oracle contradicts:

1. The model says it is done.
2. The loop runs the check's command.
3. It judges the goal with **the same judge** the Verify page's button asks
   (`fed_agent.judge_output`, shared on purpose: not two readers that agree
   until they don't).
4. If it exits non-zero **or** the judge calls the bar missed, the output and
   the judge's why go back to the model («you got 1840, the bar was 2000»)
   and the turn **continues**, on the same iteration budget. No new loop, no
   new clock.
5. If it ends green, the stamp at the end of the answer says all of it:
   ``[verified: `./bench.sh` exits 0 and meets `TOTAL at 2% or better`]``. A goal
   nobody could judge is stamped as exactly that (``— goal `…` NOT judged``),
   never absorbed into «meets».

If the iterations run out with the oracle still red, the answer ships marked at
the top (``[⚠ `./bench.sh` STILL FAILS — …]``, with the last output): an answer
that declares itself good never travels without the one command that could
disprove it having run.

**A check that cannot run is a third answer, not a red.** If the firewall of the
tab (or the general one) refuses `run_command`, the check is not «still failing»:
nobody checked anything. It is said once, on screen and at the head of the
answer; the model is not told its work is wrong, nothing is retried, and the
turn ends on the model's answer. You are warned earlier too: when you block the
shell in a tab that has a check, and when you set a check with `/verify` in a
tab whose shell is blocked.

**The reviewer sees the check as a step with its verdict** («exit 0, PASSED»,
«exit ≠0, FAILED», «did NOT run») next to the other steps it judges the answer
against. It never reads the test's output: the judge of the expectation does.

---

## 5. Where each thing lives

| Thing | Stored in | Scope |
|---|---|---|
| «Before» (commands that ran) | `QSettings` (this machine) | per project |
| Each check's goal | `QSettings`, keyed by the command | per project |
| The command box | `ctx.verify_cmd` + encrypted session | per tab |
| Work goal | `ctx.goal` + encrypted session | per tab |
| Run log | in memory | per project, current session |

Three fields travel in the turn: `verify` (the command), `verify_goal` (its goal)
and `goal` (the work goal in words). A cron does **not** inherit them: the job's
instruction is its own contract. API reference: [`docs/API.md`](API.md).

---

## Quick reference

```
EXIT CODE       pytest -q                        did you break it?
CHECK GOAL      «TOTAL at 2% or better»          does it reach it?
                «no errors in the log»           (words: a model judges them
                «pp512 above 2000»                against the run's output)
WORK GOAL       «must cite both sources»         is it what I asked?  (the
                                                  reviewer's bar, words too)

done = exit 0  AND  the judge calls the bar met  AND  the work goal is met
```
