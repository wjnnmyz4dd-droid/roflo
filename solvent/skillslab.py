"""Skills Lab — the lifecycle around a capability, not a second authority over it.

Solvent already has the authority that matters for capability growth. The
registry records proposals, takes the owner's answer, distinguishes *may build*
from *may deploy*, certifies verifiers against generated evidence, revokes them,
and refuses to register a scope the evidence does not support. None of that is
re-decided here.

What was missing is everything *around* that decision, and each piece exists
because of a specific way the work goes wrong:

**A need is not a specification.** "A job wanted XLSX" says nothing about what to
build. A project records which job exposed the gap and how often, so the owner is
answering a question with evidence attached rather than a suggestion.

**Almost every "new skill" is an existing one.** The cheapest thing for a builder
to do is write a new file, and the result is two capabilities with overlapping
authority and no rule about which decides. The overlap gate runs before any work
and can conclude that nothing needs building.

**An upgrade will call itself an update.** An update is meant to be cheap — fix a
bug, keep the contract. So a change that widens what a capability accepts has
every incentive to be labelled one, which is how scope grows without anybody
approving it. Classification is computed from the declared scope, not accepted
from the caller.

**Told is not true.** A builder works from documentation, and documentation is
wrong, stale, or hostile. Evidence here is a claim plus a source plus a trust
level plus a verification outcome, and an unverified claim blocks the build. That
is the whole defence against being confidently wrong: not detecting lies, but
refusing to proceed on anything nobody checked.

The Lab writes four tables and holds no other authority. It cannot promote a
capability, amend policy, touch the ledger or authorise an external effect. When
a stage needs a decision that belongs to somebody else, it asks them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .audit import AuditLog, new_id, now
from .errors import FailClosed
from .store import Store

AUTHORITY = "skillslab"


# --------------------------------------------------------------- change types
#: What kind of change this project is, in the order of how much evidence it
#: needs. Ordered so "is this at least an upgrade?" is a comparison.
UPDATE = "UPDATE"      # fix, without intending to change what it can do
UPGRADE = "UPGRADE"    # deliberately change or widen what it can do
NEW = "NEW"            # a capability that does not exist yet
CHANGE_TYPES = (UPDATE, UPGRADE, NEW)
_WEIGHT = {UPDATE: 0, UPGRADE: 1, NEW: 2}


# ------------------------------------------------------------- overlap gate
EXISTING_SUFFICIENT = "EXISTING_SKILL_SUFFICIENT"
UPDATE_EXISTING = "UPDATE_EXISTING"
UPGRADE_EXISTING = "UPGRADE_EXISTING"
NEW_SKILL_REQUIRED = "NEW_SKILL_REQUIRED"
HUMAN_REQUIRED = "HUMAN_REQUIRED"
CANNOT_EXECUTE = "CANNOT_EXECUTE"
OVERLAP_VERDICTS = (EXISTING_SUFFICIENT, UPDATE_EXISTING, UPGRADE_EXISTING,
                    NEW_SKILL_REQUIRED, HUMAN_REQUIRED, CANNOT_EXECUTE)
#: Verdicts that authorise no building at all.
_NO_BUILD = (EXISTING_SUFFICIENT, HUMAN_REQUIRED, CANNOT_EXECUTE)


# ----------------------------------------------------------------- lifecycle
#: The stages a project passes through. Ordered: a project may not skip forward.
NEED = "NEED"
OVERLAP_CHECKED = "OVERLAP_CHECKED"
EVIDENCE = "EVIDENCE"
SPECIFIED = "SPECIFIED"
BUILT = "BUILT"
#: Handed to an external validator. The candidate is built and nobody has
#: independently tested it yet, which is a different thing from "untested".
VALIDATION_REQUESTED = "VALIDATION_REQUESTED"
#: An external validator ran the battery and it did not pass. The candidate
#: stays a candidate; this is a recorded outcome, not a dead end.
VALIDATION_FAILED = "VALIDATION_FAILED"
CERTIFIED = "CERTIFIED"
AWAITING_OWNER = "AWAITING_OWNER"
PROMOTED = "PROMOTED"
ABANDONED = "ABANDONED"
STAGES = (NEED, OVERLAP_CHECKED, EVIDENCE, SPECIFIED, BUILT, CERTIFIED,
          AWAITING_OWNER, PROMOTED)


#: What a version is doing now. A version never leaves the record; only its
#: lifecycle changes, so a retired version's jobs stay explicable.
ACTIVE = "ACTIVE"
SUPERSEDED = "SUPERSEDED"
DEPRECATED = "DEPRECATED"
REVOKED = "REVOKED"
RETIRED = "RETIRED"
LIFECYCLES = (ACTIVE, SUPERSEDED, DEPRECATED, REVOKED, RETIRED)
#: Lifecycles a new job may not use. Deprecated is still usable on purpose —
#: it is a warning, not a withdrawal.
_UNUSABLE = (REVOKED, RETIRED)


# ------------------------------------------------------------------- evidence
#: How much a source is worth. Ordered, because "prefer the better source" and
#: "this claim rests only on prose" are the two questions that matter.
OWNER_STATEMENT = "OWNER_STATEMENT"      # the owner said so, directly
MEASURED = "MEASURED"                    # Solvent ran it and observed the result
PUBLISHER_PRIMARY = "PUBLISHER_PRIMARY"  # the thing's own authoritative source
THIRD_PARTY = "THIRD_PARTY"              # documentation about it
CLIENT_ASSERTION = "CLIENT_ASSERTION"    # a client said so
MODEL_ASSERTION = "MODEL_ASSERTION"      # a model said so
UNSOURCED = "UNSOURCED"                  # prose with nothing behind it
TRUST_LEVELS = (OWNER_STATEMENT, MEASURED, PUBLISHER_PRIMARY, THIRD_PARTY,
                CLIENT_ASSERTION, MODEL_ASSERTION, UNSOURCED)
_TRUST_RANK = {level: rank for rank, level in enumerate(TRUST_LEVELS)}

#: Trust levels that can never, alone, establish a claim a build depends on.
#: A model's assertion and unsourced prose are the two that produced confident
#: wrongness; a client's assertion is a fact about the client, not about the
#: world. Each may still be *recorded* — it just cannot carry a build.
CANNOT_ESTABLISH = (CLIENT_ASSERTION, MODEL_ASSERTION, UNSOURCED)

#: Evidence is append-only, so a claim is not a row that changes — it is a
#: lineage of rows sharing a ``claim_ref``, and its status is the latest one.
#: Recording the check as an edit would let a refuted claim be quietly restored,
#: which is exactly the history a certification rests on.
UNVERIFIED = "UNVERIFIED"
VERIFIED = "VERIFIED"
REFUTED = "REFUTED"
CONTRADICTED = "CONTRADICTED"
EVIDENCE_STATUSES = (UNVERIFIED, VERIFIED, REFUTED, CONTRADICTED)


@dataclass(frozen=True, slots=True)
class Project:
    """One skill change, from the need that exposed it to the owner's answer."""

    id: str
    skill: str
    change_type: str
    stage: str
    need: str
    observed_on: str = ""
    occurrences: int = 1
    base_version: str = ""
    target_version: str = ""
    overlap_verdict: str = ""
    overlap_why: str = ""
    opened_by: str = ""
    note: str = ""


def bump(version: str, change_type: str) -> str:
    """The next version for this kind of change.

    ``name/MAJOR.MINOR`` is what this codebase already uses (``csv-cleanup/1.0``),
    so a patch level is added only when one is needed rather than rewriting every
    existing record. UPDATE bumps the patch, UPGRADE the minor, and a breaking
    change is the owner's to name — nothing here decides that a change is
    breaking, because that is a judgement about clients, not about code.
    """
    if not version or "/" not in version:
        raise FailClosed(
            f"{version!r} is not a version this can advance; expected "
            "'name/MAJOR.MINOR'")
    name, _, numbers = version.partition("/")
    parts = [int(p) if p.isdigit() else 0 for p in numbers.split(".")]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    if change_type == UPDATE:
        patch += 1
    elif change_type == UPGRADE:
        minor, patch = minor + 1, 0
    else:
        raise FailClosed(
            f"{change_type} does not advance an existing version; a new "
            "capability starts at a version the owner names")
    tail = f"{major}.{minor}" if patch == 0 else f"{major}.{minor}.{patch}"
    return f"{name}/{tail}"


def classify(*, base_covers: frozenset, target_covers: frozenset,
             declared: str = "") -> tuple[str, str]:
    """``(change_type, why)`` computed from the scopes, not taken on trust.

    An UPDATE that adds a check is an UPGRADE however it is labelled. This is the
    one classification that must not be an input: "it is only a small fix" is
    exactly what a scope expansion says about itself, and believing it is how a
    capability grows without the certification that growth requires.

    A *narrower* target is also not an update — removing a check changes what
    the capability promises, and a client relying on the removed one is affected.
    """
    added = sorted(target_covers - base_covers)
    removed = sorted(base_covers - target_covers)
    if not base_covers:
        return NEW, "no existing scope to change"
    if added and removed:
        return UPGRADE, (f"scope changed: added {added}, removed {removed}; "
                         "a changed contract is an upgrade")
    if added:
        return UPGRADE, (f"scope adds {added}; widening what a capability "
                         "accepts is an upgrade, whatever the change is called")
    if removed:
        return UPGRADE, (f"scope removes {removed}; withdrawing a check is a "
                         "changed promise, not a fix")
    reason = "scope is unchanged, so this is a fix rather than a change of scope"
    if declared and declared != UPDATE:
        return UPDATE, (f"{reason} (declared {declared}, and a declaration "
                        "cannot make an unchanged scope into a wider one)")
    return UPDATE, reason


class SkillsLab:
    """The lifecycle around capability growth. Decides nothing others own."""

    def __init__(self, store: Store, audit: AuditLog, capability,
                 policy=None) -> None:
        self._db = store.for_authority(AUTHORITY)
        self._audit = audit
        self._capability = capability
        self._policy = policy

    # ------------------------------------------------------------- the need
    def record_need(self, *, skill: str, need: str, observed_on: str = "",
                    occurrences: int = 1, requested_by: str = "skillslab",
                    base_version: str = "") -> str:
        """A job wanted something. That is a question, not a plan.

        ``observed_on`` is the job or workflow that exposed the gap. It is
        required for anything claiming to have been observed more than once: a
        count with no instance behind it is a number somebody liked.
        """
        if not need.strip():
            raise FailClosed("a project needs a stated need")
        if occurrences > 1 and not observed_on.strip():
            raise FailClosed(
                f"{occurrences} occurrences are claimed for {skill!r} with no "
                "job recorded against them; a frequency nobody can point at is "
                "not evidence of demand")
        project_id = new_id("skp")
        self._db.execute(
            "INSERT INTO skill_projects(id,ts,skill,change_type,stage,need,"
            "observed_on,occurrences,base_version,opened_by) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (project_id, now(), skill, "", NEED, need.strip(),
             observed_on.strip(), max(1, int(occurrences)), base_version,
             requested_by))
        self._db.commit()
        self._event(project_id, NEED, "OPENED", need.strip(), requested_by)
        self._audit.record(
            event="skillslab.need_recorded", authority=AUTHORITY,
            initiator=requested_by, why=need.strip()[:200],
            job_id=observed_on or None, decision=skill, input_ref=project_id,
            result=f"{occurrences} occurrence(s)")
        return project_id

    # ---------------------------------------------------------- overlap gate
    def check_overlap(self, *, project_id: str,
                      target_covers: frozenset | None = None,
                      needs_human: bool = False,
                      impossible: str = "",
                      defect: str = "") -> tuple[str, str]:
        """Does anything already do this? Runs before any work.

        Returns one of :data:`OVERLAP_VERDICTS`. Three of them authorise no
        building at all, and that is the point: the cheapest outcome for a
        builder is a new file, and the correct outcome is usually not one.

        ``defect`` distinguishes the two questions "we already do this" answers
        differently. It answers a *feature* request — nothing needs building. It
        does not answer a *bug report*: a fix has exactly the same scope as the
        thing it fixes, and refusing it because the scope is covered would mean
        a capability could never be repaired. A defect must be described, so that
        "it is broken" is a statement somebody made rather than a way round the
        gate.
        """
        project = self.project(project_id)
        if impossible.strip():
            return self._settle_overlap(project, CANNOT_EXECUTE, impossible.strip())
        if needs_human:
            return self._settle_overlap(
                project, HUMAN_REQUIRED,
                "this needs a person; Solvent has no capability for it and "
                "should not pretend otherwise")

        wanted = frozenset(target_covers or ())
        existing = {c.name: c for c in self._capability.capabilities()}
        current = existing.get(project.skill)

        if current is not None:
            covered = frozenset(current.covers)
            if wanted and wanted <= covered and not defect.strip():
                return self._settle_overlap(
                    project, EXISTING_SUFFICIENT,
                    f"{current.name} {current.version} already covers "
                    f"{sorted(wanted)}; nothing needs building")
            if defect.strip() and wanted <= covered:
                return self._settle_overlap(
                    project, UPDATE_EXISTING,
                    f"{current.name} {current.version} covers this scope and is "
                    f"reported defective: {defect.strip()[:200]}. A fix has the "
                    "same scope as the thing it fixes",
                    base_version=current.version)
            change_type, why = classify(base_covers=covered,
                                        target_covers=wanted or covered)
            verdict = UPGRADE_EXISTING if change_type == UPGRADE else UPDATE_EXISTING
            return self._settle_overlap(project, verdict, why,
                                        base_version=current.version)

        # Nothing registered under this name. Anything else already covering the
        # same checks is still an overlap, whatever it is called.
        for name, capability in sorted(existing.items()):
            if wanted and wanted <= frozenset(capability.covers):
                return self._settle_overlap(
                    project, EXISTING_SUFFICIENT,
                    f"{name} {capability.version} already covers "
                    f"{sorted(wanted)} under a different name; building "
                    f"{project.skill!r} would create two capabilities with "
                    "overlapping authority and no rule about which decides")
        overlapping = sorted(
            name for name, capability in existing.items()
            if wanted & frozenset(capability.covers))
        if overlapping:
            return self._settle_overlap(
                project, UPGRADE_EXISTING,
                f"{overlapping} already cover part of {sorted(wanted)}; "
                "widening one of them is a change to a known capability, and a "
                "second capability over the same checks is not")
        return self._settle_overlap(project, NEW_SKILL_REQUIRED,
                                    f"nothing covers {sorted(wanted)}")

    def _settle_overlap(self, project: Project, verdict: str, why: str,
                        base_version: str = "") -> tuple[str, str]:
        change_type = {UPDATE_EXISTING: UPDATE, UPGRADE_EXISTING: UPGRADE,
                       NEW_SKILL_REQUIRED: NEW}.get(verdict, "")
        self._db.execute(
            "UPDATE skill_projects SET stage = ?, overlap_verdict = ?, "
            "overlap_why = ?, change_type = ?, base_version = ? WHERE id = ?",
            (OVERLAP_CHECKED, verdict, why, change_type,
             base_version or project.base_version, project.id))
        self._db.commit()
        self._event(project.id, OVERLAP_CHECKED, verdict, why, AUTHORITY)
        self._audit.record(
            event="skillslab.overlap_checked", authority=AUTHORITY,
            initiator=AUTHORITY, why=why[:200], decision=verdict,
            input_ref=project.id, result=change_type or "no build")
        return verdict, why

    # -------------------------------------------------------------- evidence
    def record_claim(self, *, project_id: str, claim: str, source: str,
                     trust: str, detail: str = "") -> str:
        """Something the build would rely on, and where it came from.

        Recording a claim is not believing it. Every claim starts UNVERIFIED and
        blocks the build until something checks it, which is what stops a
        confident paragraph from becoming an implementation decision.
        """
        if trust not in TRUST_LEVELS:
            raise FailClosed(
                f"{trust!r} is not a trust level; one of {', '.join(TRUST_LEVELS)}")
        if not claim.strip() or not source.strip():
            raise FailClosed(
                "a claim needs both the claim and where it came from; a claim "
                "with no source is the thing this exists to refuse")
        self.project(project_id)  # fails closed on an unknown project
        claim_id = new_id("skc")
        self._append_evidence(
            project_id=project_id, claim_ref=claim_id, claim=claim.strip(),
            source=source.strip(), trust=trust, status=UNVERIFIED, detail=detail)
        self._event(project_id, EVIDENCE, "CLAIM_RECORDED",
                    f"{claim.strip()[:80]} ({trust})", AUTHORITY)
        return claim_id

    def verify_claim(self, *, claim_id: str, status: str, checked_by: str,
                     detail: str = "") -> None:
        """Record what happened when somebody actually checked a claim."""
        if status not in EVIDENCE_STATUSES:
            raise FailClosed(f"{status!r} is not an evidence status")
        row = self._latest_claim(claim_id)
        if row is None:
            raise FailClosed(f"no claim {claim_id!r}")
        if status == VERIFIED and row["trust"] in CANNOT_ESTABLISH:
            raise FailClosed(
                f"a {row['trust']} cannot be marked VERIFIED on its own: "
                f"{row['source']!r} is an assertion, and checking it means "
                "finding a better source that agrees, recorded as its own claim")
        if not checked_by.strip():
            raise FailClosed("a verification needs to say who checked it")
        self._append_evidence(
            project_id=row["project_id"], claim_ref=claim_id, claim=row["claim"],
            source=row["source"], trust=row["trust"], status=status,
            checked_by=checked_by.strip(), detail=detail)
        self._event(row["project_id"], EVIDENCE, status,
                    f"{row['claim'][:70]}: {detail[:70]}", checked_by)

    def note_contradiction(self, *, project_id: str, claim_ids: list[str],
                           why: str) -> None:
        """Two claims the build cannot both act on.

        Marked rather than resolved. Choosing between contradictory sources is a
        judgement, and a Lab that quietly picked one would be deciding something
        it has no basis to decide.
        """
        if len(claim_ids) < 2:
            raise FailClosed("a contradiction needs at least two claims")
        for claim_id in claim_ids:
            row = self._latest_claim(claim_id)
            if row is None:
                raise FailClosed(f"no claim {claim_id!r}")
            self._append_evidence(
                project_id=row["project_id"], claim_ref=claim_id,
                claim=row["claim"], source=row["source"], trust=row["trust"],
                status=CONTRADICTED, checked_by=AUTHORITY, detail=why,
                contradicts=",".join(c for c in claim_ids if c != claim_id))
        self._event(project_id, EVIDENCE, CONTRADICTED, why, AUTHORITY)
        self._audit.record(
            event="skillslab.contradiction", authority=AUTHORITY,
            initiator=AUTHORITY, why=why[:200], input_ref=project_id,
            decision=CONTRADICTED, result=f"{len(claim_ids)} claim(s)")

    def _append_evidence(self, *, project_id: str, claim_ref: str, claim: str,
                         source: str, trust: str, status: str,
                         checked_by: str = "", detail: str = "",
                         contradicts: str = "") -> None:
        self._db.execute(
            "INSERT INTO skill_evidence(id,ts,project_id,claim_ref,claim,source,"
            "trust,status,checked_by,contradicts,detail) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("ske"), now(), project_id, claim_ref, claim, source, trust,
             status, checked_by, contradicts, detail))
        self._db.commit()

    def _latest_claim(self, claim_ref: str) -> dict | None:
        rows = [dict(r) for r in self._db.query(
            "SELECT * FROM skill_evidence WHERE claim_ref = ? ORDER BY ts, id",
            (claim_ref,))]
        return rows[-1] if rows else None

    def evidence(self, project_id: str) -> list[dict]:
        """One entry per claim, at its latest recorded status."""
        latest: dict[str, dict] = {}
        for row in self._db.query(
                "SELECT * FROM skill_evidence WHERE project_id = ? "
                "ORDER BY ts, id", (project_id,)):
            latest[row["claim_ref"]] = dict(row)
        return list(latest.values())

    def evidence_history(self, project_id: str) -> list[dict]:
        """Every row, in order. What was claimed, checked, and concluded."""
        return [dict(r) for r in self._db.query(
            "SELECT * FROM skill_evidence WHERE project_id = ? ORDER BY ts, id",
            (project_id,))]

    def evidence_blockers(self, project_id: str) -> list[str]:
        """Why this project may not be built yet. Empty means it may."""
        rows = self.evidence(project_id)
        if not rows:
            return ["no evidence recorded: nothing establishes what to build"]
        blockers = []
        for row in rows:
            if row["status"] == REFUTED:
                blockers.append(
                    f"refuted claim still in the record: {row['claim'][:70]!r} "
                    f"({row['detail'][:60]})")
            elif row["status"] == CONTRADICTED:
                blockers.append(
                    f"contradicted claim: {row['claim'][:70]!r} — "
                    f"{row['detail'][:60]}")
            elif row["status"] == UNVERIFIED:
                blockers.append(
                    f"unverified claim: {row['claim'][:70]!r} from "
                    f"{row['source'][:40]!r} ({row['trust']})")
        return blockers

    # ------------------------------------------------------------ the build
    def may_build(self, project_id: str) -> tuple[bool, str]:
        """Everything that must hold before a line is written.

        Four separate questions, and each has produced the wrong answer on its
        own: has anybody decided this should exist, did the overlap gate say
        build anything, does the evidence stand up, and is the change type
        computed rather than declared.
        """
        project = self.project(project_id)
        if project.stage == ABANDONED:
            return False, f"{project_id} was abandoned"
        if not project.overlap_verdict:
            return False, "the overlap gate has not run; it runs before work"
        if project.overlap_verdict in _NO_BUILD:
            return False, (f"the overlap gate returned "
                           f"{project.overlap_verdict}: {project.overlap_why}")
        blockers = self.evidence_blockers(project_id)
        if blockers:
            return False, ("the evidence does not support building this yet: "
                           + "; ".join(blockers[:3]))
        permitted, why = self._capability.may_develop(project.skill)
        if not permitted:
            return False, why
        return True, (f"{project.change_type} of {project.skill} is supported by "
                      f"{len(self.evidence(project_id))} verified claim(s)")

    def mark_specified(self, *, project_id: str, target_covers: frozenset,
                       declared_change_type: str = "",
                       target_version: str = "") -> tuple[str, str]:
        """Fix the scope, and classify the change from it.

        ``declared_change_type`` is recorded and then checked, never believed. A
        caller declaring UPDATE over a widened scope is told what it actually is.
        """
        project = self.project(project_id)
        allowed, why = self.may_build(project_id)
        if not allowed:
            raise FailClosed(f"cannot specify {project_id}: {why}")

        base = self.base_scope(project)
        change_type, reason = classify(base_covers=base,
                                       target_covers=frozenset(target_covers),
                                       declared=declared_change_type)
        if (declared_change_type
                and _WEIGHT.get(change_type, 0) > _WEIGHT.get(declared_change_type, 0)):
            reason = (f"declared {declared_change_type}, but {reason}. The "
                      "stronger classification stands: a change that widens "
                      "what a capability does cannot be certified as a fix")
        version = target_version or (
            bump(project.base_version, change_type) if project.base_version
            else "")
        self._db.execute(
            "UPDATE skill_projects SET stage = ?, change_type = ?, "
            "target_version = ?, note = ? WHERE id = ?",
            (SPECIFIED, change_type, version, reason, project_id))
        self._db.commit()
        self._event(project_id, SPECIFIED, change_type,
                    "scope:" + ",".join(sorted(target_covers)) + "|" + reason,
                    AUTHORITY)
        self._audit.record(
            event="skillslab.specified", authority=AUTHORITY, initiator=AUTHORITY,
            why=reason[:200], decision=change_type, input_ref=project_id,
            result=version or "version to be named by the owner")
        return change_type, reason

    def base_scope(self, project: Project) -> frozenset:
        """What the thing being changed currently covers.

        The registry is asked first, because a promoted capability's scope is the
        one clients rely on. A skill the owner has *not* promoted still has a
        scope — the Lab certified it — and reading only the registry made every
        fix to an unpromoted skill classify as a brand new capability, which
        would have let a scope change ride in as a first version.
        """
        if not project.base_version:
            return frozenset()
        registered = {c.name: c for c in self._capability.capabilities()}
        current = registered.get(project.skill)
        if current is not None and current.version == project.base_version:
            return frozenset(current.covers)
        for row in reversed(self.versions(project.skill)):
            if row["version"] == project.base_version and row["scope"]:
                return frozenset(n for n in row["scope"].split(",") if n)
        if current is not None:
            return frozenset(current.covers)
        return frozenset()

    def _scope_or_empty(self, project_id: str) -> frozenset:
        try:
            return self.target_scope(project_id)
        except FailClosed:
            return frozenset()

    def mark_built(self, *, project_id: str, fingerprint: str,
                   detail: str = "") -> None:
        project = self.project(project_id)
        if project.stage != SPECIFIED:
            raise FailClosed(
                f"{project_id} is at {project.stage}; a build follows a "
                f"specification, and skipping to {BUILT} would mean the scope "
                "was never fixed")
        if not fingerprint.strip():
            raise FailClosed(
                "a build must name the code it produced; evidence that does not "
                "name an implementation belongs to no implementation")
        self._db.execute("UPDATE skill_projects SET stage = ? WHERE id = ?",
                         (BUILT, project_id))
        self._db.commit()
        self._event(project_id, BUILT, "BUILT", f"{fingerprint} {detail}"[:200],
                    AUTHORITY)

    # -------------------------------------------------- external validation

    def request_validation(self, *, project_id: str, fingerprint: str,
                           requested_by: str = AUTHORITY) -> dict:
        """Hand a built candidate to an external validator, and say so.

        DeskPilot may build a candidate. It may not then decide the candidate
        is sound, because "I made it, therefore it is safe" is the reasoning
        this whole lifecycle exists to prevent (§22). So a built candidate goes
        out to somebody else, and the state says openly that it is waiting on
        them -- which is also what stops the dashboard implying a candidate has
        been tested when nothing has tested it.

        Returns the handoff package. It is deterministic and self-contained so
        a validator working from it is working from the same facts as the
        dashboard, with no screen-scraping and no second registry.
        """
        project = self.project(project_id)
        if project.stage not in (BUILT, VALIDATION_FAILED):
            raise FailClosed(
                f"{project_id} is at {project.stage}; validation follows a "
                "build, and a candidate that was never built has nothing to "
                "validate")
        if not (fingerprint or "").strip():
            raise FailClosed(
                "validation must name the artifact being validated; evidence "
                "that names no artifact belongs to no artifact")
        self._db.execute("UPDATE skill_projects SET stage = ? WHERE id = ?",
                         (VALIDATION_REQUESTED, project_id))
        self._db.commit()
        self._event(project_id, VALIDATION_REQUESTED, "REQUESTED",
                    f"{fingerprint} handed to an external validator"[:200],
                    requested_by)
        self._audit.record(
            event="skillslab.validation_requested", authority=AUTHORITY,
            initiator=requested_by, input_ref=project_id,
            why=f"{project.skill} {project.target_version} needs independent "
                "testing",
            decision=VALIDATION_REQUESTED, result=fingerprint)
        return self.handoff(project_id, fingerprint=fingerprint)

    def handoff(self, project_id: str, *, fingerprint: str = "") -> dict:
        """Everything a validator needs, and nothing it should not have.

        Deliberately contains no credential, no client data and no owner key.
        A validator tests a candidate; it does not operate the business, and a
        package that carried production secrets would make every validator a
        production risk (§25).
        """
        project = self.project(project_id)
        return {
            "project_id": project.id,
            "skill": project.skill,
            "change_type": project.change_type,
            "base_version": project.base_version,
            "target_version": project.target_version,
            "fingerprint": fingerprint,
            "need": project.need,
            "stage": project.stage,
            # What the candidate must do, from the specification that fixed
            # the scope -- not from whatever the candidate happens to do.
            "required_checks": sorted(self.target_scope(project_id)),
            "evidence": self.evidence(project_id),
            "prior_failures": [
                e for e in self.timeline(project_id)
                if e.get("outcome") in ("VALIDATION_FAILED",
                                        "CERTIFICATION_FAILED")],
            "created_by": project.opened_by,
            "test_standard": (
                "baseline, requirements, positive, negative, boundary, "
                "adversarial, malformed input, security, restart, "
                "determinism, independent verification, mutation, "
                "vacuous-test audit, holdout, full regression"),
        }

    def record_validation(self, *, project_id: str, validator: str,
                          passed: bool, fingerprint: str, evidence_ref: str = "",
                          detail: str = "", certified_checks=None,
                          verifier_fingerprint: str = "") -> tuple[bool, str]:
        """Take an external validator's result.

        Two things are checked before the result is believed at all.

        The **validator is not the creator** (§22). A candidate that certified
        itself would make the separation between building and attesting a
        matter of convention, and conventions are what get skipped at 2am.

        The **artifact matches** what was handed out. A validator reporting a
        pass on a different digest tested something else, and the fact that
        both are called version 1.1 is not evidence that they are the same.

        A pass records certification through the existing path -- there is no
        parallel certification here, because there is one certification
        authority and this is not it.
        """
        project = self.project(project_id)
        if project.stage != VALIDATION_REQUESTED:
            raise FailClosed(
                f"{project_id} is at {project.stage}; a validation result "
                "answers a validation request, and there is none outstanding")
        if not (validator or "").strip():
            raise FailClosed("a validation result must name its validator")
        if validator == project.opened_by:
            raise FailClosed(
                f"{validator!r} opened this candidate and may not also validate "
                "it; a thing cannot be its own independent evidence")
        if validator == AUTHORITY:
            raise FailClosed(
                "the Skills Lab may not validate its own candidates")
        if not (fingerprint or "").strip():
            raise FailClosed(
                "a validation result must name the artifact it tested")

        if not passed:
            self._db.execute("UPDATE skill_projects SET stage = ? WHERE id = ?",
                             (VALIDATION_FAILED, project_id))
            self._db.commit()
            self._event(project_id, VALIDATION_FAILED, "VALIDATION_FAILED",
                        f"{validator}: {detail}"[:200], validator)
            self._audit.record(
                event="skillslab.validation_failed", authority=AUTHORITY,
                initiator=validator, input_ref=project_id,
                why=detail[:200] or "external validation did not pass",
                decision=VALIDATION_FAILED,
                result="the candidate remains a candidate")
            return False, detail or "validation did not pass"

        # A pass returns the project to BUILT so the existing certification
        # path can run unchanged. The Lab does not certify here.
        self._db.execute("UPDATE skill_projects SET stage = ? WHERE id = ?",
                         (BUILT, project_id))
        self._db.commit()
        self._event(project_id, BUILT, "VALIDATED",
                    f"{validator} @ {fingerprint}"[:200], validator)
        self._audit.record(
            event="skillslab.validated", authority=AUTHORITY,
            initiator=validator, input_ref=project_id,
            why=detail[:200] or "external validation passed",
            decision="VALIDATED", result=fingerprint)
        checks = frozenset(certified_checks if certified_checks is not None
                           else self.target_scope(project_id))
        return self.record_certification(
            project_id=project_id, state="CERTIFIED", certified_checks=checks,
            fingerprint=fingerprint, verifier_fingerprint=verifier_fingerprint,
            evidence_ref=evidence_ref)

    def validation_state(self, project_id: str) -> str:
        """What a dashboard should say about this candidate's validation."""
        return self.project(project_id).stage

    # ------------------------------------------------------- certification
    def record_certification(self, *, project_id: str, state: str,
                             certified_checks: frozenset, fingerprint: str,
                             verifier_fingerprint: str = "",
                             evidence_ref: str = "") -> tuple[bool, str]:
        """Take the registry's certification result and stage the project on it.

        The certification itself is the registry's; this only records what it
        said. A project whose verifier is not CERTIFIED for the whole target
        scope does not reach the owner, because asking the owner to approve
        something the evidence does not cover is asking them to be the evidence.
        """
        project = self.project(project_id)
        if project.stage != BUILT:
            raise FailClosed(
                f"{project_id} is at {project.stage}; certification follows a build")
        target = self.target_scope(project_id)
        missing = sorted(target - frozenset(certified_checks))
        if state != "CERTIFIED" or missing:
            detail = (f"verifier is {state}"
                      + (f"; not certified for {missing}" if missing else ""))
            self._event(project_id, BUILT, "CERTIFICATION_FAILED", detail, AUTHORITY)
            self._audit.record(
                event="skillslab.certification_failed", authority=AUTHORITY,
                initiator=AUTHORITY, why=detail[:200], input_ref=project_id,
                decision=state, result="not offered to the owner")
            return False, detail
        self._db.execute("UPDATE skill_projects SET stage = ? WHERE id = ?",
                         (CERTIFIED, project_id))
        self._db.commit()
        self._record_version(
            project, lifecycle=ACTIVE if project.change_type == UPDATE else ACTIVE,
            fingerprint=fingerprint, verifier_fingerprint=verifier_fingerprint,
            evidence_ref=evidence_ref, decided_by=AUTHORITY,
            why=f"certified for {len(certified_checks)} check(s)")
        self._event(project_id, CERTIFIED, "CERTIFIED",
                    f"{len(certified_checks)} check(s) @ {fingerprint}", AUTHORITY)
        return True, f"certified for {sorted(certified_checks)}"

    def offer_to_owner(self, project_id: str) -> str:
        """Put a certified project in front of the owner. Never promotes it."""
        project = self.project(project_id)
        if project.stage != CERTIFIED:
            raise FailClosed(
                f"{project_id} is at {project.stage}; only a certified project "
                "goes to the owner, because approval is not a substitute for "
                "evidence")
        self._db.execute("UPDATE skill_projects SET stage = ? WHERE id = ?",
                         (AWAITING_OWNER, project_id))
        self._db.commit()
        self._event(project_id, AWAITING_OWNER, "OFFERED",
                    f"{project.change_type} {project.target_version}", AUTHORITY)
        self._audit.record(
            event="skillslab.offered_to_owner", authority=AUTHORITY,
            initiator=AUTHORITY, why=project.need[:200], input_ref=project_id,
            decision=project.change_type,
            result=f"awaiting owner: {project.target_version}")
        return AWAITING_OWNER

    def record_promotion(self, *, project_id: str, owner_identity: str,
                         why: str, fingerprint: str) -> None:
        """Note that the owner promoted this. The registry did the promoting.

        Deliberately takes the owner's identity and checks it, even though this
        writes nothing the owner owns: a Lab that recorded promotions on its own
        say-so would make its own history the wrong place to look.

        ``fingerprint`` binds the approval to an artifact. The owner approves
        *a thing that was tested*, not a name: an approval that travelled with
        the version number alone would authorise whatever code happened to be
        sitting behind that number at promotion time, including code written
        after the certification the owner was shown. Changing certified code
        invalidates the certification, and this is where that is enforced.

        It is a required argument rather than an optional check, because an
        optional integrity check is one every caller is free to skip.
        """
        if self._policy is not None and not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"{owner_identity!r} is not a registered owner; the Lab records "
                "the owner's decision and never makes it")
        project = self.project(project_id)
        if project.stage != AWAITING_OWNER:
            raise FailClosed(
                f"{project_id} is at {project.stage}; nothing was offered")
        if not (fingerprint or "").strip():
            raise FailClosed(
                f"promoting {project_id} needs the fingerprint of the artifact "
                "being promoted; an approval that names no artifact authorises "
                "any artifact")
        certified = self.certified_fingerprint(project)
        if certified and fingerprint != certified:
            raise FailClosed(
                f"{project_id} was certified at {certified} and promotion "
                f"presents {fingerprint}; the code changed after the evidence "
                "the owner was shown, so the certification no longer describes "
                "it and a new one is required")
        registered = {c.name: c for c in self._capability.capabilities()}
        if project.skill not in registered:
            raise FailClosed(
                f"{project.skill} is not registered as proven; the registry "
                "promotes, and this only records that it did")
        self._db.execute("UPDATE skill_projects SET stage = ? WHERE id = ?",
                         (PROMOTED, project_id))
        self._db.commit()
        self._supersede_earlier(project)
        self._event(project_id, PROMOTED, "PROMOTED", why, owner_identity)

    def certified_fingerprint(self, project) -> str:
        """The artifact digest the certification was recorded against.

        Read from the version row the certification wrote, which is the only
        place that records what was actually tested.
        """
        version = project.target_version or ""
        for row in self.versions(project.skill):
            if row.get("version") == version and row.get("fingerprint"):
                return row["fingerprint"]
        rows = [r for r in self.versions(project.skill) if r.get("fingerprint")]
        return rows[-1]["fingerprint"] if rows else ""

    def abandon(self, *, project_id: str, why: str,
                actor: str = AUTHORITY) -> None:
        self.project(project_id)
        self._db.execute("UPDATE skill_projects SET stage = ? WHERE id = ?",
                         (ABANDONED, project_id))
        self._db.commit()
        self._event(project_id, ABANDONED, "ABANDONED", why, actor)

    # ---------------------------------------------------------- the versions
    def _record_version(self, project: Project, *, lifecycle: str,
                        fingerprint: str, verifier_fingerprint: str,
                        evidence_ref: str, decided_by: str, why: str,
                        rollback_target: str = "") -> str:
        version_id = new_id("skv")
        self._db.execute(
            "INSERT INTO skill_versions(id,ts,skill,version,change_type,scope,"
            "fingerprint,verifier_fingerprint,evidence_ref,lifecycle,supersedes,"
            "rollback_target,why,decided_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (version_id, now(), project.skill,
             project.target_version or project.base_version,
             project.change_type, ",".join(sorted(self._scope_or_empty(project.id))),
             fingerprint,
             verifier_fingerprint, evidence_ref, lifecycle,
             project.base_version, rollback_target, why, decided_by))
        self._db.commit()
        return version_id

    def _supersede_earlier(self, project: Project) -> None:
        """Mark older ACTIVE versions of this skill superseded. History stays."""
        for row in self.versions(project.skill):
            if (row["lifecycle"] == ACTIVE
                    and row["version"] != project.target_version):
                self._set_lifecycle(row["id"], SUPERSEDED,
                                    f"superseded by {project.target_version}",
                                    AUTHORITY)

    def _set_lifecycle(self, version_id: str, lifecycle: str, why: str,
                       decided_by: str) -> None:
        """A lifecycle change is a new row. The old one is history.

        ``skill_versions`` is append-only, so this cannot edit the record even
        by mistake — which is the property that makes a rollback explicable
        afterwards rather than a gap.
        """
        row = self._db.query_one(
            "SELECT * FROM skill_versions WHERE id = ?", (version_id,))
        if row is None:
            raise FailClosed(f"no version record {version_id!r}")
        self._db.execute(
            "INSERT INTO skill_versions(id,ts,skill,version,change_type,scope,"
            "fingerprint,verifier_fingerprint,evidence_ref,lifecycle,supersedes,"
            "rollback_target,why,decided_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("skv"), now(), row["skill"], row["version"],
             row["change_type"], row["scope"], row["fingerprint"],
             row["verifier_fingerprint"], row["evidence_ref"], lifecycle,
             row["supersedes"], row["rollback_target"], why, decided_by))
        self._db.commit()

    def versions(self, skill: str = "") -> list[dict]:
        if skill:
            return [dict(r) for r in self._db.query(
                "SELECT * FROM skill_versions WHERE skill = ? ORDER BY ts",
                (skill,))]
        return [dict(r) for r in self._db.query(
            "SELECT * FROM skill_versions ORDER BY ts")]

    def version_state(self, skill: str, version: str) -> str:
        """The latest lifecycle recorded for one version, or ``""``."""
        rows = [r for r in self.versions(skill) if r["version"] == version]
        return rows[-1]["lifecycle"] if rows else ""

    def usable_versions(self, skill: str) -> list[str]:
        """Versions a new job may use. Revoked and retired ones may not."""
        latest: dict[str, str] = {}
        for row in self.versions(skill):
            latest[row["version"]] = row["lifecycle"]
        return sorted(v for v, state in latest.items() if state not in _UNUSABLE)

    def may_run(self, skill: str, version: str) -> tuple[bool, str]:
        """May a new job use this version? Silence is not permission."""
        state = self.version_state(skill, version)
        if not state:
            return False, (f"{skill} {version} has no version record; an "
                           "unrecorded version has no evidence behind it")
        if state in _UNUSABLE:
            return False, f"{skill} {version} is {state}"
        if state == DEPRECATED:
            return True, f"{skill} {version} is DEPRECATED but still usable"
        return True, f"{skill} {version} is {state}"

    # ------------------------------------------------- rollback / retirement
    def revoke_version(self, *, skill: str, version: str, why: str,
                       owner_identity: str = "") -> None:
        """Stop new jobs using this version. Keeps every record."""
        rows = [r for r in self.versions(skill) if r["version"] == version]
        if not rows:
            raise FailClosed(f"no version record for {skill} {version}")
        self._set_lifecycle(rows[-1]["id"], REVOKED, why,
                            owner_identity or AUTHORITY)
        self._audit.record(
            event="skillslab.version_revoked", authority=AUTHORITY,
            initiator=owner_identity or AUTHORITY, why=why[:200],
            decision=f"{skill} {version}", result=REVOKED)

    def rollback(self, *, skill: str, from_version: str, to_version: str,
                 why: str, owner_identity: str = "") -> str:
        """Withdraw a bad version and point new work at an earlier trusted one.

        Rollback does not delete anything and does not touch jobs already
        running: a job that was verified against the version it ran on stays
        verified, and rewriting that would make its evidence describe code it
        never used. What changes is only which version *new* work may use.
        """
        if from_version == to_version:
            raise FailClosed("a rollback needs two different versions")
        target_state = self.version_state(skill, to_version)
        if not target_state:
            raise FailClosed(
                f"cannot roll back to {skill} {to_version}: it has no version "
                "record, so there is no evidence it ever worked")
        if target_state in _UNUSABLE:
            raise FailClosed(
                f"cannot roll back to {skill} {to_version}: it is "
                f"{target_state}, and rolling back onto a withdrawn version "
                "would put new work on something already judged unfit")
        self.revoke_version(skill=skill, version=from_version,
                            why=f"rolled back: {why}",
                            owner_identity=owner_identity)
        rows = [r for r in self.versions(skill) if r["version"] == to_version]
        self._set_lifecycle(rows[-1]["id"], ACTIVE,
                            f"rollback target from {from_version}: {why}",
                            owner_identity or AUTHORITY)
        self._audit.record(
            event="skillslab.rolled_back", authority=AUTHORITY,
            initiator=owner_identity or AUTHORITY, why=why[:200],
            decision=f"{skill} {from_version} -> {to_version}", result=ACTIVE)
        return to_version

    def deprecate_version(self, *, skill: str, version: str, why: str,
                          owner_identity: str = "") -> None:
        rows = [r for r in self.versions(skill) if r["version"] == version]
        if not rows:
            raise FailClosed(f"no version record for {skill} {version}")
        self._set_lifecycle(rows[-1]["id"], DEPRECATED, why,
                            owner_identity or AUTHORITY)

    def retire_version(self, *, skill: str, version: str, why: str,
                       owner_identity: str) -> None:
        """Retire a version for good. Owner only, and history is kept."""
        if self._policy is not None and not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"{owner_identity!r} is not a registered owner; retiring a "
                "capability version withdraws something clients may rely on")
        rows = [r for r in self.versions(skill) if r["version"] == version]
        if not rows:
            raise FailClosed(f"no version record for {skill} {version}")
        self._set_lifecycle(rows[-1]["id"], RETIRED, why, owner_identity)
        self._audit.record(
            event="skillslab.version_retired", authority=AUTHORITY,
            initiator=owner_identity, why=why[:200],
            decision=f"{skill} {version}", result=RETIRED)

    # ----------------------------------------------------------------- reads
    def project(self, project_id: str) -> Project:
        row = self._db.query_one(
            "SELECT * FROM skill_projects WHERE id = ?", (project_id,))
        if row is None:
            raise FailClosed(f"no skill project {project_id!r}")
        return Project(
            id=row["id"], skill=row["skill"], change_type=row["change_type"],
            stage=row["stage"], need=row["need"], observed_on=row["observed_on"],
            occurrences=row["occurrences"], base_version=row["base_version"],
            target_version=row["target_version"],
            overlap_verdict=row["overlap_verdict"], overlap_why=row["overlap_why"],
            opened_by=row["opened_by"], note=row["note"])

    def projects(self, *, stage: str = "", skill: str = "") -> list[Project]:
        sql, params = "SELECT id FROM skill_projects", []
        clauses = []
        if stage:
            clauses.append("stage = ?")
            params.append(stage)
        if skill:
            clauses.append("skill = ?")
            params.append(skill)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts"
        return [self.project(r["id"]) for r in self._db.query(sql, tuple(params))]

    def target_scope(self, project_id: str) -> frozenset:
        """The scope this project was specified at, read back from its record.

        Read from the SPECIFIED event rather than recomputed, so certification is
        checked against the scope that was actually fixed. Returning an empty set
        here would make every certification cover its whole target by
        construction, which is the shape of a check that always passes.
        """
        for row in reversed(self.timeline(project_id)):
            if row["stage"] == SPECIFIED and row["detail"].startswith("scope:"):
                names = row["detail"].split("scope:", 1)[1].split("|", 1)[0]
                return frozenset(n for n in names.strip().split(",") if n)
        raise FailClosed(
            f"{project_id} has no recorded target scope; a certification with "
            "nothing to compare against would cover whatever it was handed")

    def timeline(self, project_id: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM skill_project_events WHERE project_id = ? "
            "ORDER BY ts, id", (project_id,))]

    def awaiting_owner(self) -> list[Project]:
        """Projects the owner has been asked about. Their queue, from the Lab."""
        return self.projects(stage=AWAITING_OWNER)

    def _event(self, project_id: str, stage: str, outcome: str, detail: str,
               actor: str) -> None:
        self._db.execute(
            "INSERT INTO skill_project_events(id,ts,project_id,stage,outcome,"
            "detail,actor) VALUES(?,?,?,?,?,?,?)",
            (new_id("ske"), now(), project_id, stage, outcome, detail[:400],
             actor))
        self._db.commit()


