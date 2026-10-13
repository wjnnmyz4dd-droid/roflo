"""Mutation testing for Solvent's load-bearing controls.

A passing suite proves the tests ran. It does not prove they would notice if
the control they are named after stopped working. This breaks one control at
a time and insists the designated test *newly* fails -- then puts it back and
insists the failure goes away.

Three outcomes matter:

``KILLED``
    The designated test passed before, failed with the mutant, and passed
    again after the revert. The control is pinned.
``SURVIVED``
    The test passed anyway. The control is not pinned by that test, whatever
    the test's name claims. This is the finding.
``NOT_APPLIED``
    The old text was not found -- the file moved on and the mutant is stale.
    **Not a kill.** Counting it as one is how a mutation report becomes a
    decoration.

Usage::

    python3 tools/mutate_solvent.py                 # every mutant
    python3 tools/mutate_solvent.py --only t2       # name substring
    python3 tools/mutate_solvent.py --list
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


@dataclasses.dataclass(frozen=True)
class Mutant:
    name: str
    module: str
    old: str
    new: str
    test: str
    breaks: str


MUTANTS: tuple[Mutant, ...] = (

    # ---- T2 independent review -------------------------------------
    # The contract asks C_HIGH for "T1 + T2 with verifier != executor".
    # Each mutant below removes one of the things that makes the second
    # opinion a second opinion.

    Mutant(
        "t2-review-takes-any-signed-subject",
        "solvent/independent.py",
        "    if approval.subject != expected:",
        "    if False:",
        "tests_solvent.test_independent_review.APersonsReviewNeedsTheirSignature"
        ".test_a_review_signed_for_a_different_artifact_is_refused",
        "an approval signed for a different artifact would be accepted as a "
        "review of this one"),

    Mutant(
        "t2-review-needs-no-signature",
        "solvent/independent.py",
        "    if approval is None:",
        "    if False:",
        "tests_solvent.test_independent_review.APersonsReviewNeedsTheirSignature"
        ".test_an_unsigned_review_is_refused",
        "an unsigned claim of review would be recorded as owner evidence"),

    Mutant(
        "t2-worker-may-review-itself",
        "solvent/audit.py",
        '        if reviewer_identity in ("worker", "execution"):',
        "        if False:",
        "tests_solvent.test_independent_review.APersonsReviewNeedsTheirSignature"
        ".test_the_executor_cannot_review_its_own_work",
        "the executor could sign off its own work as the independent review"),

    Mutant(
        "t2-differential-ignores-disagreement",
        "solvent/independent.py",
        "    if disagreements:",
        "    if False:",
        "tests_solvent.test_independent_review.TheSecondOpinionIsAboutTheFigures"
        ".test_a_wrong_figure_is_caught_by_the_independent_reader",
        "a deliverable whose figures the independent reader disputes would "
        "still be recorded as reviewed"),

    Mutant(
        "t2-parsing-counts-as-review",
        "solvent/independent.py",
        "    if not findings:",
        "    if False:",
        "tests_solvent.test_independent_review.TheSecondOpinionIsAboutTheFigures"
        ".test_a_brief_with_nothing_recomputable_is_refused_not_passed",
        "'the file opens' would be recorded as an independent review of the "
        "figures"),

    Mutant(
        "t2-any-reader-will-do",
        "solvent/independent.py",
        "    if module is None:",
        "    if False:",
        "tests_solvent.test_independent_review.TheSecondOpinionIsAboutTheFigures"
        ".test_a_format_with_no_independent_reader_is_refused",
        "a format with no independently authored reader would fall back to "
        "Solvent reading its own output twice"),

    Mutant(
        "t2-eligibility-never-blocks",
        "solvent/harness.py",
        "    if not cycle.tier_reachable:",
        "    if False:",
        "tests_solvent.test_independent_review.TheTierIsCheckedBeforeTheMoney"
        ".test_a_job_nobody_can_review_stops_before_any_work",
        "a job whose required tier nothing here can produce would still be "
        "budgeted and executed before the refusal was discovered"),

    Mutant(
        "t2-evidence-needs-no-artifact",
        "solvent/audit.py",
        '        if not artifact_digest:\n'
        '            raise FailClosed(\n'
        '                "independent review evidence must name the artifact '
        'digest it "',
        '        if False:\n'
        '            raise FailClosed(\n'
        '                "independent review evidence must name the artifact '
        'digest it "',
        "tests_solvent.test_independent_review.APersonsReviewNeedsTheirSignature"
        ".test_evidence_without_a_requirement_or_digest_is_refused",
        "review evidence could not be invalidated when the file it reviewed "
        "is corrected"),

    # ---- the Job Finder ---------------------------------------------
    # RULES 1-8. Each mutant removes one thing that keeps a recommendation
    # honest, and the ninety-scenario matrix is what has to notice.

    Mutant(
        "finder-minimum-without-platform-permission",
        "solvent/finder.py",
        "        if not permitted:",
        "        if False:",
        "tests_solvent.test_finder.TheMinimumNeedsAllThreeConditions"
        ".test_the_owner_configuring_it_is_not_enough",
        "a minimum engagement would be billed on platforms whose terms "
        "forbid it"),

    Mutant(
        "finder-minimum-without-agreed-terms",
        "solvent/finder.py",
        "        if not opportunity.structured_terms and not opportunity.owner_entered:",
        "        if False:",
        "tests_solvent.test_finder.TheMinimumNeedsAllThreeConditions"
        ".test_the_platform_permitting_it_is_not_enough",
        "a minimum would be billed to a client who never agreed to one"),

    Mutant(
        "finder-minimum-ignores-the-postings-exclusion",
        "solvent/finder.py",
        '        if "no minimum" in text or "without minimum" in text:',
        "        if False:",
        "tests_solvent.test_finder.NinetyPostings.test_the_matrix",
        "a posting that says 'no minimum charge' would be billed one anyway"),

    Mutant(
        "finder-guesses-hourly-revenue-without-history",
        "solvent/finder.py",
        "            if worked_hours is None:",
        "            if False:",
        "tests_solvent.test_finder.ItWillNotInventTheNumbersItLacks"
        ".test_no_history_means_unknown_not_zero_and_not_an_average",
        "an hourly job would be quoted from no measurement at all"),

    Mutant(
        "finder-invents-an-hourly-rate",
        "solvent/finder.py",
        "            if rate <= 0:",
        "            if False:",
        "tests_solvent.test_finder.ItWillNotInventTheNumbersItLacks"
        ".test_no_configured_rate_means_unknown",
        "revenue would be computed at a rate the owner never set"),

    Mutant(
        "finder-guesses-an-ambiguous-contract-type",
        "solvent/finder.py",
        "    if fixed and not hourly:",
        "    if fixed:",
        "tests_solvent.test_finder.ReadingTheContractType"
        ".test_a_posting_naming_both_is_not_guessed",
        "a posting naming both an hourly rate and a project budget would be "
        "treated as fixed price, and the client billed on terms they never "
        "agreed"),

    Mutant(
        "finder-skips-feasibility",
        "solvent/finder.py",
        "        if not feasible:",
        "        if False:",
        "tests_solvent.test_finder.NinetyPostings.test_the_matrix",
        "work would be recommended that no certified capability can do"),

    Mutant(
        "finder-ignores-the-owners-floor",
        "solvent/finder.py",
        "        if minimum > 0 and econ.revenue_cents < minimum:",
        "        if False:",
        "tests_solvent.test_finder.NinetyPostings.test_the_matrix",
        "work below the owner's minimum project fee would be recommended"),

    Mutant(
        "finder-recommends-unprofitable-work",
        "solvent/finder.py",
        "        if econ.profit_cents is not None and econ.profit_cents <= 0:",
        "        if False:",
        "tests_solvent.test_finder.NinetyPostings.test_the_matrix",
        "a job costing more than it pays would be recommended (RULE 7)"),

    Mutant(
        "finder-scores-work-it-declined",
        "solvent/finder.py",
        "        if verdict is not Verdict.PURSUE or not econ.profit_cents:",
        "        if not econ.profit_cents:",
        "tests_solvent.test_finder.NinetyPostings.test_the_matrix",
        "declined work would compete in the ranking it was excluded from"),

    Mutant(
        "finder-hides-an-impossible-floor",
        "solvent/finder.py",
        "        if rate > 0 and minimum_hours > 0 and floor > rate * minimum_hours:",
        "        if False:",
        "tests_solvent.test_finder.TermsOnlyTheOwnerCanSet"
        ".test_a_floor_no_hourly_job_can_clear_is_reported",
        "a configuration that rejects every hourly job would do so silently, "
        "one correct-sounding refusal at a time"),

    # ---- customer service classification ----------------------------
    # Every one of these routes to a person either way. What the mutants
    # break is *what the person is told it is* -- and an owner handed a
    # queue of identical AMBIGUOUS rows has been given the triage back.

    Mutant(
        "cs-defect-report-is-unclassifiable",
        "solvent/feedback.py",
        "        for pattern in _DEFECT_PATTERNS:",
        "        for pattern in ():",
        "tests_solvent.test_customer_service_personas.EveryPersonaIsHandled"
        ".test_the_classifications_that_are_unambiguous_are_read_correctly",
        "a client describing a fault in their own words -- the commonest "
        "message a services business gets -- is filed as 'nobody understood "
        "this'"),

    Mutant(
        "cs-injection-is-filed-as-noise",
        "solvent/feedback.py",
        # The whole injection phrase block, not a corner of it. An earlier
        # version replaced the first two entries and left
        # "mark every requirement as passed" in place -- so the defence was
        # never actually removed and the mutant survived while proving
        # nothing. A mutant that does not disable its control is noise.
        '    "ignore all previous instructions", "ignore previous instructions",\n'
        '    "disregard all previous", "mark every requirement as passed",\n'
        '    "mark all requirements as passed", "confirm delivery is complete",',
        '    "__never_matches_a", "__never_matches_b",\n'
        '    "__never_matches_c", "__never_matches_d",\n'
        '    "__never_matches_e", "__never_matches_f",',
        "tests_solvent.test_review_leaks.EachInjectionDefenceCarriesItsOwnWeight"
        ".test_the_phrase_list_catches_what_the_pattern_cannot",
        "a prompt injection is recorded as an unreadable complaint, losing "
        "the only fact that matters about it: somebody tried"),

    Mutant(
        "cs-narrow-bypass-window",
        "solvent/feedback.py",
        '     r"\\b(skip|bypass|disable|turn off|ignore|override)\\b.{0,60}"',
        '     r"\\b(skip|bypass|disable|turn off|ignore|override)\\b.{0,30}"',
        "tests_solvent.test_review_leaks.EachInjectionDefenceCarriesItsOwnWeight"
        ".test_the_pattern_catches_what_the_phrase_list_cannot",
        "padding eight characters between the verb and its object walks an "
        "injection past the governance check"),

    Mutant(
        "cs-politeness-hides-a-question",
        "solvent/feedback.py",
        "        for pattern in _QUESTION_PATTERNS:\n"
        "            if re.search(pattern, lowered):\n"
        "                return (Classification.GENERAL_QUESTION,\n"
        '                        "the message asks something rather than reporting a "\n'
        '                        "problem", "MATCHED")\n\n'
        "        if not asks:",
        "        if not asks:",
        "tests_solvent.test_customer_service_personas.EveryPersonaIsHandled"
        ".test_the_classifications_that_are_unambiguous_are_read_correctly",
        "a politely worded question is filed as unclassifiable because it "
        "contained the word 'could'"),

    # ---- owner authentication ---------------------------------------

    Mutant(
        "owner-approval-reusable-under-concurrency",
        "solvent/owner.py",
        "        if cursor.rowcount != 1:",
        "        if False:",
        "tests_solvent.test_independent_review"
        ".AnApprovalIsSingleUseUnderConcurrency"
        ".test_two_threads_holding_one_approval_yield_exactly_one_winner",
        "two callers holding one single-use approval both proceed, because "
        "the loser of the claim is never told it lost"),

    # ---- inference routing -------------------------------------------
    # A router that decides whether prompts leave the machine, and whether
    # a paid endpoint gets called, is a security boundary.

    Mutant(
        "ai-cloud-used-without-permission",
        "solvent/inference.py",
        '        if not self._permitted("cloud"):',
        "        if False:",
        "tests_solvent.test_inference_adversarial.SpendingMoneyNobodyAuthorised"
        ".test_cloud_is_not_used_without_its_own_permission",
        "a configured cloud endpoint would be called, and billed, without "
        "the owner ever permitting it"),

    Mutant(
        "ai-client-content-leaves-quietly",
        "solvent/inference.py",
        '        if not bool(self._ai("permit_cloud_client_content", False)):',
        "        if False:",
        "tests_solvent.test_inference_adversarial.GettingClientContentOffTheMachine"
        ".test_permitting_cloud_does_not_also_permit_client_content",
        "permitting cloud inference would silently also permit the client's "
        "own documents to reach a third party"),

    Mutant(
        "ai-absent-privacy-class-reads-as-safe",
        "solvent/inference.py",
        "        if privacy is None:",
        "        if False:",
        "tests_solvent.test_inference_adversarial.GettingClientContentOffTheMachine"
        ".test_omitting_the_privacy_class_does_not_bypass_the_check",
        "omitting the privacy class would be a way past the check, so the "
        "safest-looking call site would be the unsafe one"),

    Mutant(
        "ai-unknown-cost-reads-as-measured-zero",
        "solvent/inference.py",
        "        if not usage:",
        "        if False:",
        "tests_solvent.test_inference_routing.WhatACallCost"
        ".test_a_cloud_call_with_no_reported_usage_is_unknown",
        "a call whose cost is unknown would be recorded as a measured zero, "
        "overstating every margin it touched"),

    Mutant(
        "ai-unpriced-tokens-read-as-free",
        "solvent/inference.py",
        "        if in_rate <= 0 and out_rate <= 0:",
        "        if False:",
        "tests_solvent.test_inference_routing.WhatACallCost"
        ".test_tokens_without_a_recorded_price_are_unknown",
        "tokens at an unrecorded price would cost exactly zero, which is the "
        "most expensive kind of zero"),

    Mutant(
        "ai-unprobed-route-reads-as-ready",
        "solvent/inference.py",
        "        if self._probe is None:",
        "        if False:",
        "tests_solvent.test_inference_routing.NothingIsReadyUntilItIsConfigured"
        ".test_permitted_but_unprobed_is_unknown_not_ready",
        "a route nobody has probed would report READY, which is the "
        "reassuring-default failure this project keeps finding"),

    Mutant(
        "ai-delivery-may-call-a-model",
        "solvent/inference.py",
        "        if not isinstance(purpose, Purpose):",
        "        if False:",
        "tests_solvent.test_inference_adversarial.GeneratedTextReachingADeliverable"
        ".test_a_made_up_purpose_is_refused_rather_than_coerced",
        "any caller could invent a purpose, so generated text could reach a "
        "deliverable and deterministic verification would stop meaning "
        "anything"),

    # ---- source failure reporting ------------------------------------

    Mutant(
        "discovery-failure-reason-unredacted",
        "solvent/discovery.py",
        '            detail = redact(f"{type(exc).__name__} while parsing: {exc}")',
        '            detail = f"{type(exc).__name__} while parsing: {exc}"',
        "tests_solvent.test_discovery_leaks.ACredentialInAnUpstreamError"
        ".test_no_credential_shape_survives_a_parse_failure",
        "an API error body echoing the request would write the credential "
        "verbatim into the sources page and into an append-only audit log "
        "that cannot be redacted afterwards"),

    # ---- the control centre's view of the runtime --------------------

    Mutant(
        "liveness-stats-without-reading",
        "solvent/web/board.py",
        '        with path.open("rb") as handle:\n            handle.read(64)',
        "        pass",
        "tests_solvent.test_web_status.LivenessComesFromTheHeartbeat"
        ".test_a_heartbeat_this_process_may_not_read_is_unknown",
        "stat() needs no read permission, so a heartbeat the control centre "
        "cannot read would report VERIFIED HEALTHY -- the reassuring default "
        "this project exists to refuse"),

    # ---- acquisition: sources, storefront, prospecting ---------------

    Mutant(
        "terms-grant-outranks-the-platform",
        "solvent/discovery.py",
        "        allowed, finding = self._terms_allow(name, permission)",
        "        allowed, finding = (True, '')",
        "tests_solvent.test_marketplace_permissions.TheTermsOutrankTheGrant"
        ".test_an_owner_grant_alone_does_not_authorise_a_governed_activity",
        "the owner's intention would stand in for the platform's terms, so "
        "DeskPilot would bid on a board whose terms forbid it"),

    Mutant(
        "robots-disallow-ignored",
        "solvent/discovery.py",
        "        crawlable, crawl_why = self._public_discovery_allowed(name)",
        "        crawlable, crawl_why = (True, '')",
        # Pointed at a source excluded *by robots and nothing else*. The
        # first version named the grants-feed test, which is excluded by
        # work type -- so the mutant changed nothing there and survived
        # while proving nothing about the control it was named for.
        "tests_solvent.test_source_audit.ARobotsRefusalIsEnforcedNotJustRecorded"
        ".test_it_is_not_active",
        "a platform's own machine-readable refusal would be ignored and the "
        "source shown as an automated route"),

    Mutant(
        "storefront-advertises-uncertified-work",
        "solvent/storefront.py",
        "        allowed, why = self._capability.may_deploy(service.capability)",
        "        allowed, why = (True, '')",
        "tests_solvent.test_storefront.EveryOfferMatchesACertifiedSkill"
        ".test_withdrawing_a_skill_closes_its_offer",
        "an advert is a promise, and this one would promise work no "
        "certified skill can deliver"),

    Mutant(
        "storefront-order-enters-the-pipeline-twice",
        "solvent/storefront.py",
        "        if order.opportunity_id:",
        "        if False:",
        "tests_solvent.test_storefront.OneOrderBecomesOneOpportunity"
        ".test_an_order_cannot_enter_the_pipeline_twice",
        "one order would become two jobs for one payment"),

    Mutant(
        "prospector-ignores-its-allowlist",
        "solvent/prospecting.py",
        "        if not self.on_allowlist(url):",
        "        if False:",
        "tests_solvent.test_prospecting.ItRefusesToBeUncontrolled"
        ".test_a_domain_off_the_allowlist_is_never_fetched",
        "a research prototype would fetch whatever URL it was handed, which "
        "is an uncontrolled crawl of the internet"),

    Mutant(
        "prospector-ignores-robots",
        "solvent/prospecting.py",
        "        if not self._robots(domain, path):",
        "        if False:",
        "tests_solvent.test_prospecting.ItRefusesToBeUncontrolled"
        ".test_robots_disallowed_paths_are_not_fetched",
        "a path a site disallows would be fetched anyway"),

    Mutant(
        "prospector-has-no-budget",
        "solvent/prospecting.py",
        "            spent, why = self.budget.exhausted()",
        "            spent, why = (False, '')",
        "tests_solvent.test_prospecting.ItRefusesToBeUncontrolled"
        ".test_the_request_budget_stops_the_run_and_says_so",
        "a run would continue past any ceiling the owner set"),

    Mutant(
        "a-posting-may-issue-instructions-unnoticed",
        "solvent/finder.py",
        "        if content.looks_like_instructions(opportunity.description):",
        "        if False:",
        "tests_solvent.test_source_resilience.ListingsThatAreWrong"
        ".test_09_a_posting_issuing_instructions_changes_nothing",
        "a posting trying to command the system would be pursued like any "
        "other listing, throwing away the one fact worth keeping about it"),

    # ---- accepting a contract ---------------------------------------
    # Seven submission gates plus three that only matter once the
    # commitment is binding. Each mutant removes one.

    Mutant(
        "claim-needs-no-accept-permission",
        "solvent/application.py",
        "        permitted, why = self._s.discovery.may(source, access.ACCEPT_CONTRACT)",
        "        permitted, why = (True, 'permitted')",
        "tests_solvent.test_claiming.AcceptingNeedsItsOwnPermission"
        ".test_submit_permission_does_not_let_it_accept",
        "permission to bid would be read as permission to accept, which is "
        "the exact distinction the seven activity kinds exist to keep apart"),

    Mutant(
        "claim-does-not-recheck-the-capability",
        "solvent/application.py",
        "        deployable, why = self._s.capability.may_deploy(capability)",
        "        deployable, why = (True, 'deployable')",
        "tests_solvent.test_claiming.CanTheWorkStillBeDone"
        ".test_a_withdrawn_capability_stops_the_commitment",
        "a contract would be accepted for work whose skill was withdrawn "
        "between the bid and the acceptance"),

    Mutant(
        "claim-accepts-terms-nobody-approved",
        "solvent/application.py",
        "        if approved_cents and offered_cents != approved_cents:",
        "        if False:",
        "tests_solvent.test_claiming.AreTheseTheTermsTheOwnerApproved"
        ".test_a_client_accepting_at_their_own_number_needs_the_owner",
        "a client could accept at their own number and bind DeskPilot to it"),

    Mutant(
        "claim-may-be-made-twice",
        "solvent/application.py",
        '            if attempt["phase"] == "CLAIM" and attempt["outcome"] in tx.NO_RETRY:',
        "            if False:",
        "tests_solvent.test_claiming.AtMostOneObligation"
        ".test_an_unknown_outcome_also_blocks_a_second_attempt",
        "one piece of work could become two obligations after a network "
        "failure left the first outcome unknown"),

    # ---- running unattended -----------------------------------------

    Mutant(
        "a-caller-may-widen-the-owners-job-limit",
        "solvent/operation.py",
        "            max_jobs=min(asked.max_jobs, owner.max_jobs),",
        "            max_jobs=asked.max_jobs,",
        "tests_solvent.test_operation_adversarial.RunningLongerThanAllowed"
        ".test_a_caller_cannot_widen_the_job_limit",
        "code could grant itself more jobs than the person who carries the "
        "consequences allowed"),

    Mutant(
        "the-halt-is-checked-only-once",
        "solvent/operation.py",
        "            if self._halted():",
        "            if False:",
        "tests_solvent.test_operation_adversarial.TurningItOffMustAlwaysWork"
        ".test_disabling_mid_run_stops_the_next_iteration",
        "the emergency halt would not stop a run already going round"),

    Mutant(
        "zero-jobs-is-rounded-up-to-one",
        "solvent/operation.py",
        "        if self._configured_jobs() < 1:",
        "        if False:",
        "tests_solvent.test_operation_adversarial.ZerosTheOwnerActuallyMeant"
        ".test_zero_jobs_per_run_does_no_jobs",
        "an owner setting zero jobs per run would get one anyway, and would "
        "not be told their limit had been widened"),

    Mutant(
        "a-zero-ceiling-means-unlimited-again",
        "solvent/operation.py",
        "                if (report.committed_cents > 0",
        "                if (limits.max_committed_cents",
        "tests_solvent.test_operation_adversarial.ZerosTheOwnerActuallyMeant"
        ".test_a_ceiling_of_zero_permits_no_commitment",
        "a ceiling of nothing would be read as no ceiling at all, which is "
        "what Bounds' own docstring says it is not"),

    # ---- the platform's own answer ----------------------------------

    Mutant(
        "polling-ignores-the-robots-finding",
        "solvent/discovery.py",
        "        readable, readable_why = self._public_discovery_allowed(source.name)",
        "        readable, readable_why = (True, '')",
        "tests_solvent.test_operation_adversarial"
        ".PollingObeysThePlatformsOwnAnswer"
        ".test_a_source_whose_robots_forbids_reading_is_not_polled",
        "a site whose robots.txt disallows automated reading would be "
        "crawled as long as somebody had marked it PERMITTED"),

    Mutant(
        "an-unstated-price-is-worth-nothing",
        "solvent/finder.py",
        "            if opportunity.quoted_cents <= 0:",
        "            if False:",
        "tests_solvent.test_operation_adversarial.APostingThatNamesNoPrice"
        ".test_no_stated_price_is_unknown_not_zero",
        "a posting naming no budget would be declined as too cheap, which "
        "is a false statement about a posting that named no figure"),

    # ---- what reaches the append-only log ---------------------------

    Mutant(
        "a-failing-transport-logs-its-credential",
        "solvent/application.py",
        '                    f"{type(exc).__name__} while accepting: {exc}")',
        '                    f"{type(exc).__name__} while accepting: {exc}"',
        "tests_solvent.test_operation_adversarial.ClaimingUnderAttack"
        ".test_a_transport_failure_does_not_leak_a_credential_into_the_log",
        "an HTTP client's exception quotes the request it was making, and "
        "the request carries the source's credential"),

    Mutant(
        "the-redactor-knows-only-field-names",
        "solvent/resilience.py",
        '    (re.compile(r"\\bgh[pousr]_[A-Za-z0-9]{16,}"), "[REDACTED]"),',
        "",
        "tests_solvent.test_redaction_shapes.EveryShapeIsCaught"
        ".test_each_shape_is_redacted_under_an_unhelpful_label",
        "a provider-shaped token under a label the list does not know "
        "would be stored in full"),

    # ---- the six-platform interface ---------------------------------

    Mutant(
        "an-unresearched-host-is-governed-less-than-a-researched-one",
        "solvent/discovery.py",
        '            return self._uncatalogued(name, "reading its listings")',
        '            return True, ""',
        "tests_solvent.test_retired_sources.WhatRetiringThemWouldHaveOpened"
        ".test_an_uncatalogued_host_may_not_have_its_listings_read",
        "deleting a catalogue entry would make its source more permitted "
        "than it was before, which is the opposite of what retiring a "
        "source should mean"),

    Mutant(
        "an-uncatalogued-host-may-be-acted-on",
        "solvent/discovery.py",
        "            return self._uncatalogued(name, permission)",
        '            return True, ""',
        "tests_solvent.test_retired_sources.WhatRetiringThemWouldHaveOpened"
        ".test_an_uncatalogued_host_may_not_be_acted_on",
        "a site nobody researched could be bid on, messaged and delivered "
        "through"),

    Mutant(
        "a-fetching-source-needs-no-rate-limit",
        "solvent/discovery.py",
        "            if self._fetches(source):",
        "            if False:",
        "tests_solvent.test_retired_sources.AFetchingSourceDeclaresItsLimit"
        ".test_a_fetching_source_without_an_interval_cannot_poll",
        "a source that makes external calls and declares no interval would "
        "be polled with nothing holding it back"),

    # ---- the four commercial terms ----------------------------------

    Mutant(
        "unconfigured-terms-still-recommend-work",
        "solvent/finder.py",
        "        configured, gaps = self.commercially_configured()",
        "        configured, gaps = (True, [])",
        "tests_solvent.test_commercial_configuration"
        ".NothingIsRecommendedUntilAllFourAreSet"
        ".test_an_unconfigured_installation_recommends_nothing",
        "work would be recommended by an installation that cannot tell "
        "work worth taking from work that merely looks like it"),

    Mutant(
        "the-owners-margin-is-ignored",
        "solvent/finder.py",
        "        margin = self.minimum_margin_fraction",
        "        margin = 0.0",
        "tests_solvent.test_commercial_configuration.TheOwnersMarginIsApplied"
        ".test_clearing_cost_by_a_hair_is_not_worth_taking",
        "a job clearing its cost by a cent would count as worth taking, "
        "which is what the owner set a margin to prevent"),

    Mutant(
        "an-uncited-rate-counts-as-evidence",
        "solvent/finder.py",
        "        if not citation:",
        "        if False:",
        "tests_solvent.test_commercial_configuration.AnUncitedRateIsNotEvidence"
        ".test_an_uncited_rate_cannot_be_read_for_a_quote",
        "a number typed into the rate table with no source would price "
        "real work as though it were researched"),

    # ---- who is asking to be paid -----------------------------------

    Mutant(
        "an-invoice-may-name-nobody",
        "solvent/harness.py",
        '    allowed, why = s.policy.may_contract_for("INVOICE")',
        "    allowed, why = (True, '')",
        "tests_solvent.test_commercial_configuration"
        ".AnInvoiceNamesWhoIsAskingToBePaid.test_no_identity_means_no_invoice",
        "a document that looks like a bill, names nobody to pay and cannot "
        "be sent would be produced, and the pipeline would report the "
        "invoice stage as done"),
)


def _drop_bytecode() -> None:
    """Delete every __pycache__ before running.

    This is load-bearing, not hygiene. CPython decides a .pyc is current by
    comparing the source mtime it recorded against the source's mtime, at
    **one-second granularity**. A mutation harness rewrites a file, runs a
    test and rewrites it back, often inside the same second -- so the
    interpreter happily imports bytecode compiled from the *previous*
    contents.

    When that happens the results are not merely noisy, they are wrong in
    both directions: a mutant can read as SURVIVED because the test ran
    against unmutated bytecode, and a revert can read as "did not recover"
    because the restored source was never recompiled. Both were observed
    here before this existed.
    """
    for cache in ROOT.rglob("__pycache__"):
        for pyc in cache.glob("*.pyc"):
            pyc.unlink(missing_ok=True)


def run(test: str) -> tuple[bool, str]:
    _drop_bytecode()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", test, "-v"],
        cwd=ROOT, capture_output=True, text=True, timeout=1800, env=env)
    return done.returncode == 0, (done.stdout + done.stderr)[-400:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    chosen = [m for m in MUTANTS if args.only in m.name]
    if args.list:
        for m in chosen:
            print(f"{m.name:40s} {m.module}")
        return 0

    results = []
    for mutant in chosen:
        path = ROOT / mutant.module
        original = path.read_text()

        if original.count(mutant.old) != 1:
            found = original.count(mutant.old)
            print(f"NOT_APPLIED  {mutant.name}  "
                  f"(old text found {found} times, need exactly 1)")
            results.append((mutant, "NOT_APPLIED", ""))
            continue

        green, _ = run(mutant.test)
        if not green:
            print(f"HARNESS      {mutant.name}  "
                  f"(baseline is not green; nothing to prove)")
            results.append((mutant, "HARNESS", ""))
            continue

        path.write_text(original.replace(mutant.old, mutant.new))
        try:
            still_green, tail = run(mutant.test)
        finally:
            path.write_text(original)

        back_green, _ = run(mutant.test)
        if not back_green:
            print(f"HARNESS      {mutant.name}  (did not recover on revert)")
            results.append((mutant, "HARNESS", ""))
            continue

        verdict = "SURVIVED" if still_green else "KILLED"
        print(f"{verdict:12s} {mutant.name}")
        if verdict == "SURVIVED":
            print(f"             would mean: {mutant.breaks}")
        results.append((mutant, verdict, tail))

    killed = sum(1 for _, v, _ in results if v == "KILLED")
    print(f"\n{killed}/{len(results)} killed"
          f"  ({sum(1 for _, v, _ in results if v == 'SURVIVED')} survived, "
          f"{sum(1 for _, v, _ in results if v in ('NOT_APPLIED', 'HARNESS'))}"
          f" not applied or harness)")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
