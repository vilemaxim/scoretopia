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

**Group**:
A set of known players who have each consented to Scoretopia keeping a game going for them. A player can withdraw consent.

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
