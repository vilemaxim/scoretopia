# Game readings: what can be read, from where, in each state of a game

Research for issue #61 (map #60). Written 2026-11-23 against `main` at `167b13b`.

**Question.** For each state of a game the seat is in (invitation and lobby, in
progress, resigned by the seat but still in progress, finished), which game
readings can Scoretopia obtain, and from which screen or local file? Does an
invitation show who sent it?

**Confidence words used here**

- **confirmed**: seen on a real frame of the Steam client, or read by a parser
  in this repository that was built and measured against real frames.
- **likely**: follows from a confirmed observation or from a public source, but
  no frame of that exact case was seen.
- **needs a live capture**: nothing seen settles it. The screen to capture is
  named.

## Sources

| Tag | Source | What it is |
| --- | --- | --- |
| [code] | `src/scoretopia/screenshot/steam.py`, `models.py`, `captures.py`, `extract.py`, `icons.py`, `tribes.py`; `src/scoretopia/domain/games.py`; `tests/test_screenshot_steam.py` | The parsers for the 1280x720 Steam frames and what the domain does with their output. Primary for "what is read today". |
| [drive] | The private infrastructure repository's game-driving code: one Python screen module and the capture, new-game, and save-file-read playbooks, at its main branch on 2026-11-23 | Primary for "what the client shows": its comments record measurements taken on real frames, with dates. Described here, never quoted. |
| [notes] | The private operations notes behind that code: the dated measurement notes of the invitation, accept, resign, and replay walk-through (2026-11-10), and the private write-up of the save-file investigation (2026-10-11 to 2026-10-18) | Primary observations of the client. Described here, never quoted. |
| [tourn] | Midjiwan, "The Rise of the Tournament Update!", Steam announcement, 2022: <https://steamcommunity.com/games/874390/announcements/detail/3487503395165170265> | First-party. The Steam page body did not render for the fetch tool; the text was read through the mirror at <https://devtrackers.gg/polytopia/p/366d81c7-the-rise-of-the-tournament-update>. |
| [changelog] | Midjiwan staff, pinned "Up to date changelog" on the Steam forum, read through the mirror at <https://devtrackers.gg/polytopia/p/e92335a3-pinned-up-to-date-changelog> | First-party text, mirrored. |

**Not available.** The Polytopia wiki (`polytopia.fandom.com`) refused the
fetch (HTTP 402), so nothing here rests on it. No official page was found that
documents the in-game Game Stats screen, the invitation panel, or what a
resigned player's game list shows. Those points rest on [drive] and [notes]
or are marked as needing a capture. No sample frames were available in this
worktree (`samples/screenshots/` is empty in a fresh checkout), so no frame was
looked at during this research; every "confirmed" below is confirmed by
[code], [drive], or [notes], not by a new look.

## The screens and files that carry readings

Names follow the client's own labels.

| # | Screen or file | How it is reached | What it shows | Read by Scoretopia today? |
| --- | --- | --- | --- | --- |
| A | **Ongoing list** (Multiplayer > Ongoing) | Main menu > MULTIPLAYER | Cards in sections under headers: "Game Invitations", then "Your Turn", then "Their Turn". Each card: game name, a subtitle (such as "Your turn"), a timer badge (time left, or "Time Up!"). Cards on the seat's turn and invitation cards are drawn blue; other cards dark [drive][notes]. | No. Only used to find cards to click. |
| B | **Card panel** (click a card on A) | A | Game name; map size and map type; mode (Glory or Might) with the points needed; game timer; a status line ("It is your turn to Play" or "Waiting for ... to play"); one portrait and name per player; a "You" marker; RESIGN, BACK, OPEN [code][notes]. | Yes: `game_basics` (`parse_steam_game_basics`). |
| C | **Game Info** (More info on a panel) | B, F, or H | Game id; game version; turn. A lobby shows the id only [code]. | Yes: `game_info`. Only the id is used (to de-duplicate). |
| D | **Invitation panel** | Click a card under "Game Invitations"; it also opens by itself when an invitation arrives while the list is open [drive] | The same panel family as B: settings circles and the player portraits with a tick or pending mark each; DECLINE, BACK, ACCEPT [notes]. | No frame of it has been through the parser. |
| E | **PICK YOUR TRIBE (accept side)** | ACCEPT on D | Tribe grid, with four lines of the game's settings under the title [drive]. | No. |
| F | **Lobby panel** | A, for a game not yet started | As B without a status line or win-condition line; players ticked or pending; an empty "Add" slot on the host's side. Host side: START GAME once invitees accepted. Invited side: a line naming who can start the game, LEAVE, and one BACK [code][drive][notes]. | Parser handles it (`test_lobby_*`), but the capture skips lobbies. |
| G | **Game view** | OPEN on B | The map; a turn 0 dialog on first open; bottom bar Settings, Game Stats, Tech Tree, End Turn [notes]. In-game Settings carries RESIGN [drive]. | No. Game Stats has never been opened. |
| H | **Replays list and replay panel** (Multiplayer > Replays) | Tab on the Multiplayer screen | List header "Last 5 Games". Each row: game name, "Ended <date>", "Won by: <name>". Panel: map, mode and points, More info, Share, the text "This game is over, open it to view the final result", the players. No timer and no "You" marker [notes][code]. | Panel yes (`game_basics`, as `setup.png`). The row's text is not read. |
| I | **End screen** | H > OPEN > wait for the replay to play out > Finish Game. Also shown at once to a player who resigns [drive][notes]. | Header: score, stars (with stars gained), turn. A "<name> wins!" line. One row per player: name, tribe, then either "score: N points" or "resigned on turn N", an elo value and its change. A variant for a game ended by resignation adds "No opponents left" and skull icons, and its DONE button sits lower [code][drive][notes]. | Yes: `game_end` (`parse_steam_game_end`). |
| J | **Main menu badge** | Main menu | A count on the MULTIPLAYER button. | No. |
| K | **Client log** (`Player.log`, `Player-prev.log` in the game's persistent-data directory) | Local file | A game-over line naming the winner; a line with a game id when a player resigns; a line saying a game is ready in state Started or Ended (Ended marks a replay being opened) [drive][notes]. | No. |
| L | **Save files** (`<user id>/Data/Multiplayer/<game id>.state`) | Local file | A 6-byte stub for a multiplayer game. A Pass & Play save exists but is undecoded binary [notes]. | No. |

## Readings by state

One table per state. "Source" is the screen letter above.

### 1. Invitation and lobby (game not started)

| Reading | Source | Confidence |
| --- | --- | --- |
| An invitation exists; how many are pending | A: cards under the "Game Invitations" header | **confirmed** that the section exists and that an unanswered invitation stays there after BACK on its panel [drive][notes]. Counting the cards is **likely** workable (the same band detection the capture uses), but no code counts them, and the seat's list only shows others' changes after a client restart. |
| Who sent the invitation | D before accepting; F after accepting | **After accepting: confirmed.** The invited side's lobby panel names the host in a line saying that player can start the game [notes]. **Before accepting: needs a live capture of the invitation panel (D) for a game with three or more players.** On the one invitation measured (two players) the panel showed the host ticked and the seat pending, so the sender was the only ticked player. With more invitees, other players who already accepted are presumably ticked too, and it is not known whether the panel marks the host or orders the host first. Also capture whether D carries the host line that F does. |
| Players (names) | D, F | **likely** for D (same layout as B; never parsed from a real frame). **confirmed** for F in the parser [code], which skips the empty Add slot. Names are OCR readings and need matching against known players. |
| Who has accepted | D, F: tick or pending mark on each portrait | **confirmed** that the marks are shown [notes]; **not read** by any parser. |
| Tribes | — | **Not available as text.** The seat's own tribe is whatever it picked on E. Other players' tribes are at best visible as portrait art: **needs a live capture of F after every player has accepted**, to see whether portraits show tribes before the game starts. |
| Map type and size, mode, points needed, timer | D, F; also E's settings lines | **confirmed** on F and on the started-game panel [code]. On D **likely** (same circles). E shows four settings lines [drive], unread. |
| Game id | C from the lobby | **confirmed**: a lobby's Game Info shows the id, with no version or turn line [code]. |
| Turn, scores, placement, winner, eliminations | — | Not applicable. |
| Whether the seat created the game or was invited | F | **confirmed** distinguishable at the lobby stage only: the host side has START GAME, the invited side has the host line and LEAVE [drive][notes]. Once a game has started no screen tells the two apart [drive]. |

### 2. In progress, seat still playing

| Reading | Source | Confidence |
| --- | --- | --- |
| Players (names) | B | **confirmed** [code]. The frames the driving code was measured on are mostly two-player games; a panel with many players **needs a live capture of B for a large game** to confirm how names wrap and whether the panel scrolls. |
| Which player is the seat | B: "You" marker | **confirmed** [code]. |
| Tribes | B: portrait art only | **needs a live capture of B** with players on distinct tribes, and an icon classifier. No text on A, B, or C names a tribe. Game Stats (G) may list tribes: see scores. |
| Map type and size | B | **confirmed** [code]. |
| Mode and points needed | B | **confirmed** for Glory [code]. The parser recognises the Might label, but what a Might panel shows in place of the points line was not established here, and the accept-side measurements say Glory only [drive]: treat Might as **likely** for the mode itself and unmeasured for the rest. |
| Timer | B | **confirmed** [code]. |
| Turn | C: "Turn" line | **confirmed** [code]. It is the turn as of the client's last reload of the list. |
| Whose turn | A: section and card colour; B: status line | **confirmed** for "seat's turn" [drive][code]. The waiting line names the player being waited for [code]. That a started game on another player's turn draws dark was inferred from lobbies, **not seen** [drive]. |
| Scores | G: Game Stats | **needs a live capture of the Game Stats screen** in a running game. It is the only candidate: no panel, card, modal, log line, or save file carries a score before the game ends [notes]. Not known: whether it lists every player or only tribes the seat has met, and whether it shows scores at all in Might. Opening the game view is something the driving code does only in order to resign. |
| Placement | derived from scores | Depends on the Game Stats capture. |
| Eliminations | B: portrait turns to a skull | **likely**. The parser has an `is_eliminated` flag fed by a grey-skull detector [code], but its Steam path is tested only with a stand-in detector, not a real Steam frame. **Needs a live capture of B for a game in which a player has been eliminated by conquest.** |
| Another player resigned | B: that player's portrait becomes a grey robot | **confirmed** on a two-player game [drive]; the card on A does not change. The mark appeared one to two hours after the resignation, and a client restart before then still showed the plain portrait [drive]. Public changelog: "Resigned players now show as bots" [changelog]. Position of the mark for three or more players is scanned for, not measured. |
| Seat is the last human | G: a "last standing human player" dialog, then Finish Game | **confirmed** once; the dialog does not always appear [drive]. |
| Game id, game version | C | **confirmed** [code]. |

### 3. Resigned by the seat, game still in progress for the others

This is the least known state, and the one the map's mid-game posts depend on.

| Reading | Source | Confidence |
| --- | --- | --- |
| What the seat sees at the moment it resigns | I, shown at once | **confirmed** for a two-player game, where the resignation ends the game: the end screen appears immediately with the winner line, both rows with elo and change, and the seat's row "resigned on turn N" [notes]. For a game with other humans still playing, **needs a live capture of the screen that follows the Resign confirmation**. |
| Where the game then appears on the seat's client | A or H | **needs a live capture**: after the seat resigns from a game with at least two other human players, capture the Ongoing list and the Replays list after a client restart, at once and again an hour later. Unknown whether the card stays on Ongoing, moves to Replays while the game still runs, or disappears until the game ends. In the two-player case it left Ongoing and was on Replays within 15 minutes [notes]. |
| Players, map, mode, points, timer, game id | B or H panel, if the game is still listed | **likely** if the game stays listed anywhere, since both panels carry them. Otherwise only from what was captured before resigning. |
| Turn, scores, eliminations while the others play on | G as a spectator, if offered | **needs a live capture**: open the resigned game from wherever it is listed and capture the game view and Game Stats. The 2022 announcement says "You can now spectate games where you've been defeated" without added vision of the map [tourn]; whether a player who resigned counts as defeated for this, and whether the feature is still present, is not established. |
| Final result once the others finish | H then I | **likely**: a finished online game is saved to the cloud and listed under Replays [tourn], and the resigned two-player game did arrive there [notes]. Whether a game the seat left long before its end reaches the seat's Replays list **needs the same live capture, continued until the game ends**. |
| What the other players see of the seat | their B | The seat's portrait becomes the robot and an AI opponent plays its tribe on [changelog][drive]. Relevant because the seat's row will appear in every final result as "resigned on turn N". |

### 4. Finished

| Reading | Source | Confidence |
| --- | --- | --- |
| Winner | I: "<name> wins!" line | **confirmed** [code][notes]. Also H's row ("Won by: <name>"), **confirmed** shown [notes] but unread; and K's game-over line, **confirmed** [notes]. |
| Players (names) | I rows; H panel | **confirmed** [code]. |
| Tribes | I rows (text, before the score or resignation) | **confirmed** [code]. The end screen is the only place a tribe is written as text. |
| End points per player | I rows: "score: N points" | **confirmed** for rows that have a score [code]. |
| End points of a player who resigned | — | **Not shown.** The row reads "resigned on turn N" in place of a score [code][notes]. |
| End points of a player eliminated by conquest | I row | **needs a live capture of the end screen of a game with three or more players in which one was eliminated without resigning.** Unknown whether that row shows a score, a zero, or another status. |
| Placement of every player | I: row order and scores | **Partly.** Scoretopia ranks the rows that carry a score, by score [code: `domain/games.py`], so a player who resigned gets no placement today. The parser assumes the winner is the first row [code]; the changelog records that "Player sorting in win screen adjusted" without giving the rule [changelog]. **Needs the same three-player end-screen capture** to learn the row order and where resigned and eliminated players are placed. A resignation turn is available to order resigned players among themselves (later turn, better place). |
| Turn the game ended on | I header "turn"; C from the replay panel | **confirmed** read [code]. Whether the header's score and stars are the viewing seat's own figures is **likely** (they sit apart from the per-player rows) and unimportant, since the seat is excluded. |
| Resignations and their turn | I rows | **confirmed** [code]. |
| Eliminations | I: skull icons | **confirmed** that the resignation variant of the end screen draws skulls [notes]; they are not read, and which rows get them when someone was conquered is covered by the capture named above. |
| Elo and elo change per player | I rows | **confirmed** [code]. |
| Map type and size, mode, points needed | H panel | **confirmed** [code]. The replay panel has no timer [code], so the timer is known only from the capture made while the game ran. |
| End date | H row: "Ended <date>" | **confirmed** shown [notes]; unread. |
| Game id | C from the replay panel; K sometimes | **confirmed** on C [code]. The log gives an id only on resignation lines [notes]. |
| End screen of a Might game | I | **needs a live capture of a finished Might game's end screen**: whether rows carry a score when the mode has no score target. |

## Local files: what they do and do not give

- **Client log.** Read over whole logs after several finished games, the log
  yielded the winner's name on a game-over line, a game id only where a resign
  line followed, and a marker telling a live game end from a replay being
  opened. No setup field (players, map, mode, points needed) and no end points
  or turn appear anywhere in it [notes]. It records nothing when an invitation
  is sent [drive]. The log is replaced on each client start and only one
  previous log is kept, so a game's lines survive one restart, not two [notes].
  Verdict: **confirmed** useful only as a cross-check of the winner.
- **Save files.** A multiplayer game leaves a 6-byte stub written after the
  game ended; it opens under no decompression tried. No replay or archive file
  is written locally, including after a replay is opened [notes]. Verdict:
  **confirmed** no reading available. The investigation's own caveat applies:
  it shows these reads did not find the data, not that the data is absent.

## Constraints that shape any routine built on these readings

1. **The seat's lists do not update live.** Changes made by other players (an
   accepted invitation, a turn played, a new invitation in an already open
   list) reach the Ongoing list and its panels only after a client restart; a
   pull-to-refresh did not reload them [drive]. Every reading above is "as of
   the last restart".
2. **A resignation by another player shows late**: one to two hours on the one
   game measured [drive]. That is outside the map's "within an hour".
3. **Replays is headed "Last 5 Games"** [notes]. If the list holds only five,
   a result must be captured before five later games finish. **Likely**, from
   the header text; the cap itself was not tested.
4. **The end screen costs a replay.** It is reached only by opening the replay
   and waiting for it to play to its last turn (about 15 to 20 s per turn
   measured), then Finish Game [drive]. Long games take proportionally long.
5. **The main menu badge is ambiguous.** One measurement had it count
   invitations together with games on the seat's turn [notes]; the screen
   module documents it as counting only games on the seat's turn [drive]. It
   cannot by itself say "an invitation is waiting".
6. **An arriving invitation opens its panel unasked** over the Ongoing list,
   and Escape does not close it; BACK does, leaving the invitation pending
   [drive]. Any routine on that list must expect it.
7. **Names are OCR readings.** Every player name above comes through OCR and
   has to be matched against known players; ids of players are shown nowhere.

## Live captures needed, in one list

| # | Capture | Settles |
| --- | --- | --- |
| 1 | The **invitation panel** for a game with three or more players, at least one other invitee having accepted | Whether the sender can be told before accepting; whether the panel names the host |
| 2 | The **Ongoing and Replays lists, then the game view and Game Stats**, after the seat resigns from a game with at least two other humans; repeated until that game ends | Everything in state 3: where the game is listed, whether it can be spectated, whether its result reaches Replays |
| 3 | The **Game Stats screen** in a running game (Glory and Might) | Whether scores, and so placement, can be read mid-game; whether it lists tribes |
| 4 | The **end screen of a finished game with three or more players**, one eliminated by conquest and one resigned | Row order, placement of resigned and eliminated players, whether an eliminated player has end points, which rows get skulls |
| 5 | The **end screen of a finished Might game** | Whether rows carry scores in Might |
| 6 | The **card panel of a running game** with distinct tribes, one player eliminated, and as many players as the group ever uses | Tribes from portraits, the skull detector on Steam frames, name layout for a large game |
| 7 | The **lobby panel** after every invitee has accepted | Whether tribes are visible before the start |

## Answers in brief

- **Players, map type and size, mode, points needed, timer, game id**: readable
  from the lobby onward, from the card panel and Game Info. Confirmed.
- **Turn**: readable in progress from Game Info and at the end from the end
  screen header. Confirmed.
- **Tribes**: written as text only on the end screen. Before that, portraits
  at best. Needs a capture.
- **Scores**: only on the end screen today. Mid-game needs the Game Stats
  capture.
- **Winner**: end screen, Replays row, and the client log. Confirmed.
- **Placement of every player**: only partly. Players who resigned have no
  end points on the end screen, and the order of rows is not established.
- **Eliminations**: resignations are confirmed (robot portrait in progress,
  "resigned on turn N" at the end). Conquest eliminations need a capture.
- **Invitation sender**: confirmed readable after accepting (the lobby names
  the host). Before accepting it is only inferable in a two-player invitation;
  the general case needs a capture.
- **A game the seat resigned that is still running**: nothing is known beyond
  the two-player case, where resigning ends the game. Needs a capture.
