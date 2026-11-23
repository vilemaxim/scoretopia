# Testing game-driving code without the live game client

Research for issue #62 (map #60). Written 2026-11-23.

**Question.** The code that recognises screens and sends input has no tests;
every check today needs the real client. What approaches would let it be
tested from recorded screens, what do comparable screen-automation projects
do, which recordings already exist or could be gathered by existing jobs,
and which one approach is recommended?

## Answer in short

Test from **recorded frames replayed at two levels, behind one seam**:

1. **Recogniser goldens.** Keep a local corpus of recorded 1280x720 frames,
   each with a same-stem JSON label saying which screen it is and what the
   recognisers should read from it. One discovery-driven test runs every
   recogniser against every frame. This is the practice this repository
   already uses for OCR.
2. **A scripted fake screen.** Replace the only two things that touch the
   world (take a frame, send input) and the clock with a fake that serves
   labelled frames, changes frame when the expected input arrives, and logs
   every input. Navigation, waits, and recovery are tested against it.

It cannot tell whether the recorded frames still look like today's client,
whether a click really leads where the fake says it does, or anything about
timing, the game server, or the X tools. A short live check after a client
update stays necessary.

## How this was investigated

- The private infrastructure repository's screen module and three of its
  playbooks (capture, new game, controls) were read at the last commit the
  local copy holds (2026-11-23). They are described here in general terms
  only; nothing is quoted from them.
- This repository's existing golden-file tests and accuracy harness.
- Public primary sources: Stb-tester's manual and its open-source test
  suite, openQA's documentation and os-autoinst's test suite, Playwright's
  snapshot documentation, pytest's documentation, and the X.Org manual pages
  for `Xvfb` and `xwud`. Links are under [Sources](#sources).
- One trial, run locally: the screen module was imported on a machine with
  no game and no virtual display, and its recognisers were run over the 18
  frames in this repository's local `samples/screenshots/`. Result below.

## What the game-driving code looks like today

The screen module is about 1,500 lines. It falls into three layers that are
already separate in the text but not in how they are wired.

| Layer | What it is | Touches the world? |
|-------|------------|--------------------|
| Recognition | About fifty functions that take an image and return a yes/no, a name, a position, a list of card bands, or a bit mask. Colour shares in fixed boxes, pixel probes, and white-text cell masks compared by Hamming distance. | No. Pure functions of a PIL image. |
| Frames and input | One function that takes a frame (X window dump, converted to PNG, loaded with PIL, refused unless 1280x720) and one that runs the input tool. Click, pointer park, and the pull gesture are built on the second. One more call restarts the client's service. | Yes. These are the only ones. |
| Waits and moves | Polling for a screen, recovery, going to the list or the main menu, switching tab, scrolling to the top, the stale-list check and its repair, the client restart with its settled-menu wait. | Only through the layer above and the clock. |

Findings that matter for testing:

- **Recognition is testable today, unchanged.** In the trial the module
  imported with no display present and classified 18 frames in under half a
  second. Its screen-naming function answered: 11 list-with-panel, 2 bare
  Ongoing list, 1 game setup, 1 tribe pick, 3 unknown. The 3 unknown frames
  are victory screens, which the victory recogniser does identify but the
  screen-naming function does not include.
- **Recognisers are context-dependent.** In the same trial a dialog
  recogniser meant for the in-game view answered yes on 2 list-with-panel
  frames. It is only ever called where that dialog can appear, so this is
  not a bug in use, but it means a golden label has to say which recognisers
  apply to a frame, or the cross-check has to be a deliberate matrix. The
  module's own comments already record this kind of check by hand ("false on
  the other N frames checked", for sets of about 30 to 40 frames).
- **The seam exists as two functions, but is not injectable.** Waits and
  moves call the frame and input functions and `time.sleep`/`time.time`
  directly, through module globals, about twenty clock calls in all. Run
  settings, the temporary frame path, the deadline, and the last badge
  reading are module-level state.
- **About a thousand lines of driving logic are not in the module at all.**
  The capture playbook and the new-game playbook each carry several hundred
  lines of Python inline in YAML: the per-game capture sequences, the list
  sweeps, the option-row walker, create, start, accept, and resign. They
  import the module's functions by name. Inline code cannot be imported by
  a test, and names bound at import are not affected by patching the module.
- **What recovery does.** It waits for a screen it knows, and returns at
  once on the main menu or the Multiplayer list with no panel. From an
  invitation's panel it clicks that panel's back button. From the settings
  screen it uses the back arrow to the main menu; from tribe pick it backs
  out to game setup, and from game setup to the list. Otherwise it presses
  Escape, re-reads the screen, and repeats up to a small fixed number of
  times, then stops the run with an "unknown screen" code. It never presses
  Escape on the list, where Escape opens settings. A second mode, used after
  a stop, sends Escape only, except on the invitation panel. Every one of
  these rules is a statement about (screen seen, input sent, screen
  expected next), which is exactly what a scripted fake screen can check.

## What this repository already does

- `tests/test_sample_screenshot_ocr_goldens.py` discovers image/JSON pairs
  in `samples/screenshots/` and asserts every field of the extraction
  against the golden. The directory is gitignored because the frames show
  player names; with no samples the parametrised test collects nothing.
- `src/scoretopia/screenshot/accuracy.py` holds the discovery function and
  a harness that prints counts only, never field values.
- `tests/test_screenshot_steam.py` needs no image: it builds synthetic OCR
  lines laid out as on a 1280x720 frame and tests the parsers per field.
- There is no CI workflow in the repository; tests run from a checkout.

So the repository already has both habits the recommendation needs: local
private goldens found by discovery, and synthetic inputs that can be
committed.

## What comparable projects do

**Stb-tester** (image-based testing of set-top boxes and TVs, Python).

- Recognition is organised as "Page Objects", classes whose properties are
  computed from one frame. The manual tells authors to save screenshots of
  each screen under `selftest/screenshots/` so that "you will be able to
  develop and test your Page Object without actually running a test", and
  says this "is much more efficient because running a test is relatively
  slow". The service runs the page-object code against those saved
  screenshots and shows each property's value.
- The frame-object constructor "takes an optional frame of video; if the
  frame is not provided, it will grab a live frame". Its image functions
  take the same optional `frame` argument. That one optional argument is the
  whole seam between replay and live use.
- Frame objects are immutable and describe one frame, so reading the screen
  and acting on it are kept apart.
- Its own test suite keeps dozens of small PNGs beside the tests and loads
  them as frames (`tests/test_frameobject.py`, `tests/test_match.py`).
- For behaviour over time it uses a hand-written fake:
  `tests/test_transition.py` defines `FakeDeviceUnderTest`, with a `press`
  method that changes an internal state and a `frames` generator that yields
  1280x720 frames for that state, drawn in code or loaded from a file, with
  a counter of key presses to ignore so that retries and timeouts can be
  tested. No device and no video pipeline are involved.

**openQA / os-autoinst** (drives whole operating systems in virtual machines
by screen matching).

- A reference, called a needle, "consists of a full screenshot in PNG format
  and a json file with the same name" holding the relevant areas and tags.
  Matching is fuzzy, per area. That is the same pairing as this repository's
  goldens: full frame plus a JSON sidecar.
- Each run "generates a video, screenshots and a JSON file with detailed
  results", and new needles are made from the screenshots of a run. The
  corpus is grown from what ordinary runs already produce.
- The matcher itself is tested offline against stored image and JSON pairs
  (`t/01-test_needle.t` and the data directories beside it).

**Playwright** (browser automation; whole-image goldens).

- "On first execution, Playwright test will generate reference
  screenshots"; later runs compare against them, with a tolerance in
  differing pixels or colour distance, and are refreshed with an update
  flag.
- It warns that rendering "can vary based on the host OS, version,
  settings, hardware" and that tests should run in the environment where
  the references were made. Whole-image goldens are tied to one renderer.

**pytest.** The `monkeypatch` fixture sets or replaces an attribute for one
test, and "all modifications will be undone after the requesting test
function or fixture has finished". This is enough to swap a module's frame,
input, and clock functions without redesigning it, as long as the code under
test reaches them through the module.

**X tools.** `Xvfb` is "an X server that can run on machines with no
display hardware and no physical input devices", and `xwud` displays "in a
window an image saved in a specially formatted dump file, such as produced
by xwd". A recorded frame can therefore be put back on a real virtual
display.

## Approaches compared

| Approach | What it checks | Cost and limits |
|----------|----------------|-----------------|
| **A. Recogniser goldens**: recorded frame plus JSON label, every recogniser against every frame | Each screen test, band finder, tab reader, button finder, and mask on real pixels; false positives across screens | Needs a labelled corpus. Frames show names, so the corpus stays local and uncommitted, as the OCR samples do. Works on today's module unchanged. |
| **B. Scripted fake screen**: fake frame source, input sink, and clock | Navigation, waits, recovery, timeouts, the two-frames-in-a-row rule, the run budget, which inputs are sent and which never are | Needs the seam to be reachable, and needs the inline playbook code moved into importable modules. The fake's transitions are an assumption, not a recording. |
| **C. Fake display**: a real `Xvfb` showing recorded frames, the real frame and input tools | The frame-grab path end to end, that the tools are installed and the frame is 1280x720 | Something must still change the picture when a click arrives, which is approach B rebuilt as an X program. Adds X dependencies to the test run. Not tried here; both tools are present on the development machine. |
| **D. Whole-frame image goldens** (Playwright style) | That a frame is pixel-equal, within tolerance, to a reference | Wrong fit: almost every frame legitimately differs (names, lists, scroll offsets). The existing recognisers are already the tolerant comparison. |
| **E. Synthetic frames drawn in code** | Logic that does not depend on real pixels: flat-frame detection, wait and retry rules, Hamming matching and its tolerance | Committable, runs anywhere. Cannot show that a recogniser works on the real screen. |
| **F. Live check on the real client** | Everything, once | Slow, serial, changes real state (invitations, games, resignations). This is today's only method. |

## Recommendation

Adopt **A and B together, with E to fill in what can be committed**, and
keep F as a short manual check rather than the test loop.

1. **One corpus, one label format.** A local, gitignored directory of
   1280x720 PNG frames with a same-stem JSON label, found by discovery and
   skipped when empty, as `samples/screenshots/` is today. A label names the
   screen and carries only what recognisers should return: screen name,
   selected tab, number of card bands, whether a panel is open, which
   dialogs show, and boxes for the buttons the code clicks. Labels hold no
   names, so a label is safe even where a frame is not.
2. **Recogniser matrix test.** For every frame, every recogniser listed in
   its label must agree, and the screen-naming function must return the
   labelled screen. Recognisers that are only meaningful in one context are
   checked only on frames labelled for that context. This replaces the
   hand-kept "false on the other N frames" notes.
3. **Click targets from labels.** Where a label has a button's box, a test
   asserts that the point the code would click lies inside it. This is the
   only offline check on coordinates.
4. **A fake screen with three parts**: a frame source that returns the frame
   for the current state, an input sink that records each input and applies
   a small transition table (state, input) to next state, and a clock that
   advances without sleeping. The table can delay a change by a number of
   stale frames, to exercise the "must hold on two frames in a row" rule
   and the input lag, and can ignore an input, to exercise timeouts. This is
   Stb-tester's `FakeDeviceUnderTest` pattern.
5. **Navigation tests state rules, not scripts.** For example: from every
   labelled screen, recovery ends on the main menu or the list, or stops
   with the unknown-screen code; Escape is never sent while the list shows;
   nothing is clicked after a client restart until two settled menu frames;
   a run stops rather than exceed its budget.
6. **Build the seam in from the start.** The map intends game control to be
   specified afresh, so the new code should take its frame source, input
   sink, and clock as parameters (or as one small object), keep recognisers
   as pure functions of a frame, and keep all sequences in importable
   modules. Where a transition table is driven by synthetic stand-in frames
   and stand-in recognisers, the navigation tests need no private frames and
   can be committed and run anywhere; with the real corpus present the same
   tests run on real pixels.
7. **Optionally one smoke test of approach C**, skipped unless `Xvfb` is
   present: show one frame, grab it through the real path, and assert it
   comes back 1280x720 and classifies the same. It guards the frame path
   only.

## What the recommendation cannot cover

- **Whether the recordings are still true.** A client update that moves a
  button or changes a colour leaves every replay test green. Replay catches
  regressions in the code, not changes in the game.
- **Whether the transition table is true.** That a click at a point opens a
  given screen is asserted by the fake, not observed. Only the
  point-inside-labelled-box check narrows this.
- **Timing.** Input lag, a menu that has not settled after a restart,
  mid-scroll frames, and animation frames can be imitated but not measured.
- **Client and server behaviour.** Lists that do not reload, an invitation
  arriving on its own, a game that stays on the wrong tab after another
  player resigns, whether the pull gesture reloads anything.
- **The tools and the machine.** Input delivery, X errors, the frame grab
  failing while the client starts, the service restart, file modes and
  ownership of what is written.
- **Real effects of irreversible actions.** Creating, starting, accepting,
  and resigning are only known to work when done against the real service.
- **Screens nobody recorded.** An unlabelled screen is just "unknown" to
  the tests, as it is to the code.
- **What the frames say.** Names, scores, and ids are the OCR tests' job.

## Recordings: what exists and what could be gathered

**Exist now**

- **This repository's local samples.** 18 frames at 1280x720, 9 with OCR
  goldens. By the screen module's own classification: 11 Multiplayer list
  with a panel open, 2 bare Ongoing list, 1 game setup, 1 tribe pick, 3
  victory screens. Verified in the trial above. They carry OCR goldens, not
  screen labels; labels would have to be written.
- **The scheduled capture job's output.** For each game it keeps full
  frames on the game machine, in the directory Scoretopia ingests from:
  for a setup capture the card panel and its more-info panel; for a result
  capture the replay's setup panel, its more-info panel, and the victory or
  resigned-game end screen. These are five kinds of screen, growing with
  every game. How many are on the machine now was not checked.
- **Frames from earlier measurement sessions.** The module's comments cite
  checks against sets of about 30 to 40 frames, including in-game views and
  dialogs. Where those frames are kept was not found; the infrastructure
  repository tracks no images.

**Could be gathered by an existing job, as it is**

- **The controls job's screenshot control** saves one full frame of
  whatever the screen shows, sends no input, and keeps the file in a
  private directory on the game machine. Run after its main-menu control it
  gives a main-menu frame; run while another job or a person has left a
  screen showing, it gives that screen. It is one frame per run, by hand.

**Not kept today**

- The new-game job (create, start, accept, resign) and the other controls
  write their frames to a temporary directory that the run removes. These
  runs pass through most of the screens missing from the corpus: main menu,
  settings, game setup at both scroll ends, tribe pick on the create and
  accept sides, lobby panels, the invitation panel, the turn-0 dialog,
  in-game settings, the resign confirmation, and the last-human dialog.
- No job records video.
- A flat frame needs no recording; it can be drawn in code.

**Would need the maintainer's approval** (listed, not proposed as decided)

- Changing the existing jobs so that a run can keep the frames it reads
  instead of deleting them, which would fill the corpus from ordinary
  scheduled runs, as openQA does.
- Any new job whose purpose is to walk the screens and record them.
- Copying frames off the game machine to wherever tests run, since frames
  show player names and must stay out of the public repository.

## Not verified

- The number and age of captures on the game machine, and where the
  measurement-session frames are kept. Only the repository text was read;
  the machine was not contacted.
- The infrastructure checkout was read as it stood locally; it was not
  refreshed, so later changes are not reflected.
- Approach C was not run.
- Quotations from the Stb-tester manual, Playwright, and pytest were read
  through a page-summarising fetch tool; the Stb-tester and os-autoinst
  test files, the openQA document, and the manual pages were read directly.

## Sources

- Stb-tester manual, Object Repository: <https://stb-tester.com/manual/object-repository>
- Stb-tester manual, Python API (`FrameObject`, `match`, `ocr`, `press_and_wait`): <https://stb-tester.com/manual/python-api>
- Stb-tester source, `tests/test_frameobject.py`, `tests/test_match.py`, `tests/test_transition.py`: <https://github.com/stb-tester/stb-tester/tree/main/tests>
- openQA, Getting Started (architecture, needles): <https://github.com/os-autoinst/openQA/blob/master/docs/GettingStarted.md>
- os-autoinst source, `t/01-test_needle.t`: <https://github.com/os-autoinst/os-autoinst/blob/master/t/01-test_needle.t>
- Playwright, Visual comparisons: <https://playwright.dev/docs/test-snapshots>
- pytest, How to monkeypatch/mock modules and environments: <https://docs.pytest.org/en/stable/how-to/monkeypatch.html>
- X.Org manual pages `Xvfb(1)` and `xwud(1)`: <https://www.x.org/releases/X11R7.7/doc/man/man1/>
- This repository: `tests/test_sample_screenshot_ocr_goldens.py`, `tests/test_screenshot_steam.py`, `src/scoretopia/screenshot/accuracy.py`, `.agent-context.md`
