# Review method rules

**Status:** current · **Date:** 2026-09-06

These thirty-two rules come from forty-two rounds of review over the PowerShell support design.
That document set has been retired (`powershell-support-lightweight.md` records the final design;
the history is in Git), but the rules themselves have nothing to do with that subject — each one
exists because *not* having it let a defect through, they are ordered by how many times that
happened, and every one of them holds for any review. So they live here on their own.

The examples still cite the rule identifiers of the time (`IMG-01`, `LAUNCH-08`, …) and the file
names of the time. **Those identifiers no longer exist.** They are coordinates for the cases
here, showing what concrete defect each rule was bought with.

1. **When borrowing, read the whole function in order, and take its test corpus with it.** Three
   separate rounds each took only the piece of codex's lowering that a finding had named and left
   the gates around it where they were; the counter-example had been sitting in its fixture file
   the whole time. A defence borrowed piecemeal has its holes in exactly the seams nobody looked
   at.
2. **Write each requirement where an implementer will copy it from, then check the prose and the
   table against each other.** A requirement stated in prose and contradicted by the
   specification table beside it has not been stated. This caused four independent P0s. **And
   when two rules point at the same object — a trusted root, a floor, a filter — check that they
   point at it with the same strength:** rev 21 found a filtered `PATH` rejected by D4 as an
   insufficient trust level and accepted by 5a as a trusted root, in the very revision that
   closed that hole. One fix must reach every rule that names the thing being fixed. That is a
   grep, not a re-read.
3. **Ask what a rule quantifies over, and check both ends of the direction it carries.** A
   fail-closed assertion applied to the wrong unit is fail-open for the right one; a predicate
   over the predecessors says nothing about the last element; and a recursive analysis that
   reports only "what contaminated this file *internally*" says nothing at all about what the
   file leaves for its caller.
4. **When a design says "closed", ask what the rule does with the case its list omits.** If the
   answer is "allow", it is a blacklist. And listing the alphabet of a stateful checker specifies
   none of its behaviour. **This rule was written down three revisions before anyone applied it
   to 5a**, where the omitted case was "any explicit `.exe`" and the answer was indeed "allow" —
   a rule that is written down reviews nothing until someone walks it across every rule that
   calls itself closed. **It took eight more revisions to reach ENV-03:** the threat model says
   the whole inherited environment is untrusted, while ENV-03 was a removal list of three names,
   and its omitted cases (`GIT_CONFIG_*`, `NODE_OPTIONS`) answered "pass through". The word
   "closed" does not appear in that rule, so the rule would never be caught by asking about the
   wording — ask about **every untrusted input the threat model names**, not about how a rule is
   phrased.
5. **When it says "lock-free", count the writers; when it says "same as today", verify today.**
6. **A check evaluated by the thing being checked is not a check.** A guard running inside an
   interpreter cannot attest that interpreter; a static path is not immutable bytes. **Nor can a
   program be attested by starting it** — starting it is the very event the check exists to gate,
   and every field a child process reports about itself is reported by the suspect. The choice
   has to be made host-side, before the first byte executes.
7. **Run the code.** §2.7, §2.12, §3.4, §3.8–§3.12 and §3.14–§3.16 exist because reasoning gave
   the wrong answer for every one of them.
8. **When a revision writes down a lesson, apply it to that same revision first — including the
   edit that wrote it.** The round that recorded rule 1 violated rule 1 in the same pass. **A
   number about this document is an assertion in this document:** this line once said eighteen
   rounds while the heading said nineteen, because the heading was updated and the sentence under
   the rule was not. Every self-referential count has to be re-derived whenever the thing it
   counts changes. **rev 37 adds: "that same revision" includes the edit that wrote the rule.**
   rev 36 wrote method rule 19 (a completeness promise needs somewhere that actually counts) and
   missed three mandatory fields in the same function; rev 36 added "the line in the code" to
   rule 11 and mirrored only one of the review package's three conditions in that same edit —
   **mirroring one condition is not mirroring the check**. The moment a rule is written down, the
   lines most in need of it are the ones under your hands.
9. **A gate that can never go red gates nothing.** Separate a release gate from a characterising
   probe, and write the expected result into the probe (§6). **But "write the expectation in"
   only holds for something already measured:** rev 28 pre-filled G21-17 with "expect the DLL to
   be loaded from the working tree", which is precisely what the probe was there to ask — and
   guessed wrong, since PowerShell's location is runspace state rather than the process's current
   directory, and the residue differs by rung entirely. **What can be derived does not need a
   probe, and what cannot be derived must not have its answer written in** — such a probe is
   written as "record these three measured values", and a change in either direction still fails
   the suite. The same mistake had already been made once in rev 16 of the hooks plan
   (pre-filling probe answers in a reversal list); this was the second time.
10. **A branch that cannot be reached is not a defence.** `allow_git_bash` guarded a tier below
    `cmd`, and every supported Windows has `cmd.exe` — so the switch, the gate that pinned the
    ordering, and the probe that tested that tier were all green on a path production cannot
    reach. When a rule carries an ordering, check that every position in it is reachable.
11. **Changing a rule is not finished until every summary, table and gate that references it has
    been re-read.** rev 22 rewrote 5a and left the TL;DR and §1 still treating the allowlist as an
    alternative, a gate pointing at a case no gate scheduled, and §5 still saying "one token, one
    task" — the next round's three findings were all old text living where that edit had not
    gone. The mechanical version is a grep, run before this round closes rather than left to the
    next reviewer, with the three clauses the round that wrote this rule lacked: **normalise
    whitespace first** — the wording it missed had been hyphen-wrapped onto the next line;
    **scan each twin in its own wording**, because the translated document does not contain the
    word you changed; and **the list is every term this round changed**, not the one that produced
    a finding — rev 23 changed a predicate, a clear list and a signature, scanned only the
    predicate, and left the other two in the table. **rev 36 adds: the list is not only
    documents.** rev 35 fixed the "a sub-rule may lean on its parent" relation in `coverage` and
    `gates_naming` and left three other functions in the same script consuming the same relation
    untouched, so the main check passed while the review package reported the same gap —
    **changing a predicate means grepping every consumer of that relation, which is the same act
    as grepping every reference when changing a rule.**
12. **A rule may have exactly one definition point; everywhere else cites its identifier.** rev
    21 through rev 24 each led with "a rule was changed and a copy did not follow", and the round
    that wrote rule 11 missed a scan itself — a rule that depends on someone remembering to grep
    fails the same way as no rule at all. So rev 25 collapsed the definition to one: the
    invariant table in the specification file. The TL;DR, the target architecture, the PR table,
    the gates and the index carry identifiers only. `scripts/check_design_set.py` checked it
    mechanically: every identifier defined exactly once, every referenced identifier existing,
    every identifier having at least one gate and one PR, Windows-only cases landing on the PR
    with the Windows job, numbered list items not appearing mid-line, and adjacent duplicated
    phrases (rev 23's `task.cancel()` fragment) not appearing. **rev 38 extended it from rules to
    predicates:** "does this rule have a gate" and "does the contract anchor it" were each written
    twice, in `check_set` and in the `--changed-since` review package, so rev 36, 37 and 38 all
    led with "the check accepts a path the report does not". Adding a condition only adds a copy —
    extracting `gates_for()` and `anchored()` and calling both from each side is what collapses
    the definition to one. **rev 40 adds the other half: express a relation as a predicate, not by
    polluting data.** To say "changing a sub-rule touches the parent's gates", the parent
    identifier was merged into the set of "what actually changed" — the relation held, and every
    other reader of that set now read a falsehood: the parent's other sub-rules became "changed"
    too, and a gate naming only a sibling lost the warning it should have raised. **Widening a set
    to express a relation imposes that relation on every user of the set.** **rev 44 is the fifth
    time, and this one was inside the single definition point:** `gates_for()` had long been the
    one definition of "which gates cover this rule", but it had two routes inside it (a matrix
    row, and a `Gnn` on the rule's own line) and parent-child inheritance was written on only the
    first. Coverage therefore depended on **which kind of gate the parent happened to have**: the
    SUB, MCP and ENG families are all defined by bullets, so adding a sub-rule to any of them was
    judged ungated. **A single definition point is not a single application point — once a
    predicate is collapsed into one function, ask whether every branch in its body applies it.**
13. **Every dialect tier needs a syntax gate as strong as LOWER-02's, and every name a gate offers
    as an allowed example must be checkable, word for word, against the definition of inertness.**
    Twenty-two rounds of rule-by-rule review plus a split never asked "where is the syntax gate
    for the bash tier" or "what does inert actually mean" — rule-by-rule review sees whether each
    rule is right and cannot see that the tier is missing one. Whole-structure questions have to
    be their own checklist items rather than being expected to surface from a rule-by-rule pass.
14. **The rule table, the pseudocode and the type contract gate each other: every rule that
    produces a verdict needs a reachable branch in the pseudocode and a value in the types that
    can express it.** Eight of rev 27's twelve findings were this seam: EFF-05 says "not inert"
    and `EffectFlag` has no such value; `EXHAUSTED` sits after the check that treats it as an
    unknown rung; `floor()` cannot return the exit state EFF-03 needs; `select_rung` assigns to an
    object declared immutable; a cmdlet is sent through a `PATH` search that always fails.
    Rule-by-rule review reads the table, function-by-function review reads the pseudocode, and
    neither reads the other side — so **start from each rule and find its branch in the
    pseudocode, then start from each branch and find its rule in the table**, once in each
    direction.

15. **Write a closed set together with its membership criterion, then check every member against
    that criterion.** rev 27 made the child environment a closed set and shut off `GIT_CONFIG_*`
    and `NODE_OPTIONS`; the default set, though, was listed off a typical environment, so
    `XDG_CONFIG_HOME`, `HOME` and `SSL_CERT_*` — the same class of key — were listed straight back
    in. **A whitelist with no criterion is as intuition-driven as a blacklist**, only the error
    changes direction from "one missing" to "one too many". Once the criterion is written down
    ("is the value a path?"), the treatment of each of the three member classes is derivable and a
    new key has somewhere to be asked. This is the dual of method rule 4: rule 4 asks what happens
    to the case the list omits, this one asks what earns each item its place. **rev 40 pushed it
    to partitions:** the pinned values were split into "must pass IMG-01" and "shape check only"
    with the criterion never written down, so `PUBLIC` was classified on whether its name looked
    like a system directory — while it is by design a shared user-writable data directory, and one
    `FILE_ADD_FILE` under IMG-06a rejects every policy-enabled rung. **When a set is split in two,
    the criterion belongs on the split, not on each member's own judgement**; written down ("does
    any rule depend on this directory's contents?"), a new key has somewhere to be asked and a
    misfiling can be pointed at.

16. **Write the contract as code a type checker will accept, not as pseudocode; one rule per cell,
    and split when it overflows.** rev 27 (eight of twelve), rev 29 (five of seven, and two more
    on self-review), rev 30 and rev 31 all led with the same class — a return type too small for
    what the rule needs, assignment to an object declared frozen, dereferencing a launcher that
    may be `None`, a variant added without splitting the field — every one of which `mypy
    --strict` reports at zero cost on a real Python module (rev 32 verified three classes with a
    probe). Method rule 14's "once in each direction" then gets a mechanical half: every
    identifier anchored at least once in the contract. The other half is the cell: all 24 lines
    changed between rev 26 and rev 31 got longer and none got shorter, one 2.9 KB cell held eight
    MUSTs, and a gate naming it could not say which sentence it tested — **a cell with no upper
    bound is a specification that is single-definition at the file level and multi-definition
    again at the sentence level.** The bound (900 bytes, three sentences) is checked by
    `check_design_set.py`; a split moves sentences verbatim, and the machine check compares before
    and after word for word, so zero semantic drift is what makes it a structural revision.

    **rev 42 adds: types are not behaviour.** `mypy --strict` catches "the return type is too
    small", "assignment to a frozen object" and "dereferencing a possibly-`None` launcher". It
    does not catch "this branch is now unreachable", "nobody reads this field" or "this fix was
    undone by the next one" — of roughly ten self-inflicted findings across fifteen rounds, four
    lived on four functions in the contract with **no test at all**. A function with a body has to
    be pinned by **behaviour** (`tests/test_powershell_contracts.py`), stubbing the seams and
    asserting nothing there; land each fix's case with the fix, and **revert to the old spelling
    to confirm it goes red**. State the boundary plainly: it stops fixes undoing each other, it
    does not find new defects.
17. **Something already judged reaches execution by exactly one road: the execution end must not
    accept it a second time, and one value must not carry two meanings.** rev 33's P0 was the
    first half — `decide()` took a body and a working directory and `launch()` took each again,
    while the decision compared only the spec's object identity, so "judge `Get-Date`, launch
    other text" was a legal call; **deleting the second entry point from the signature is stronger
    than adding a comparison there**, because with no second copy there is nothing to compare. The
    second half is its dual: in `resolve_reparse() -> AbsPath | None`, `None` meant both "not a
    reparse point" and "could not resolve", the caller had to pick one reading, and it picked the
    permissive one. **Ask two things of every interface — how many sources does this value have,
    and how many meanings does this value carry? An answer other than one is an unspecified
    branch.**
18. **Every registered field needs the place that reads it, and a signature that promises a rule
    must be able to receive what answering that rule requires.** Five of rev 35's nine findings
    were the two directions of this. Direction one, registered and unread: `predicate_positions`
    is EFF-06's entire footprint in the contract and nothing in the file reads it, while
    `ArgPattern.matches` carries a comment saying "the caller has already judged this opaque" — an
    assertion about a caller that no caller honours; `caller_scope` is read, but in only one of two
    branches, so the half of EFF-07's meaning that lives on the other table has no exit.
    Direction two, read and unanswerable: `filtered_path` promises filtering under IMG-01 with no
    oracle in its parameter list, and `resolve` promises to resolve on the filtered `PATH` without
    receiving it — once a signature cannot answer, the implementation has to build its own copy,
    and one rule now has two inconsistent answers. **A machine check cannot catch this class:** the
    anchor check proves the rule is named in the contract, `mypy --strict` proves the types are
    consistent, and the gates prove cases exist (G05-02, G04-18 and G04-32 were untouched that
    round) — none of them proves the branch is not empty. There is one technique: **when you add a
    registered field or a seam that says "per rule X", grep its readers on the spot and check each
    reader's parameter list for the source of the answer.** **rev 38 adds the dual: every input
    that is read in and validated needs the place that writes it down.** An explicit `shell.path`
    passed three checks, the derived rung happened to be policy-disabled, and that construction
    path had no field to hold it — so the validation was performed for a value discarded
    immediately after, and naming `/bin/zsh` produced the same fingerprint as naming nothing. **A
    value having been checked does not mean it was kept.**
19. **A promise of completeness needs somewhere that actually counts, at runtime — an `Optional`
    field and a `Protocol` method both enforce nothing.** rev 36's two P1s are two faces of one
    thing. `PinnedEnv`'s field is declared `AbsDir | None` so one record can hold two platforms,
    which makes "cannot answer `SystemRoot` on Windows" a **legal value**; the validator checked
    the shape and checked cross-platform `None` and never checked presence, while downstream
    treats `None` as "this key is absent". `IdentityOracle` is a `Protocol` while the
    specification says "a missing method ⇒ that rung is unattested" — a static protocol enforces
    nothing about an object an executor hands in, so absence is not a refusal but an
    `AttributeError` inside `launch()`, by which point the call has already been judged allowed.
    **Wherever a specification says "complete / closed / a missing one is a refusal", there must be
    an explicit list and an explicit count**, and the count has to happen before a verdict is based
    on it, not at the first dereference. **rev 37 adds: a non-`Optional` annotation enforces
    nothing either.** `PinnedEnv.home` is declared `AbsDir`, but it is constructed from the
    oracle's answer and `None` gets in regardless, while the shape check in the same function
    explicitly writes `v is None or …` — **"this field cannot be None" is a type checker's
    conclusion, not a runtime fact**, whenever the end that produces it is outside the type
    checker's coverage.
20. **Where a new fail-closed gate is placed matters as much as what it refuses — first ask
    whether any rule explicitly says "this branch runs as before" on a path it now blocks.** rev
    34's P0 and rev 37's heaviest finding share a victim: the two policy-disabled tiers. In the
    first, the ladder could not construct them at all; in the second, to honour SPEC-05c's "a
    missing method is a refusal", the completeness check was put at the entrance to `select_rung`
    — and SPEC-05c's own last sentence is "only the policy-disabled rungs run as before", and
    those two tiers sit downstream of that gate and never ask the oracle at all. **A rule's
    exception clause has to be implemented with the rule, or what is implemented is a different
    rule.** Mechanically: the moment you write the gate, count every `return` in the function it
    sits in and ask which ones are now unreachable — rather than waiting for the next review to
    count them for you. **rev 39 is its third billing, and it was introduced while fixing it:**
    folding `target_filesystem_is_local` into the mandatory answers for tier selection emptied out
    a default executor that lacked only that method — and that exception is written in SPEC-04a
    ("unanswerable reads as false"), not in SPEC-05c. **Exception clauses often do not live with
    the rule, so search by victim, not by rule**: ask "the two tiers this gate blocks — does
    anything anywhere promise them?"
21. **The verdict scans the body; every piece of text agentao itself assembles into the command
    line passes no syntax gate at all.** rev 39's P1 was the first in thirteen rounds to land on
    that half: `<W>` is encoded by `encode_workdir` and interpolated into the prelude, while
    `analyse_body` looks only at the body and the LOWER-01 and BASH-01 gates are outside it too —
    PowerShell recognises five single-quote delimiters and the encoder doubled only the ASCII one,
    so a working directory containing `’` splices a command into the prelude. **The list is every
    value that enters the command line without passing the verdict**: the working directory, the
    `<E>` `<V>` `<H>` `<C>` substituted into the prelude, environment values, the launcher path.
    Ask of each "which lexical version was its encoding rule written against, and how many
    delimiters does that version recognise", and refuse when the answer is uncertain — guessing
    the encoding wrong is an injection, refusing is one `launch-cwd`.
22. **An exception thrown inside the floor is not a verdict.** rev 41's third finding: tool
    arguments arrive as JSON, a `\ud800` escape survives decoding into the Python string as-is,
    and all three of LAUNCH-08's measurements encode first — UTF-16 and UTF-8 both reject a lone
    surrogate, so `floor()` raises `UnicodeEncodeError` **before any analysis at all**. It does not
    travel the DENY channel: no `hardline:` reason, no protection from TOOL-03 ("a floor DENY
    cannot be shadowed by a rule"), and over ACP it may be wrapped by a layer above into a tool
    error the model retries verbatim. **Ask of every call on the verdict path that can raise: once
    this exception leaves, who turns it into a verdict?** Encoding, decoding, path normalisation,
    regular expressions and parsers are all in this class; the answer must not be "a layer above
    catches it" but an explicit refusal before the verdict, with a reason of its own. Assertions
    have to be written as "what verdict was returned", not "what exception was raised".

23. **After narrowing a predicate, re-measure the whole population it used to admit — not just the
    one example the finding named.** rev 43's sixteenth finding: the env check used `key not in
    contract`, necessarily true for short keys (`Path` hides inside `AbsPath`, `join_path`,
    `PSModulePath`), and the fix was whole-word matching **plus full case folding**; the example in
    the finding genuinely stopped being trivially true, but the contract holds 47 lowercase `path`
    occurrences as ordinary parameter names, so `Path` could still be answered by a module that
    never mentions that environment variable — **the hole was not closed, it moved down one
    level**. What has to be measured is "how many ways can this check still be passed on this real
    file", not "is the one line in the finding red now"; it is the measured 47 that says how far to
    narrow (accept only the verbatim and all-caps spellings, because what Windows folds is
    environment key names, not arbitrary identifiers).
24. **A declaration in the contract with no call site is not necessarily dead configuration — first
    ask whether a seam owes it.** rev 43's second finding: `canonicalize` had exactly two
    references in the whole contract (the `Protocol` declaration and its line in `ORACLE_METHODS`),
    neither a call, and review proposed deleting it on that basis — the same shape as rev 41
    deleting `PublisherTrust`'s dead path, which is why it looked convincing. But its call site is
    `filtered_path_entries`, a `raise Unspecified` seam: that obligation text requires "remove
    entries inside the working directory and the project root", while `path_within()` explicitly
    takes **two already-normalised paths** and `PATH` entries are raw strings from the
    environment. **An obligation owed by a seam is also a call site**; deleting the declaration
    turns an unimplemented requirement into a non-existent one, and this one is the gate against
    `..`, short names and symlinks bypassing the containment test. There is one question that
    separates the two: **if this declaration did not exist, which seam's obligation text would
    become impossible to honour?** If you can answer, it is not dead.

25. **A subprocess's silence is not a pass.** rev 44's lead finding: `typecheck_contract` joined
    stdout and stderr on a non-zero mypy exit and reported each line as a failure — and when both
    streams were empty, that list comprehension produced an empty list, which `main()` and
    `test_live_contract_typechecks` both read as "type checking passed". The exits that actually
    look like that are precisely the ones most in need of being seen: an OOM `SIGKILL`, a plugin
    crash, a segfault. **When translating a subprocess's result into a verdict, "it said nothing"
    must have a landing place of its own and must never fall into the success arm** — this is the
    other face of rule 22, "an exception thrown inside the floor is not a verdict": that one asks
    who turns the exception into a verdict, this one asks who declared a pass when nothing came out
    at all. The third face is in rule 18: **a state a rule enumerates needs a type that can hold
    it.** SPEC-04a says "absent and unanswerable both read as false", while
    `target_filesystem_is_local() -> bool` holds only the first, so "unanswerable" had nowhere to
    go but an exception — and that exception escapes `select_rung` before the two policy-disabled
    tiers have even been selected. With the signature changed to `-> bool | None`, `mypy --strict`
    blocks the old spelling by itself.

26. **When the same pair of values is compared in two places, both places must use the same
    rule.** rev 45's self-inflicted finding: `allowlist_entry_for` briefly looked up leniently by
    the target platform's path rules while `HashPin.matches` still confirmed by exact equality. It
    looks like belt and braces — find, then confirm — and is in fact two **different outcomes**
    pressed onto one road: a pin differing in case is found and then fails to match, so "this image
    is not pinned" becomes "this image is untrusted". Which direction the lenient side errs in
    depends on whether the caller uses it as a necessary condition or as a permit path:
    `trusted_image` is the former (stricter), `host_identity_ok` the latter (looser), so **one
    loosening skews in both directions at once**. The practice: decide what rule makes this pair
    equal, then make every site use it; when two sites must differ, the difference needs a name —
    here "who normalises the path the user wrote", which became q15 rather than being quietly
    absorbed by two inconsistent comparisons.

    This is also the dual of rule 12, "a rule has several consumers and only one was changed": that
    one is about **one rule** missing a consumer, this one is about **one criterion** growing a
    second rule.

27. **A fix verified by a probe is not a fix the suite protects.** rev 45's twelfth finding: the
    previous external review's lead finding was "wrapper detection only looks at the body's first
    token", and I verified its fix probe by probe and reported so faithfully — and a repository-wide
    grep for `classify_body` and `unreadable-command-word` found **zero hits in the tests**. A
    probe's conclusion lives in a conversation log that the next refactor cannot read; this round I
    happened to replace that code wholesale, and it was the falsification habit (revert to the old
    spelling and see whether the tests go red) that exposed the hole, not the suite.

    It pairs with rule 16's "a regression suite stops the self-inflicted family and cannot invent
    new defects": that one says what tests **can** stop, this one says that with **no test**,
    nothing is stopped. The criterion is short: **before reporting a fix complete, ask "which test
    goes red if I revert to the old spelling" — if you cannot answer, it is not complete.**

28. **Verify a fix across its whole sink class, and verify it on the platform where the defect
    actually fires.** rev 46's lead finding, and the boundary of rule 27: the fix for doubled CRLF
    was "add `newline=""` to `open()`", I added it in two places and missed the third write site in
    the same function, an `os.fdopen` — which is the only one an **existing** file goes through,
    and therefore the defect's only crime scene. The guard was written, rule 27 could name it, and
    it is permanently green locally (POSIX), so "can you name the test that goes red" was satisfied
    while not one instance of the defect was removed.

    Two criteria: **(a)** when a fix has the shape "add an argument to a call", grep out every call
    of the same kind in that function and that sink class and confirm each one — especially write
    sites that are **not spelled `open(`**, like `os.fdopen` and `os.open`, which a literal grep
    will not find; **(b)** a green run on a platform where the defect does not fire proves the
    guard does not bite, not that the defect is gone (same family as rule 4b: a change of state
    re-runs the whole checklist — here the state is the platform).

    **(c) The dual, added at rev 52: an assertion written on one platform takes that platform's
    answer for the rule itself.** The same function answers "there is no oracle" on POSIX and
    "every tier is refused" on Windows — both are it working correctly, and I asserted the one I
    happened to run. **Ask what the assertion actually claims** (here, "what comes back is a
    verdict rather than an exception") and write that platform-independent sentence; branch or skip
    the platform-specific part rather than letting it pose as the general rule.

29. **A gate that cannot go red is not a gate; when you write one, say first what makes it red.**
    rev 48's lead finding: LADDER-04 wrote one of the flip's preconditions as "G09's three-bucket
    degradation rate is accepted", and "three buckets" appears exactly four times in the whole
    document set, all four of them restatements of that same sentence — never enumerated, no
    threshold, no corpus, no acceptor, and nothing measured for it in the evidence file. It hung on
    the last unclosed precondition and was never named in twenty-one rounds of review, because a
    sentence that **reads like a gate** does not invite anyone to ask what makes it red.

    Two criteria: **(a)** a gate must be able to state what input, what expectation and who judges
    — missing any of the three, it is prose; **(b)** when the quantity you want only exists after
    release (here, "after the flip, how much everyday work becomes a prompt"), what the gate should
    collect is the **instrument and the way back**, not the quantity itself — writing it as a
    precondition makes a permanently open gate look like a decided one.

    **(c)** Also ask "can this quantity be collected at all": rev 53 found the same LADDER-04 had
    left the second step's precondition as "await the distribution reported back by opted-in
    hosts", and this repository has no telemetry and has decided not to build any — **a gate nobody
    can pass is no more a gate than one that cannot go red**. An instrument being present (G09-03's
    classifier) is not a channel being present.

    **(d)** And ask it in reverse: **is there anything in the design that could make it green?** Both
    of rev 55's findings were found this way — the acceptance table had a row each (Chinese output
    uncorrupted, exit codes correct) with not one word of corresponding mechanism in the design
    body, so those two gates could only ever go red. A gate doomed to red and a gate that cannot go
    red are missing the same thing: at the moment the gate was written, nobody checked the other
    half of the sheet.

30. **Taking a name out of a module namespace breaks `monkeypatch.setattr` — and no import graph
    and no linter can see that dependency.** rev 50's second finding: after `select_rung` was
    changed to call `ladder_enabled`, `_trust` no longer did `from … import LADDER_FLIPPED`, while a
    **Windows-only** test was patching exactly that attribute, giving an `AttributeError`. All five
    local gates were green, because that test skips here.

    Two criteria: **(a)** before deleting or changing a module-level name, grep `tests/` for it too
    — `monkeypatch.setattr(mod, "NAME", …)` is a consumer that appears in no import graph and that
    `F401` does not cover; **(b)** more fundamentally, **do not let the patch point have two
    copies**: `from … import CONST` makes a second copy, so which one a patch hits depends on which
    module the test came in through, and each of the two readers can only reach one. Collapsing to
    one function (here `ladder_enabled`) both removes that ambiguity and lets the test set it **the
    way a user actually would**, which is closer to what it is trying to prove than patching an
    attribute is.

31. **Each half having tests does not mean the seam has tests — because every rule is written
    against one half.** rev 55's second finding: `hardline_check`'s PowerShell branch returns a
    refusal when there is no decided record, by design, and `decided_call` freezes a `todays_floor`
    it receives into the verdict as-is, by design — each correct, each tested. And the planner is
    the **only** caller of that path, and never carries a record at that step, so every clean body
    on that tier was refused. Forty-one rounds of review and three hundred-odd findings did not see
    it, because the rules are written per half and so the review reads per half. A chain needs a
    case that walks from the real entry point all the way through, and what it asserts must be
    **allow**: a case that asserts only refusals is green on a path that refuses everything.

32. **Changing something that has not shipped yet makes a compatibility layer worth exactly
    nothing — check the release status before deciding whether to build a migration path.** rev
    55's fourth finding: the plan wrote six lines of migration and conflict table for the
    `shell.ladder` family of keys, and a single `git show v0.4.21:` shows those keys were never in
    any release, so the installed base is zero. The cost is more than that table — a migration
    branch has to be tested, written into the configuration reference, and read again at the next
    change. The loader's key set was already closed, so deleting the keys yields a named error by
    itself; that is all "not silently ignored" ever needed to cost.
