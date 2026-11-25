# Scoretopia

Scoretopia sits in a friend group's Polytopia games from an account of its own, records what happens in them, and reports to the group's Discord channel.

## Language

**Scoretopia**:
The whole system: the part that drives the game, the part that records it, and the part that talks to Discord.
_Avoid_: the bot

**Seat**:
The Polytopia account Scoretopia occupies in a game so that it can observe it. The seat never plays to win.
_Avoid_: the bot, bot account, bot player

**AI opponent**:
A computer-controlled player supplied by Polytopia.
_Avoid_: bot

**Known player**:
A person on the list of players Scoretopia accepts invitations from and keeps statistics for.

**Admin**:
A person allowed to manage groups: create, change, and delete them, and consent on a player's behalf.
_Avoid_: owner, maintainer (that is the person who looks after the code)

**Group**:
A named set of two or more known players for whom Scoretopia keeps one game going. No two groups have exactly the same members.

**Consent**:
A member's agreement to be in a group, given by their own action or by an admin for them. A group does nothing until every member has it.

**Opt-out**:
A player's withdrawal: either leaving one group, or a permanent refusal of all games Scoretopia starts, which only that player can undo.
_Avoid_: unregister, block

**Active group**:
A group whose every member has consented and is a friend of the seat, and which is not paused. Only an active group has games started for it.

**Group's game**:
A game whose players are exactly a group's members plus the seat. AI opponents do not change this.

**Paused group**:
A group for which no new game is started until someone resumes it. A member or an admin can pause it, and Scoretopia pauses it when a game could not be got going. A game already in progress carries on and is recorded.

**Lobby**:
A game that has been created but not started, waiting for its invited players to accept. It counts as the group's game in progress.

**Game settings**:
A group's choices for the games started for it: mode, score limit, map type, map size, and turn time limit. Each is one fixed value or a random pick from an allowed list, weighted toward values the group has played less.

**Routine**:
A rule Scoretopia follows on its own, built from its capabilities, such as starting a game when the group has none active.
_Avoid_: playbook (that word means an Ansible playbook)

**Seat control**:
The part of Scoretopia that recognises the game's screens and acts on them: accepting invitations, starting games, resigning, and capturing.
_Avoid_: the driver, automation, playbook

**Capture**:
The frames seat control saves of one game at one moment, with a manifest, for the recording side to read.
_Avoid_: screenshot (a capture may hold several)

**Orders**:
What the recording side tells seat control to do on its next run: which known players to accept invitations from, and which games to start.
_Avoid_: instructions, commands (those are what players send from Discord)

**Run**:
One hourly pass of seat control over the game client, from restarting the client to starting any games its orders ask for.
_Avoid_: job, poll (recording polls; seat control runs)

**Run report**:
What seat control leaves behind after a run: what it did, what it skipped, and whether it stopped early.

**Game reading**:
A fact read from a game's screens, such as its players, tribes, map, turn, or scores.
_Avoid_: stats

**Statistic**:
A figure computed over many games, such as a win ratio by player, tribe, or map.
_Avoid_: stats
