
import re
import threading
from concurrent.futures import ThreadPoolExecutor

try:
    from hoplita_i18n import t as _t
except Exception:
    def _t(key, **kwargs):
        return key


PHASE_ANSWER = "answer"
PHASE_CRITIQUE = "critique"
PHASE_REFORM = "reform"
PHASE_VOTE = "vote"

_VOTE_WORD = re.compile(r"pl[aá]c[eé]t", re.I)
_VOTE_LINE = re.compile(r"^[\s>*_#-]*(?:voto|vote)\s*[*_]*\s*[:：]\s*(.+?)"
                        r"[\s*_.]*$", re.I)
_NEGATION = re.compile(r"\b(?:no|non|not|niego|rechazo|rechaza|deny|denied|"
                       r"refuse|refused|reject|rejected)\b", re.I)
_NEG_WINDOW = 20


def read_vote(text):
    if not text:
        return "", ""
    lines = str(text).splitlines()
    for tier in (1, 2):
        for i in range(len(lines) - 1, -1, -1):
            ln = lines[i]
            if tier == 1:
                m = _VOTE_LINE.match(ln)
                if not m:
                    continue
                tail = m.group(1)
                if not _VOTE_WORD.search(tail):
                    continue
                verdict = "no" if _NEGATION.search(tail) else "placet"
            else:
                w = _VOTE_WORD.search(ln)
                if not w:
                    continue
                before = ln[max(0, w.start() - _NEG_WINDOW):w.start()]
                verdict = "no" if _NEGATION.search(before) else "placet"
            rest = "\n".join(lines[:i] + lines[i + 1:]).strip()
            return verdict, (rest or ln.strip())
    return "", str(text).strip()


class Council:

    def __init__(self, speak_fn, progress_fn=None, gate_fn=None, cancel_fn=None,
                 max_parallel=4, max_rounds=4):
        self.speak = speak_fn
        self.progress = progress_fn or (lambda ev: None)
        self.gate = gate_fn
        self.cancel = cancel_fn or (lambda: False)
        self.max_parallel = max(1, int(max_parallel or 1))
        self.max_rounds = max(1, int(max_rounds or 1))
        self._lock = threading.Lock()

    def _emit(self, **ev):
        try:
            self.progress(ev)
        except Exception:
            pass

    def _one(self, member, phase, prompt, rnd):
        self._emit(type="speak_start", phase=phase, member=member, round=rnd)
        try:
            res = self.speak(member, phase, prompt, rnd)
        except Exception as e:
            text = _t("council.member_failed", member=member, e=str(e)[:160])
            self._emit(type="speak_failed", phase=phase, member=member,
                       round=rnd, error=str(e)[:200], text=text)
            return text, False
        text, produced = self._spoken(res)
        if not produced:
            try:
                text2, ok2 = self._spoken(self.speak(
                    member, phase,
                    prompt + "\n\n" + _t("prompts.council_retry"), rnd))
                if ok2:
                    text, produced = text2, True
            except Exception:
                pass
        self._emit(type="speak_done", phase=phase, member=member, round=rnd,
                   text=text, produced=produced)
        return text, produced

    @staticmethod
    def _spoken(res):
        if isinstance(res, dict):
            text = str(res.get("text") or "")
            return text, bool(res.get("ok", True)) and bool(text.strip())
        text = str(res or "")
        return text, bool(text.strip())

    def _fanout(self, members, phase, prompt_of, rnd):
        if not members:
            return []
        if len(members) == 1 or self.max_parallel == 1:
            return [(m, self._one(m, phase, prompt_of(m), rnd)[0])
                    for m in members if not self.cancel()]
        out = {}
        with ThreadPoolExecutor(max_workers=min(self.max_parallel,
                                                len(members))) as pool:
            futs = {pool.submit(self._one, m, phase, prompt_of(m), rnd): m
                    for m in members if not self.cancel()}
            for f, m in futs.items():
                try:
                    out[m] = f.result()[0]
                except Exception as e:
                    out[m] = _t("council.member_failed", member=m,
                                e=str(e)[:160])
        return [(m, out[m]) for m in members if m in out]

    def deliberate(self, topic, members, first, context="", where=None):
        members = [m for m in (members or []) if m]
        if not members:
            return {"error": _t("council.err_no_members")}
        first = first if first in members else members[0]
        others = [m for m in members if m != first]
        topic = (topic or "").strip()
        if not topic:
            return {"error": _t("council.err_no_topic")}

        ctx_block = ""
        if (context or "").strip():
            ctx_block = "\n\n" + _t("prompts.council_context",
                                    context=str(context).strip())

        self._emit(type="convene", members=list(members), first=first,
                   others=list(others), topic=topic,
                   where=dict(where or {}))

        answer, got = self._one(first, PHASE_ANSWER,
                                _t("prompts.council_answer_user", topic=topic)
                                + ctx_block, 1)
        if self.cancel():
            return self._cancelled(first, answer, members)
        if not got:
            self._emit(type="note", key="council.first_silent",
                       args={"member": first})
            self._emit(type="done", placet=False, rounds=0)
            return {"error": _t("council.err_first_silent", member=first,
                                detail=" ".join(str(answer).split())[:300])}

        if not others:
            self._emit(type="note", key="council.alone", args={"member": first})
            self._emit(type="done", placet=True, rounds=1)
            return {"answer": answer, "placet": True, "dissent": [],
                    "rounds": [{"n": 1, "answer": answer, "critiques": [],
                                "reform": "", "votes": []}],
                    "stopped": False, "cancelled": False, "members": members,
                    "first": first}

        rounds, rnd, stopped = [], 1, False
        while True:
            crit_prompt = _t("prompts.council_critique_user", topic=topic,
                             answer=answer) + ctx_block
            critiques = self._fanout(others, PHASE_CRITIQUE,
                                     lambda _m: crit_prompt, rnd)
            if self.cancel():
                return self._cancelled(first, answer, members, rounds)

            fb = "\n\n".join(
                _t("prompts.council_feedback_item", member=m, text=txt)
                for m, txt in critiques)
            prev = answer
            reform, got = self._one(
                first, PHASE_REFORM,
                _t("prompts.council_reform_user", topic=topic, answer=answer,
                   feedback=fb) + ctx_block, rnd)
            if self.cancel():
                return self._cancelled(first, (reform if got else answer),
                                       members, rounds)
            if got:
                answer = reform.strip()
            else:
                self._emit(type="note", key="council.reform_silent",
                           args={"member": first})
                answer = prev

            vote_prompt = _t("prompts.council_vote_user", topic=topic,
                             answer=answer) + ctx_block
            spoken = self._fanout(others, PHASE_VOTE,
                                  lambda _m: vote_prompt, rnd)
            votes = []
            for m, txt in spoken:
                verdict, why = read_vote(txt)
                votes.append({"member": m, "verdict": verdict, "why": why,
                              "text": txt})
                self._emit(type="vote", member=m, round=rnd, verdict=verdict,
                           why=why[:400])

            dissent = [v for v in votes if v["verdict"] != "placet"]
            placet = bool(votes) and not dissent
            rounds.append({"n": rnd, "answer": answer,
                           "critiques": [{"member": m, "text": txt}
                                         for m, txt in critiques],
                           "reform": reform, "votes": votes})
            self._emit(type="round_done", round=rnd, placet=placet,
                       dissent=[{"member": v["member"],
                                 "verdict": v["verdict"],
                                 "why": v["why"][:400]} for v in dissent])
            if self.cancel():
                return self._cancelled(first, answer, members, rounds)
            if placet:
                break

            if rnd >= self.max_rounds:
                self._emit(type="note", key="council.rounds_ceiling",
                           args={"n": rnd})
                stopped = True
                break
            decision = "stop"
            if self.gate is not None:
                try:
                    decision = self.gate({"round": rnd, "answer": answer,
                                          "dissent": dissent,
                                          "members": members, "first": first,
                                          "topic": topic}) or "stop"
                except Exception:
                    decision = "stop"
            if decision != "again":
                stopped = True
                break
            rnd += 1
            self._emit(type="note", key="council.another_round",
                       args={"n": rnd})

        self._emit(type="done", placet=placet, rounds=len(rounds))
        return {"answer": answer, "placet": placet,
                "dissent": [{"member": v["member"], "verdict": v["verdict"],
                             "why": v["why"]} for v in dissent],
                "rounds": rounds, "stopped": stopped, "cancelled": False,
                "members": members, "first": first}

    def _cancelled(self, first, answer, members, rounds=None):
        self._emit(type="cancelled")
        return {"answer": answer or "", "placet": False, "dissent": [],
                "rounds": rounds or [], "stopped": True, "cancelled": True,
                "members": members, "first": first}


_RECORD_CHARS = 1400

RECORD_MARKS = ("[Deliberación del consejo", "[Deliberation of the council")


def strip_record(text):
    s = str(text or "")
    cut = min([i for i in (s.find(m) for m in RECORD_MARKS) if i >= 0],
              default=-1)
    return (s[:cut] if cut >= 0 else s).rstrip()


def render_record(res):
    if not isinstance(res, dict) or not res.get("rounds"):
        return ""

    def cut(txt):
        one = " ".join(str(txt or "").split())
        return (one[:_RECORD_CHARS] + _t("council.record_cut")
                if len(one) > _RECORD_CHARS else one)

    lines, rounds = [], res["rounds"]
    for r in rounds:
        if len(rounds) > 1:
            lines.append(_t("council.record_round", n=r.get("n", "?")))
        for c in r.get("critiques") or []:
            if str(c.get("text") or "").strip():
                lines.append(_t("council.record_critique",
                                member=c.get("member", "?"),
                                text=cut(c.get("text"))))
        for v in r.get("votes") or []:
            key = ("council.record_vote_placet" if v.get("verdict") == "placet"
                   else "council.record_vote_no" if v.get("verdict") == "no"
                   else "council.record_vote_unread")
            lines.append(_t(key, member=v.get("member", "?"),
                            why=cut(v.get("why")) or "—"))
    if not lines:
        return ""
    return _t("council.record", who=", ".join(res.get("members") or []),
              first=res.get("first", "?"), body="\n".join(lines))


def format_result(res):
    if not isinstance(res, dict):
        return str(res or "")
    if res.get("error"):
        return str(res["error"])
    out = [str(res.get("answer") or "").strip()]
    dissent = res.get("dissent") or []
    if dissent:
        out.append("\n" + _t("council.dissent_header", n=len(dissent)))
        for d in dissent:
            why = " ".join(str(d.get("why") or "").split())[:600]
            out.append(_t("council.dissent_item", member=d.get("member", "?"),
                          why=why or _t("council.dissent_no_reason")))
    elif res.get("rounds"):
        voters = [m for m in (res.get("members") or [])
                  if m != res.get("first")]
        if voters:
            out.append("\n" + _t("council.placet_footer",
                                 who=", ".join(voters), r=len(res["rounds"])))
    return "\n".join(x for x in out if x).strip()
