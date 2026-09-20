"""Black-box certification harness. TEST INFRASTRUCTURE, NOT BUSINESS LOGIC.

Nothing in this package is a Solvent authority. It plays the parts around
Solvent — clients, the owner, failures, crashes — and grades the result against
ground truth Solvent cannot see.

Three rules keep the grading honest:

**Ground truth is written before Solvent runs.** Every scenario declares what a
correct outcome looks like at construction time. The harness never inspects what
Solvent did and then decides what it should have done.

**Ground truth never enters Solvent.** It lives in harness memory. Solvent
receives only what a real client would have handed over: a file and a list of
requirements. A test at the end of the suite greps the Solvent database for the
ground-truth markers and fails if any appear.

**The harness cannot help Solvent pass.** It never amends Policy to make a test
easier, never records a verification, never edits an artifact, never marks a
requirement satisfied. Those are asserted by a test, not by good intentions.
"""
