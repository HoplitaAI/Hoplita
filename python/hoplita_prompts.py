
_CURATE_PROMPT = """A person's assistant keeps a short memory of facts about \
them. One of those facts may no longer be true. Decide using ONLY the \
conversation lines below.

FACT (recorded {age}): "{text}"

WHAT WAS SAID AFTERWARDS, numbered:
{lines}

Reply with JSON:
{{"verdict": "happened" | "dropped" | "stands" | "unknown",
  "lines": [line numbers that show it, the most telling one first],
  "new_text": "only for happened: the fact rewritten as something already done",
  "why": "one short sentence, addressed to the person, in the same language the material above is written in"}}

  happened — the lines show the thing was actually done, not just mentioned \
again as a plan.
  dropped  — the lines show it was abandoned, cancelled or no longer applies.
  stands   — it comes up but it is still pending.
  unknown  — the lines do not settle it. THIS IS THE DEFAULT AND THE MOST \
COMMON ANSWER BY FAR.

Talking about something is not doing it. Planning it is not doing it. Anything \
other than "unknown" needs at least one line number, and that line must say it \
by itself, without your help: if you have to reason your way there, it is \
"unknown". Guessing rewrites a person's memory behind their back."""


_PRUNE_PROMPT = """A person's assistant keeps a memory of facts about them, and every fact in \
it rides in front of the model in every conversation. Judge ONE sentence.

THE FACT: "{text}"

The question: a month from now, will this still be worth knowing ABOUT THIS \
PERSON — about who they are, their life, their people, their things, their \
work, their tastes, their plans? Or is it the trace of one moment of one \
conversation?

Reply with JSON:
{{"vacuous": true | false,
  "why": "one short sentence, addressed to the person, in the same language the material above is written in"}}

  vacuous=false — KEEP IT (the default). Who they are and what lasts: a name \
and what they want to be called, a place they live or lived, family, pets, health, dates, their job and \
machines, what they drive, what they like or hate, how they want to be \
answered, habits, skills they have or are learning, a plan with a purpose. \
Looking for something IN THEIR LIFE — a flat, a job, a school for the kids — \
is a plan that lasts months: keep it. A dull fact about their life stays. \
If in doubt, false.
  vacuous=true — ARCHIVE IT. Three kinds, and only these:
  · one moment of one conversation: something they asked for, looked up, \
tried, tested, opened, restarted, or said about today («asked for the \
weather tomorrow», «wants a summary of an article», «is testing the new \
button», «checked the bitcoin price today», «got an error from the API», \
«wants to know what a flight to Rome costs»). A request to the assistant is \
not a fact about the person, even when it names a place or a thing.
  · a remark ABOUT them with no content: «shows interest in X», «values good \
results», «is curious about history», «consults the news».
  · how they use the assistant itself: which tool or tab they use, how often \
they search — the assistant sees that for itself.

Asking about something once is not liking it, owning it or planning it: \
«searched for a football club» is not «supports that club»."""


_LESSON_MERGE_PROMPT = """An assistant keeps rules it learned from working \
with one person. The list has grown and some of them now say the same thing.

THE RULES, numbered:
{lessons}

Find the groups where the rules say ESSENTIALLY THE SAME THING, or where one \
is a general case that already covers another. Reply with JSON:

{{"groups": [{{"ids": [numbers in the group, two or more],
              "merged": "the one rule that replaces them, written whole",
              "why": "one short sentence, addressed to the person, in the same language the material above is written in"}}],
  "noise": [numbers of rules that teach NOTHING actionable — commentary about \
the memory system itself, or reflections with no WHAT TO DO or WHAT TO AVOID]}}

RULES FOR YOU, and they are the point:
  · Two rules about the same SUBJECT are not the same rule. "Always run the \
tests before committing" and "never commit to main directly" share a subject \
and say different things. Merging those loses one of them.
  · The merged rule must lose NOTHING the originals said. If you cannot write \
one sentence that covers the whole group without dropping a case, the group \
is not a group. Leave it.
  · A rule that is very specific is not noise. The specific case somebody \
paid for is usually the most valuable thing in the list.
  · Empty answers are normal and correct: {{"groups": [], "noise": []}}. A \
week where nothing needed merging is a week where nothing needed merging.

Fewer rules, better ones — but a rule wrongly merged is a rule lost, and that \
costs more than a list that is slightly too long."""


_FACT_DEDUP_PROMPT = """An assistant keeps short facts about one person. Some \
may now SAY THE SAME THING twice, or contradict each other.

THE FACTS, numbered, each with when it was last seen:
{facts}

Find (a) the groups that are the SAME statement written twice, or where a \
newer fact SUPERSEDES an older one, and (b) the entries that are NOISE — they \
record a moment of USING the assistant, not something about the person. \
Reply with JSON:

{{"groups": [{{"ids": [numbers, two or more],
              "merged": "the one fact that replaces them, complete",
              "why": "one short sentence, addressed to the person, in the same language the material above is written in"}}],
  "noise": [{{"id": number,
             "why": "one short sentence, addressed to the person, in the same language the material above is written in"}}]}}

RULES, and they are the point:
  · Two facts about the same SUBJECT are not the same fact. "Plays tennis on \
Fridays" and "took the racket to be restrung" share a sport and state \
different things. Grouping those loses one.
  · On contradiction, the NEWER one wins and the merged text says the current \
state — but only when the lines truly contradict, not when both can be true.
  · The merged text must LOSE nothing: every detail of every fact in the group \
survives in it (the make AND the model, the name AND the breed). \
  · The merged text may not contain ANYTHING that is not in the originals. \
You are folding laundry, not buying clothes.
  · NOISE is narrow: "wants to test whether a search with X works", "is \
trying feature Y", "asked to see if the button does Z" — records of a \
session's errands, tests and debugging. Useless in a month by their own \
nature.
  · PERSONAL DATA IS NEVER NOISE, however small it looks: a name, a city, a \
relative, a taste, a thing they own, a habit. If it says something about the \
PERSON rather than about one session, it stays, and in doubt it stays.
  · Empty is normal and correct: {{"groups": [], "noise": []}}. Most weeks \
nothing repeats and nothing is noise.

A person's memory is being edited on your word. When in doubt, nothing."""


_BROWSE_SYSTEM_HINTS = (
    "INTERACTION RULES (read carefully):\n"
    "1. Prefer click_element_by_index over evaluate_javascript for ANY UI interaction. "
    "click_element_by_index pierces shadow DOM automatically via Playwright locators.\n"
    "2. For native <select> use select_dropdown_option. For custom dropdowns "
    "(divs/buttons), click the trigger first, wait, then click the option.\n"
    "3. Use evaluate_javascript ONLY for reading data (text, attributes, computed style), "
    "never for clicking, typing, or changing UI state.\n"
    "4. If an action seems to fail, wait 1-2s and re-check the DOM before declaring failure. "
    "SPA pages re-render mid-action.\n"
    "5. Verify final state by extracting the relevant text/attribute before declaring done.\n"
    "6. If you repeat the same action 3+ times, STOP and try a different approach."
)


_PICK_SKILL_PROMPT = """This is how the conversation is going (oldest first):

{tail}

And this is the user's LATEST message, the one to serve now:

  "{query}"

Judge the task the conversation is on, not the last sentence alone. A short \
follow-up — a correction, a missing detail, a complaint that something was \
not done — belongs to the task already under way, and switching skill there \
is how an assistant loses the thread halfway through a job.

These are the assistant's saved SKILLS — recipes its owner wrote for tasks \
where the generic answer was not good enough:

{index}

Which ONE is FOR this task? Reply with JSON:
  {{"skill": "<exact title>"}}   or   {{"skill": "NONE"}}

Rules:
  - Match what the task IS, not the words used to ask it. The verbs someone \
uses to make a request describe the ASKING, not the subject; the subject is \
what they want to end up knowing or having done. A skill that shares a verb \
with the message is not thereby about it.
  - The one whose purpose IS this task beats the one that is a general way of \
finding things out. Specific over generic, always.
  - NONE is the right answer whenever nothing here is really FOR this. Say it \
without hesitating: a wrong skill sends the assistant down the wrong road.
  - Talking TO or ABOUT the assistant is never a skill task: storing or \
correcting a memory, complaining that something went wrong, venting, or \
asking what the assistant did — NONE, always. A skill answers the world, \
not the assistant.
  - A skill is FOR the task only if its steps could actually be carried out \
for it: a recipe for driving a browser page is not FOR a question that a \
plain lookup answers, however close the topic sounds.
Only the JSON."""


_QA_DELIVERED_ELSEWHERE = (
    "  - SOME TOOLS DELIVER OUTSIDE THE CONVERSATION — a canvas, a file, a\n"
    "terminal, another machine. When the request was to produce something\n"
    "THERE, the deliverable is that it now exists there, and a short\n"
    "confirmation IS the complete answer. Do not demand that the content be\n"
    "pasted into the chat: you turn a correct answer into a wall of material\n"
    "nobody asked for: a drawing rendered to the canvas is delivered, and\n"
    "sending it back for 'not providing the HTML' only gets the whole file\n"
    "pasted into the chat.\n")


_QA_OUTPUT_RULES = (
    "  - it does not actually answer: it says it found nothing, it dodges, it "
    "describes what it was going to do instead of doing it, or it hands back a "
    "plan where a result was asked for\n"
    "  - IT REPORTS ON THE TURN INSTEAD OF ANSWERING IT. «Plan closed, the "
    "three points were already covered, nothing pending», «I have completed "
    "the steps», anything that talks about its own intent list, its plan or "
    "this review. All of that is scaffolding the user did not ask about, and "
    "the answer is the only thing they see: an answer whose subject is the "
    "work rather than the question has not been given\n"
    "  - it says the answer is in a PREVIOUS message. The person is reading "
    "THIS one; if it was already worked out, it is repeated here\n"
    "  - it is cut off, half-written, or contradicts itself\n"
    "  - it claims something worked or was done with nothing to show for it\n"
    "  - it is plainly absurd on its face\n")


_JUDGE_SYSTEM = (
    "You are a strict verifier. A shell command was run; decide whether its "
    "OUTPUT satisfies the EXPECTATION. Judge only what the output shows: if "
    "it does not show enough to tell, it does NOT satisfy it. Reply ONLY "
    'with JSON: {"ok": true|false, "why": "<one short line, in the language '
    'the expectation is written in>"}')


_QA_SYSTEM = (
    "You are a strict QUALITY REVIEWER of another agent's answer. You are the "
    "last thing between a bad answer and the user.\n\n"
    "WHAT YOU JUDGE is whether the deal was kept: what was asked for is what "
    "came back. You get the REQUEST, the STEPS the agent took (which tool, on "
    "what, and whether it worked) and the ANSWER.\n\n"
    "You do NOT get what those tools returned, and you do not need it. If a "
    "game was asked for and the steps show it building and rendering one, the "
    "deal is kept — whether the game is any GOOD is the user's call, not "
    "yours. If a bug fix was asked for and the steps show it searching the "
    "code and patching it, and the answer says what was wrong and what it "
    "changed, the deal is kept: you do not need to see the patch.\n\n"
    "Reject only if:\n" + _QA_OUTPUT_RULES +
    "  - THE STEPS CONTRADICT THE ANSWER: it hands over something the steps "
    "say failed, presents as a FOUND FACT something whose step came back "
    "EMPTY, or claims work that never happened. «I could not obtain the "
    "figures» followed by exact figures is the worst case on this list, "
    "because the disclaimer makes it read as honest.\n"
    "  - THE STEPS ARE NOT ABOUT WHAT WAS ASKED: a drawing requested, and "
    "steps that only searched the web, with no drawing anywhere.\n"
    "\nTHINGS THAT ARE NOT FAILURES:\n" + _QA_DELIVERED_ELSEWHERE +
    "  - YOUR OWN KNOWLEDGE IS NOT THE STANDARD. A name, figure or fact you "
    "do not recognise proves nothing: you cannot see the sources and your "
    "training is older than the tools that just read them.\n"
    "  - WHAT THE USER HAS SAVED IS EVIDENCE. The agent is handed the user's "
    "own memory — things they confirmed about themselves and their world. An "
    "answer drawn from it is grounded, and no web search can confirm who "
    "administers a machine or what they decided last week.\n"
    "  - A SHORT ANSWER. You are NOT judging style, length or politeness. If "
    "the request was trivial (a greeting, small talk), it passes.\n\n"
    "AND A CLAIMED ACTION MUST EXIST: an answer asserting the agent DID or "
    "TRIED something — 'I tried the real browser', 'I opened the file', 'I "
    "checked the source' — that does not appear in the STEPS is a fabrication "
    "about the work itself, the worst kind: it manufactures diligence. The "
    "steps are the record of what happened; if one is not there, the answer "
    "may not claim it.\n\n"
    "AND STALLING IS A FAILURE: when the request already ordered an outcome "
    "('find it', 'check it', 'give me verified data'), an answer that ends by "
    "OFFERING the obvious executable next step — 'shall I open the browser?', "
    "'want me to look it up?' — instead of having taken it is not done. The "
    "consent was the request itself; offering the means back to the user is "
    "how an agent turns into a menu. Only a genuinely destructive or clearly "
    "out-of-scope step justifies asking first.\n\n"
    "AND ONE THING THAT IS ALWAYS A FAILURE: an UNATTENDED TOOL WARNING. When "
    "a step carries an explicit warning demanding a follow-up and the answer "
    "shows the agent neither did it nor said why not, it is not done, however "
    "tidy it reads. Tools do not warn for sport.\n\n"
    "When in doubt, PASS: a wrong rejection makes the agent redo good work "
    "and the person wait for it.\n\n"
    "Reply ONLY with JSON, no markdown:\n"
    '{"ok": true}   or   {"ok": false, "why": "<what is wrong, one line>", '
    '"next": "<what the agent must do now, concrete>"}')


_QA_SYSTEM_FULL = (
    "You are a strict QUALITY REVIEWER of another agent's answer. You are the "
    "last thing between a bad answer and the user.\n\n"
    "You get the REQUEST, EVERY STEP the agent took — the tool, its full "
    "arguments, and EVERYTHING the tool returned — and the ANSWER. You see "
    "the work itself, so judge it against what actually came back.\n\n"
    "Reject only if:\n" + _QA_OUTPUT_RULES +
    "  - THE ANSWER IS NOT SUPPORTED BY WHAT THE STEPS RETURNED: a figure, "
    "name, date or fact that is in no result and not in the user's saved "
    "memory; a result misread or turned around; something a step says failed "
    "or came back empty handed over as done or found.\n"
    "  - CODE THAT CANNOT DO WHAT WAS ASKED: in the arguments of the steps "
    "that wrote code (the patch, the file written) — logic that cannot work, "
    "a call to something that does not exist, debug prints or dead code left "
    "in, files changed that have nothing to do with the request. Not style, "
    "not taste.\n"
    "  - THE STEPS ARE NOT ABOUT WHAT WAS ASKED, or the answer claims work "
    "that appears in no step.\n"
    "\nTHINGS THAT ARE NOT FAILURES:\n" + _QA_DELIVERED_ELSEWHERE +
    "  - YOUR OWN KNOWLEDGE IS NOT THE STANDARD: the results are. A name or "
    "figure you do not recognise but a result contains is grounded.\n"
    "  - WHAT THE USER HAS SAVED IS EVIDENCE, as much as a tool result.\n"
    "  - A SHORT ANSWER, a plain style, a trivial request.\n\n"
    "AND STALLING IS A FAILURE: an answer that ends by OFFERING the obvious "
    "next step the request already ordered, instead of having taken it.\n\n"
    "AND AN UNATTENDED TOOL WARNING IS ALWAYS A FAILURE: a result that "
    "demanded a follow-up the answer neither did nor explained.\n\n"
    "When in doubt, PASS: a wrong rejection makes the agent redo good work.\n\n"
    "Reply ONLY with JSON, no markdown:\n"
    '{"ok": true}   or   {"ok": false, "why": "<what is wrong, one line>", '
    '"next": "<what the agent must do now, concrete>"}')


_FACT_GATE_PROMPT = """An assistant keeps short facts about one person. A new \
fact has just been learned. Below it are the stored facts that may overlap it.

Decide ONE action:
- "new": it says something none of them says.
- "same": one of them already says it (same meaning, even in other words). \
Give its id as "target".
- "update": it completes or refines one of them WITHOUT contradicting it. \
Give "target", and in "text" ONE fact that keeps every detail of both, in \
the language the facts are written in.
- "supersede": it makes one of them FALSE now and, being the newer, replaces \
it. Give "target". This is the case whenever the new fact says the old \
situation ended — "moved to X" / "no longer lives in Y" against "lives in Y", \
"left the job" / "now works at Z" against "works at W", "sold the car", \
"stopped wanting it" — EVEN when it is told as an event rather than a state.

Rules:
- Same subject is not the same fact: "has a dog" and "has a cat" are both \
true; that is "new".
- Personal data is never merged away or dropped.
- When unsure, answer "new": two facts are recoverable, a lost one is not.

Answer ONLY with JSON: {{"action": "new|same|update|supersede", \
"target": "<id or empty>", "text": "<only for update>", "why": "<a few words>"}}

New fact ({kind}): {new}

Stored facts:
{stored}"""
