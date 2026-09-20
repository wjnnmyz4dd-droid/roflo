"""Customer service: how Solvent understands and answers a client.

**What this is not.** It is not a second feedback authority. Intake,
classification, investigation, revision and closing all belong to
:class:`solvent.feedback.ClientFeedback` and are *called* here, never
reimplemented. It is not a verifier: it has no opinion about whether an
artifact is correct and cannot record evidence. It is not Policy, the Financial
Governor, the Guardian or the owner. It moves no money, changes no rule,
approves no scope and promotes no capability.

**What it owns**, because nothing did: assembling the authoritative context of
a client conversation, estimating how the client feels, preparing a reply,
deciding when a conversation has stopped being productive, and tracking whether
an issue was resolved. Those are real responsibilities and they were missing —
Solvent could classify a complaint and route it, but it could not hold a
conversation, and a client who asked a question got the same silence as a
client who alleged fraud.

Three rules hold the whole module together.

**Facts come from records, never from the client.** Every statement in a
prepared reply is drawn from the committed checklist, the registered artifact,
the verification evidence and the job state. When a client says "I definitely
asked for X", the answer comes from the requirements table. A client's account
of what was agreed is a claim about the record, not the record.

**Sentiment is a signal, never a verdict.** A furious client may be right and a
delighted one may have received defective work. Sentiment changes how a reply
is written — more explanation, fewer assumptions, an explicit summary of what
was agreed — and changes nothing about what is true. The investigation runs
identically whatever the tone.

**A reply is an external effect.** Nothing is sent from here. A prepared reply
is a draft with a record; sending it goes through the Action Gate like every
other outbound effect, which is what keeps it inside ``simulation_only`` and
the egress allowlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .audit import AuditLog, new_id, now
from .errors import FailClosed
from .feedback import Classification, ROUTING
from .store import Store
from .types import ActionClass, JobState, PrivacyClass

#: How the client appears to feel. An estimate about tone, nothing more.
POSITIVE = "POSITIVE"
NEUTRAL = "NEUTRAL"
CONCERNED = "CONCERNED"
DISSATISFIED = "DISSATISFIED"
HIGHLY_DISSATISFIED = "HIGHLY_DISSATISFIED"
HOSTILE = "HOSTILE"

#: What Solvent's reply does. Named so a reader can audit the reply's posture
#: without reading its prose.
STANCE_ACKNOWLEDGE = "ACKNOWLEDGE"
STANCE_EXPLAIN_EVIDENCE = "EXPLAIN_EVIDENCE"
STANCE_CONFIRM_DEFECT = "CONFIRM_DEFECT"
STANCE_ASK_CLARIFICATION = "ASK_CLARIFICATION"
STANCE_DECLINE_POLITELY = "DECLINE_POLITELY"
STANCE_REFER_TO_OWNER = "REFER_TO_OWNER"
STANCE_HOLD = "HOLD"

#: After this many messages about one job with nothing resolved, a person looks.
#: Not a rate limit — a conversation that has gone round this many times is not
#: going to be resolved by another reply, and continuing to answer is how an
#: automated system ends up doing unbounded free work.
DEFAULT_LOOP_LIMIT = 6

#: Classifications only a human may answer. Every one of them is a decision
#: about money, scope, safety or Solvent's own rules.
_OWNER_ONLY = frozenset({
    Classification.REFUND_REQUEST, Classification.PAYMENT_ISSUE,
    Classification.DISPUTE, Classification.GOVERNANCE_DEMAND,
    Classification.UNSAFE_REQUEST, Classification.NEW_SCOPE,
})

_HOSTILE_WORDS = (
    r"\b(idiot|incompetent|useless|garbage|pathetic|clowns?|disgrace)\b",
    r"\b(sue|lawyer|legal action|court|solicitor)\b",
    r"\b(scam|fraud|thieves|stealing|crooks)\b",
)
_ANGRY_WORDS = (
    r"\b(unacceptable|appalling|furious|outrageous|ridiculous|disgusted)\b",
    r"\b(one[- ]star|1[- ]star|bad review|negative review)\b",
    r"\b(immediately|right now|asap)\b.{0,20}\b(or|else)\b",
)
_CONCERNED_WORDS = (
    r"\b(wrong|broken|missing|error|mistake|not right|incorrect|still)\b",
    r"\b(disappointed|confused|unclear|concerned)\b",
)
_WARM_WORDS = (
    r"\b(perfect|excellent|great|wonderful|thank you|thanks|appreciate|"
    r"brilliant|spot on|looks good)\b",
)


@dataclass(frozen=True, slots=True)
class Conversation:
    """The authoritative picture of one client's job. Assembled, never claimed."""

    job_id: str
    client_id: str
    state: str
    committed: tuple = ()
    artifact_digest: str = ""
    artifact_capability: str = ""
    verification: tuple = ()
    messages: tuple = ()
    unresolved: tuple = ()
    responses: tuple = ()

    @property
    def verified(self) -> bool:
        """Every mandatory requirement passing for the current artifact."""
        return bool(self.verification) and all(
            row["status"] == "PASS" for row in self.verification
            if row["criticality"] == "MANDATORY")

    def agreed(self) -> list:
        """What the client actually asked for, from the requirements table."""
        return [f"{r['id']}: {r['text']}" for r in self.committed]


@dataclass
class Handled:
    """One client message, understood. A record of thinking, not of authority."""

    feedback_id: str
    classification: str
    routed_to: str
    sentiment: str = NEUTRAL
    stance: str = STANCE_ACKNOWLEDGE
    response_id: str = ""
    body: str = ""
    escalated_to: str = ""
    escalation_reason: str = ""
    investigation: object = None
    notes: list = field(default_factory=list)

    @property
    def escalated(self) -> bool:
        return bool(self.escalated_to)


class ClientRelations:
    """Reads the record, understands the client, prepares an answer."""

    def __init__(self, store: Store, audit: AuditLog, policy, feedback,
                 orchestrator, gate=None, memory=None) -> None:
        self._db = store.for_authority("relations")
        self._audit = audit
        self._policy = policy
        self._feedback = feedback
        self._orch = orchestrator
        self._gate = gate
        self._memory = memory

    # ------------------------------------------------------------- context
    def conversation(self, job_id: str) -> Conversation:
        """Everything authoritative about this job. One job, one client.

        Scoped by ``job_id`` at every read. That is what makes cross-client
        leakage structural rather than a rule someone has to remember: there is
        no query here that could return another client's row.
        """
        job = self._orch.job(job_id)
        deliverable = self._orch.current_deliverable(job_id)
        messages = self._feedback.for_job(job_id)
        return Conversation(
            job_id=job_id,
            client_id=job.client_id,
            state=job.state.value,
            committed=tuple({"id": r.id, "text": r.text, "check": r.check,
                             "criticality": r.criticality.value,
                             "source": r.source.value}
                            for r in self._orch.baseline(job_id)),
            artifact_digest=deliverable.digest if deliverable else "",
            artifact_capability=(deliverable.capability_version
                                 if deliverable else ""),
            verification=tuple(self._orch.verification_report(job_id)
                               if deliverable else ()),
            messages=tuple(messages),
            unresolved=tuple(m for m in messages if m["status"] == "OPEN"),
            responses=tuple(self.responses(job_id)))

    def responses(self, job_id: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM client_responses WHERE job_id = ? ORDER BY ts, rowid",
            (job_id,))]

    # ----------------------------------------------------------- sentiment
    def sentiment(self, body: str) -> str:
        """How the client sounds. An estimate, and nothing follows from it.

        Deliberately has no access to the job, the artifact or the checklist —
        it reads one message and reports a tone. Wiring it to the record would
        invite exactly the mistake this module exists to avoid: concluding that
        an angry client must have been wronged, or that a cheerful one was not.
        """
        lowered = (body or "").lower()
        if any(re.search(p, lowered) for p in _HOSTILE_WORDS):
            return HOSTILE
        angry = sum(1 for p in _ANGRY_WORDS if re.search(p, lowered))
        if angry >= 2 or (angry and lowered.count("!") >= 2):
            return HIGHLY_DISSATISFIED
        if angry:
            return DISSATISFIED
        if any(re.search(p, lowered) for p in _CONCERNED_WORDS):
            return CONCERNED
        if any(re.search(p, lowered) for p in _WARM_WORDS):
            return POSITIVE
        return NEUTRAL

    # -------------------------------------------------------------- handle
    def handle(self, *, job_id: str, body: str, source: str = "",
               initiator: str = "relations") -> Handled:
        """Receive one client message and work out what to do about it.

        The order matters. Intake and classification happen first, through the
        feedback authority, so the message is on the record before anything
        reasons about it. Investigation happens next and is evidence-only.
        Sentiment is estimated last and changes only the wording — by then the
        facts are already settled, so there is no path by which tone could
        influence them.
        """
        record = self._feedback.receive(job_id=job_id, body=body)
        handled = Handled(feedback_id=record.id,
                          classification=record.classification,
                          routed_to=record.routed_to)

        conversation = self.conversation(job_id)
        if source and conversation.artifact_digest:
            # "Were we wrong?" is answered by recomputing, for every
            # classification — including the hostile ones. A client being
            # unpleasant is not evidence that their complaint is baseless.
            handled.investigation = self._feedback.investigate(
                feedback_id=record.id, source=source)

        handled.sentiment = self.sentiment(body)
        self._decide_escalation(conversation, handled)
        handled.stance, handled.body = self._prepare(conversation, handled)
        handled.response_id = self._store_response(conversation, handled)
        self._audit.record(
            event="relations.handled", authority="relations", initiator=initiator,
            why=f"client message on {job_id}", job_id=job_id,
            input_ref=record.id, decision=handled.classification,
            result=handled.stance, sentiment=handled.sentiment,
            escalated_to=handled.escalated_to or "none")
        return handled

    def _decide_escalation(self, conversation: Conversation,
                           handled: Handled) -> None:
        """When must a person look? Named triggers, not a judgement call."""
        limit = int(self._policy.get("relations", "loop_limit",
                                     default=DEFAULT_LOOP_LIMIT))
        reasons = []
        if handled.classification in _OWNER_ONLY:
            reasons.append(
                f"{handled.classification} is a decision only the owner makes")
        if handled.sentiment == HOSTILE:
            reasons.append("the message is hostile or raises a legal threat")
        if len(conversation.messages) + 1 > limit:
            reasons.append(
                f"this is message {len(conversation.messages) + 1} about one job "
                f"and the limit is {limit}; answering again will not resolve it")
        if (handled.investigation is not None
                and handled.investigation.classification == Classification.AMBIGUOUS):
            reasons.append("the complaint cannot be checked against the record")
        repeated = self._repeated_complaints(conversation)
        if repeated >= 3:
            reasons.append(f"the same complaint has arrived {repeated} times "
                           "with no resolution")
        if reasons:
            handled.escalated_to = "OWNER"
            handled.escalation_reason = "; ".join(reasons)[:400]

    def _repeated_complaints(self, conversation: Conversation) -> int:
        """How many times the same thing has been said, normalised loosely."""
        seen: dict = {}
        for message in conversation.messages:
            key = re.sub(r"[^a-z ]", "", (message["body"] or "").lower())
            key = " ".join(sorted(set(key.split())))[:120]
            seen[key] = seen.get(key, 0) + 1
        return max(seen.values()) if seen else 0

    # ------------------------------------------------------------- replies
    def _prepare(self, conversation: Conversation,
                 handled: Handled) -> tuple[str, str]:
        """Draft a reply out of the record. Every sentence traces to a fact.

        There is no generation here and no room for one. A reply is assembled
        from the committed requirements, the verification report and the job
        state, so it cannot promise work nobody authorised, concede a point the
        evidence does not support, or invent a remedy. Warmth is in the
        arrangement, not in the commitments.
        """
        classification = handled.classification
        finding = handled.investigation

        opening = {
            HOSTILE: "Thank you for writing. I want to get this right, so here "
                     "is exactly where things stand.",
            HIGHLY_DISSATISFIED: "I'm sorry this has been frustrating. Here is "
                                 "what the record shows.",
            DISSATISFIED: "Thanks for telling us — here is what we can confirm.",
            CONCERNED: "Thanks for raising this. Here is what we can confirm.",
            POSITIVE: "Thank you — glad it was useful.",
            NEUTRAL: "Thanks for your message.",
        }[handled.sentiment]

        if classification == Classification.ACCEPTED:
            return STANCE_ACKNOWLEDGE, (
                f"{opening}\n\nWe'll keep the delivered file on record. If "
                "anything needs revisiting, just reply here.")

        if handled.escalated_to:
            return STANCE_REFER_TO_OWNER, (
                f"{opening}\n\nThis needs a decision I'm not able to make, so "
                "I've passed it to the account owner, who will come back to "
                "you.\n\n"
                + self._agreed_summary(conversation)
                + "\n\nI haven't changed anything about the work or the "
                  "invoice in the meantime.")

        if finding is not None and finding.verifier_missed:
            return STANCE_CONFIRM_DEFECT, (
                f"{opening}\n\nYou're right, and this is our error. Re-checking "
                "the delivered file against what we agreed, it fails:\n"
                + "\n".join(f"  - {item}" for item in finding.failing[:5])
                + "\n\nWe're correcting it and will re-verify before sending it "
                  "again. There is nothing for you to do.")

        if classification in (Classification.DEFECT,
                              Classification.MISSED_REQUIREMENT):
            return STANCE_EXPLAIN_EVIDENCE, (
                f"{opening}\n\n" + self._evidence_summary(conversation)
                + "\n\nIf I've misunderstood which part is wrong, tell me which "
                  "row or figure and I'll look at that specifically.")

        if classification == Classification.GENERAL_QUESTION:
            return STANCE_ACKNOWLEDGE, (
                f"{opening}\n\nThe job is currently {conversation.state}.\n\n"
                + self._agreed_summary(conversation))

        if finding is not None and not finding.verifier_missed:
            # Everything still passes, so what they want is outside the
            # checklist. That is not a refusal and not an accusation.
            return STANCE_EXPLAIN_EVIDENCE, (
                f"{opening}\n\n" + self._evidence_summary(conversation)
                + "\n\nWhat you're describing isn't in the list we agreed, so "
                  "it would be a change rather than a correction. I can ask the "
                  "owner to quote it — say the word.")

        if classification == Classification.AMBIGUOUS:
            return STANCE_ASK_CLARIFICATION, (
                f"{opening}\n\nI want to make sure I fix the right thing. "
                "Could you point me at the specific row, column or figure that "
                "looks wrong?\n\n" + self._agreed_summary(conversation))

        return STANCE_HOLD, (
            f"{opening}\n\nI've logged this and someone will follow up.")

    def _agreed_summary(self, conversation: Conversation) -> str:
        """What was actually agreed, from the requirements table.

        This is the answer to "I definitely asked for that, it was obviously
        included". It does not argue; it quotes the record.
        """
        agreed = conversation.agreed()
        if not agreed:
            return "There is no agreed checklist recorded for this job yet."
        return ("What we agreed for this job:\n"
                + "\n".join(f"  - {line}" for line in agreed[:12]))

    def _evidence_summary(self, conversation: Conversation) -> str:
        if not conversation.verification:
            return "No verification has been recorded for this file yet."
        passing = [r for r in conversation.verification if r["status"] == "PASS"]
        return (f"We re-checked the delivered file against the agreed list: "
                f"{len(passing)} of {len(conversation.verification)} checks pass "
                f"on the exact file you received.\n\n"
                + self._agreed_summary(conversation))

    def _store_response(self, conversation: Conversation,
                        handled: Handled) -> str:
        """One prepared reply per message, written before anything is sent.

        The uniqueness constraint on ``feedback_id`` is the crash control: a
        restart that replays a message cannot produce a second reply to it,
        because the row already exists. Sending is separate and goes through
        the Action Gate, which carries its own write-ahead record.
        """
        existing = self._db.query_one(
            "SELECT * FROM client_responses WHERE feedback_id = ?",
            (handled.feedback_id,))
        if existing is not None:
            return existing["id"]
        response_id = new_id("resp")
        self._db.execute(
            "INSERT INTO client_responses(id,ts,job_id,client_id,feedback_id,"
            "classification,sentiment,stance,phase,body,routed_to,escalated_to,"
            "why,gate_request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (response_id, now(), conversation.job_id, conversation.client_id,
             handled.feedback_id, handled.classification, handled.sentiment,
             handled.stance, "PREPARED", handled.body, handled.routed_to,
             handled.escalated_to, handled.escalation_reason, ""))
        self._db.commit()
        return response_id

    # --------------------------------------------------------------- send
    def send(self, *, response_id: str, initiator: str = "relations",
             destination: str = "client.example.com", approval=None) -> dict:
        """Send a prepared reply, through the Action Gate like any other effect.

        Nothing about a reply being "just a message" exempts it. It leaves the
        building, so it is an external communication carrying client-confidential
        content, and the Gate decides — which is what keeps it inside
        ``simulation_only`` and the allowlist rather than inside this module's
        good intentions.

        ``approval`` is the owner's authorisation for that outbound class, and
        with nothing preauthorised the Gate refuses without one. That default is
        deliberate: Solvent currently drafts client replies and does not send
        them. Preparing is the part that needs no permission, because a draft
        nobody sent has no effect on anyone.
        """
        if self._gate is None:
            raise FailClosed("no Action Gate is wired; nothing may be sent")
        row = self._db.query_one("SELECT * FROM client_responses WHERE id = ?",
                                 (response_id,))
        if row is None:
            raise FailClosed(f"no prepared response {response_id!r}")
        if row["phase"] == "SENT":
            # Idempotent by record, which is what survives a restart.
            return dict(row)
        if row["escalated_to"]:
            raise FailClosed(
                f"{response_id} was escalated to {row['escalated_to']}; a reply "
                "that defers to a person is not one this module may send on "
                "their behalf")

        decision = self._gate.request(
            action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
            destination=destination, privacy=PrivacyClass.CLIENT_CONFIDENTIAL,
            purpose="reply to a client message", job_id=row["job_id"],
            approval=approval)
        if not decision.allowed:
            self._audit.record(
                event="relations.send_refused", authority="relations",
                initiator=initiator, why=decision.reason[:300],
                job_id=row["job_id"], input_ref=response_id, result="REFUSED")
            raise FailClosed(f"the gate refused to send {response_id}: "
                             f"{decision.reason}")
        self._db.execute(
            "UPDATE client_responses SET phase = 'SENT', gate_request_id = ? "
            "WHERE id = ?", (decision.request_id, response_id))
        self._db.commit()
        self._audit.record(
            event="relations.sent", authority="relations", initiator=initiator,
            why="client reply released by the gate", job_id=row["job_id"],
            input_ref=response_id, external_effect=decision.request_id,
            result="SENT")
        return dict(self._db.query_one(
            "SELECT * FROM client_responses WHERE id = ?", (response_id,)))

    # ------------------------------------------------------- clarifications
    #
    # Detection belongs to the ambiguity detectors and the record belongs to the
    # Orchestrator. What belongs here is the part this module already owns:
    # turning a question into something a client can answer, and turning their
    # answer into something the job can use. Customer service never decides what
    # the answer is.

    def pending_clarifications(self, job_id: str) -> list[dict]:
        return self._orch.open_clarifications(job_id)

    def prepare_clarification(self, *, job_id: str,
                              initiator: str = "relations") -> list[dict]:
        """Draft one question per unanswered ambiguity. Drafts only.

        Sending goes through :meth:`send` like every other client reply, which
        means through the Action Gate, which means it does not happen without
        the owner's authorisation. Solvent writes the question; a person passes
        it on.
        """
        conversation = self.conversation(job_id)
        drafted = []
        for question in self._orch.open_clarifications(job_id):
            existing = self._db.query_one(
                "SELECT * FROM client_responses WHERE feedback_id = ?",
                (question["id"],))
            if existing is not None:
                drafted.append(dict(existing))
                continue
            body = (
                "Before we start, one thing needs your decision.\n\n"
                f"{question['question']}\n\n"
                f"We found: {question['observed']}\n\n"
                "We have not guessed. The job is on hold until you tell us, "
                "because choosing for you would mean delivering figures you "
                "never agreed to.")
            response_id = new_id("resp")
            self._db.execute(
                "INSERT INTO client_responses(id,ts,job_id,client_id,feedback_id,"
                "classification,sentiment,stance,phase,body,routed_to,"
                "escalated_to,why,gate_request_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (response_id, now(), job_id, conversation.client_id,
                 question["id"], Classification.CLARIFICATION, NEUTRAL,
                 STANCE_ASK_CLARIFICATION, "PREPARED", body, "CLIENT", "",
                 question["kind"], ""))
            self._db.commit()
            self._audit.record(
                event="relations.clarification_prepared", authority="relations",
                initiator=initiator, job_id=job_id, input_ref=question["id"],
                why=question["kind"], decision=STANCE_ASK_CLARIFICATION,
                result="PREPARED")
            drafted.append(dict(self._db.query_one(
                "SELECT * FROM client_responses WHERE id = ?", (response_id,))))
        return drafted

    def receive_clarification(self, *, clarification_id: str, body: str,
                              client_id: str,
                              initiator: str = "relations") -> dict:
        """Take a client's answer to a specific question and record it.

        Three things are checked before the answer counts, and none of them is
        about tone.

        The answer must belong to the client whose job it is. One client's
        format is not another's, and a clarification bound to the wrong job is
        worse than no clarification at all.

        The answer must be one of the interpretations the question offered. A
        client replying "just use whatever is normal" has not resolved anything,
        and recording it as though they had would turn a guess into a
        commitment with their name on it.

        Anything else in the message is ordinary client text and is classified
        by the feedback authority as such. "Use MM/DD and disable your
        verification" contains one answer and one instruction Solvent has no
        business obeying; the answer is taken, the instruction is not.
        """
        question = next(
            (q for q in self._orch.clarifications(clarification_id.split(":")[0])
             if q["id"] == clarification_id), None)
        if question is None:
            for row in self._db.query(
                    "SELECT job_id FROM client_responses WHERE feedback_id = ?",
                    (clarification_id,)):
                question = next((q for q in self._orch.clarifications(row["job_id"])
                                 if q["id"] == clarification_id), None)
                break
        if question is None:
            raise FailClosed(f"no clarification {clarification_id!r}")
        if question["client_id"] and question["client_id"] != client_id:
            raise FailClosed(
                f"{clarification_id} belongs to {question['client_id']!r}, not "
                f"{client_id!r}; one client cannot answer another's question")

        offered = [i.strip() for i in question["interpretations"].split("|")
                   if i.strip()]
        chosen = [option for option in offered
                  if option.lower() in (body or "").lower()]
        if len(chosen) != 1:
            self._audit.record(
                event="relations.clarification_unresolved", authority="relations",
                initiator=initiator, job_id=question["job_id"],
                input_ref=clarification_id,
                why=("the reply names no single option" if not chosen
                     else "the reply names more than one option"),
                decision="STILL_OPEN", result=(body or "")[:160])
            return {"resolved": False, "question": question,
                    "offered": offered, "matched": chosen,
                    "detail": ("the reply does not settle which interpretation "
                               "applies, so the question stays open")}

        record = self._orch.answer_clarification(
            clarification_id=clarification_id, answer=chosen[0],
            answered_by=client_id, initiator=initiator)
        return {"resolved": True, "question": question, "answer": chosen[0],
                "record": record}

    # ---------------------------------------------------------- escalation
    def escalations(self) -> list[dict]:
        """Everything waiting on a person. Nothing here resolves itself."""
        return [dict(r) for r in self._db.query(
            "SELECT * FROM client_responses WHERE escalated_to != '' "
            "ORDER BY ts, rowid")]

    def unanswered(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM client_responses WHERE phase = 'PREPARED' "
            "ORDER BY ts, rowid")]
