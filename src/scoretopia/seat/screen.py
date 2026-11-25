"""Screen tests and input for Polytopia on a 1280x720 X display.

The screen layer of seat control (ADR 003). It was written for the infrastructure playbooks,
which copied it to the game machine and drove the client from a short
script, and it is moved here with its behaviour unchanged. Comments that cited those
playbooks and their runs have been reworded; the measurements they record are kept,
because each threshold below was chosen from them.

What is here:

  Frames and input: grab(), save(), xdo(), click(), park(), pull_refresh().
  Pixel helpers: px(), dark(), blue(), mean(), flat(), share(), lit().
  Screen tests, each taking a frame and returning what it shows:
      main_menu(), on_multiplayer(), mp_list(), selected_tab(),
      settings_screen(), setup_screen(), setup_top(), setup_end(),
      tribe_screen(), accept_tribe_screen(), list_back_tribe(), pick_ready(),
      invite_panel(), resigned_panel(), game_settings_screen(), exit_ready(),
      resign_ready(), resign_confirm(), finish_green(), exit_blue(),
      victory(), resigned_over(), game_over(), last_human(), turn0_ok(),
      menu_badge(), and screen_name(), which names the screen by those tests.
  The Multiplayer list: bands(), cut_bands(), split_bands(), row_hit(),
      bot_turn_card(), and a card's identity as a mask of its white text:
      mask_of(), mask_variants(), hamming(), nearest(), match(), key_of(),
      sub_key_of(), list_sig(), subtitle_hash().
  Waits: poll_for(), wait_for(), wait_frame(), budget().
  Moves: recover(), to_list(), to_main_menu(), go_tab(), to_top(),
      restart_client(), reload_view(), list_check(), heal_list().

Coordinates are on the 1280x720 screen. Nothing here prints a frame, a
name, or an id; card keys are hashes of white-text bit masks.

Call configure() with the run's arguments, then use session() around the
work, so frames go to a private temporary directory that is removed after.

The waits and moves call the frame, input, and clock functions directly.
Putting them behind a seam is later work; until then only the screen tests
and the list and mask functions can be tested without a display.
"""

import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time

from PIL import Image, ImageStat

# Run settings. configure() overrides any of them.
A = {
    'display': ':99',
    'menu_xy': [640, 404],   # the main menu's MULTIPLAYER
    'settle': 2,             # pause after a click, before the first look
    'wait': 20,              # least timeout of a wait: input lags 6 to 8 s
    'escapes': 3,            # Escapes recover() presses at most
    'budget': 3300,          # seconds a run may spend; see budget()
    'client_wait': 600,      # restart_client(): how long it may take
    'settle_s': 8,           # restart_client(): gap between settled frames
    'client_poll': 2,        # restart_client(): gap while no menu shows
    'unit': 'polytopia.service',
    'x_user': 'steam',       # who X commands run as when the run is root
    # heal_list(): the pull. No measurement has shown a better value.
    'pull_px': 75,
    'pull_ms': 1000,
    'list_wait': 30,         # list_check(): how long a tab may take to show a card
}
FRAME = None
DEADLINE = time.time() + A['budget']
# Where the pointer waits while a frame is read: empty background, off the
# Multiplayer list (the capture run's spot) and, on GAME SETUP, in a
# description band at both scroll ends.
PARK = (160, 240)
# The Multiplayer list's centre: the middle of the y 130 to 626 band where
# cards are found, on the 320 px card column.
LIST_CENTRE = (640, 378)
IM = ['magick'] if shutil.which('magick') else ['convert']

# PICK YOUR TRIBE grid.
TRIBE_Y = [240, 362, 485]
TRIBE_X = [488, 589, 690, 791]


class Stop(Exception):
    """Ends a run with an error code, the exception's text."""


class Budget(Exception):
    """The run's budget would be passed by the next unit of work."""


def configure(**kw):
    global DEADLINE
    A.update(kw)
    DEADLINE = time.time() + A['budget']


@contextlib.contextmanager
def session():
    global FRAME
    global BADGE
    BADGE = None
    tmp = tempfile.mkdtemp(prefix='steamscreen-')
    FRAME = os.path.join(tmp, 'frame.png')
    try:
        yield
    finally:
        FRAME = None
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------ input, frames

def _x(argv):
    # Run as root (restart_client), X commands go through the screen's own
    # user, so access to :99 is the same as in a run as steam.
    if os.geteuid() == 0 and A.get('x_user'):
        return ['runuser', '-u', A['x_user'], '--'] + argv
    return argv


# The X error classes failed_code() names, from the tool's stderr: Xlib's
# "X Error of failed request: <Class>" line. It also names a display that
# would not open (nodisplay) and a failure of runuser itself (runuser).
# Only these fixed words leave the game machine; the stderr text never does.
X_ERRORS = ('BadWindow', 'BadMatch', 'BadDrawable', 'BadAlloc', 'BadAccess',
            'BadValue', 'BadAtom')


def failed_code(e):
    # The error code for a CalledProcessError: command_failed_<tool>_rc<n>,
    # or _sig<n> when a signal ended it, then _<xerror> when the stderr
    # names one of X_ERRORS or a display that would not open (nodisplay).
    # <tool> is the command after runuser's "--" when _x() wrapped it.
    # Until 2026-10-25 every caller printed the basename
    # of cmd[0], so in a root run every failed X command read
    # command_failed_runuser and the tool and exit code were lost.
    cmd = e.cmd if isinstance(e.cmd, (list, tuple)) else [e.cmd]
    cmd = [str(c) for c in cmd]
    tool = cmd[0] if cmd else 'unknown'
    if os.path.basename(tool) == 'runuser' and '--' in cmd:
        i = cmd.index('--') + 1
        if i < len(cmd):
            tool = cmd[i]
    tool = ''.join(c for c in os.path.basename(tool) if c.isalnum() or c in '-_.')[:32] or 'unknown'
    rc = e.returncode
    code = 'command_failed_%s_%s' % (tool, ('sig%d' % -rc) if rc is not None and rc < 0 else 'rc%s' % rc)
    err = e.stderr or b''
    if isinstance(err, str):
        err = err.encode('utf-8', 'replace')
    m = re.search(rb'X Error of failed request:\s+(Bad[A-Za-z]+)', err)
    if m and m.group(1).decode() in X_ERRORS:
        return code + '_' + m.group(1).decode().lower()
    if b'open display' in err:
        return code + '_nodisplay'
    if err.startswith(b'runuser:'):
        # runuser itself failed, not the tool it was to run.
        return code + '_runuser'
    return code


def grab(path=None):
    path = path or FRAME
    xwd = subprocess.run(_x(['xwd', '-root', '-silent', '-display', A['display']]),
                         capture_output=True, check=True).stdout
    subprocess.run(IM + ['xwd:-', 'png:' + path], input=xwd, capture_output=True, check=True)
    im = Image.open(path).convert('RGB')
    im.load()
    if im.size != (1280, 720):
        raise Stop('frame_not_1280x720')
    return im


def save(im, path, mode=0o600):
    # The file is created with `mode`, 0600 by default, so it is never
    # readable by others even for a moment. Capture passes 0640 for its
    # screens, which recording reads through a shared group.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'wb') as fh:
        im.save(fh, 'PNG')
    os.chmod(path, mode)


def xdo(*args):
    subprocess.run(_x(['xdotool'] + [str(a) for a in args]), capture_output=True, check=True)


def click(x, y):
    xdo('mousemove', x, y, 'click', 1)
    time.sleep(A['settle'])


def park():
    # No click: a pointer over a row changes the white pixels key_of reads.
    xdo('mousemove', *PARK)
    time.sleep(A['settle'])


def pull_refresh(pull_px, pull_ms):
    # A held pull-down on the Multiplayer list: press at its centre, move
    # down pull_px in steps of about 5 px spread over pull_ms, release.
    x, y = LIST_CENTRE
    steps = max(1, int(round(pull_px / 5.0)))
    pause = pull_ms / 1000.0 / steps
    xdo('mousemove', x, y)
    xdo('mousedown', 1)
    try:
        for i in range(1, steps + 1):
            time.sleep(pause)
            xdo('mousemove', x, y + int(round(pull_px * i / steps)))
    finally:
        xdo('mouseup', 1)
    time.sleep(A['settle'])


# ------------------------------------------------------------ pixel helpers

def px(im, x, y):
    return im.getpixel((x, y))


def dark(p):
    return max(p) < 30


def blue(p):
    return p[2] > 170 and p[0] < 80 and 80 < p[1] < 200


def mean(im, box):
    return ImageStat.Stat(im.crop(box)).mean


def flat(im):
    return ImageStat.Stat(im.convert('L')).stddev[0] / 255.0 <= 0.01


def share(im, box, rgb, tol=8):
    # The share of the box's pixels within tol of rgb on every channel.
    area = (box[2] - box[0]) * (box[3] - box[1])
    return sum(n for n, p in im.crop(box).getcolors(area)
               if all(abs(a - b) <= tol for a, b in zip(p, rgb))) / area


def lit(im, box):
    return sum(1 for x in range(box[0], box[2]) for y in range(box[1], box[3])
               if min(px(im, x, y)) > 120)


# ------------------------------------------------------------ screen tests
# Those marked capture are used only when capturing a finished game.

def on_multiplayer(im):
    # The white back arrow and the pink header, and the Ongoing and Replays
    # tab band darker than the header: without the band the test passes on
    # the Player Profile screen (2026-10-20).
    c, h = px(im, 31, 31), px(im, 300, 30)
    tabs = mean(im, (60, 0, 440, 60))[0] - mean(im, (60, 72, 440, 120))[0]
    return min(c) > 150 and h[0] > 140 and h[0] > h[1] + 50 and tabs > 20


def selected_tab(im):
    # The selected tab is always the pill at the centre; the other tab's
    # label sits left of it (Replays selected) or right of it (Ongoing).
    return 'replays' if lit(im, (455, 85, 570, 118)) > lit(im, (712, 85, 820, 118)) else 'ongoing'


def row_hit(im, y):
    # True when both probes inside the 320 px card differ from the
    # background just outside it by more than 90 summed over RGB: x 490
    # against x 470, and x 790 against x 810. The one row test of bands(),
    # cut_bands(), and split_bands(), so the three never disagree.
    return (sum(abs(a - b) for a, b in zip(px(im, 490, y), px(im, 470, y))) > 90 and
            sum(abs(a - b) for a, b in zip(px(im, 790, y), px(im, 810, y))) > 90)


def bands(im):
    # A card or row: rows where row_hit() holds, for at least 40 px.
    # Measured 2026-10-23: Ongoing cards and Replays rows
    # both span x 479 to 800 and are 60 to 61 px tall, so one set of probes
    # (x 470, 490, 790, 810) serves both tabs.
    #
    # Measured 2026-10-24: 5 Ongoing cards from the list's top to its end, 7
    # wheel notches, and 4 Replays rows. From about y 455 down the list lies
    # over the menu scenery, not flat sky. There the right probe pair fell
    # to 60 to 90 for 1 or 2 rows inside the 5th card, splitting it into
    # runs of 24/15/21, 37/23, 34/24, and 35/25 px on the top page and three
    # middle pages; on the last page that card sits at y 393 to 454, whole.
    # The scenery also makes rows between and under cards hit, in fixed runs
    # of 1 to 11 px at y 457 to 506. Over all frames: smallest in-card probe
    # difference 60, largest between-card 137; largest in-card gap 2 px,
    # smallest between-card gap 1 px (a scenery run 1 px from a card).
    #
    # The test was left as it is. Bridging gaps of 1
    # or 2 px joins scenery runs to cards: it drops a card this test reads
    # whole (68 px with the scenery run, over 66) and still loses a split
    # one (73 px). No threshold separates 60 from 137. split_bands() counts
    # what is still dropped.
    out, start = [], None
    for y in range(130, 626):
        hit = row_hit(im, y)
        if hit and start is None:
            start = y
        elif not hit and start is not None:
            if y - start >= 40:
                out.append((start, y))
            start = None
    return out


def cut_bands(im):
    # The runs bands() drops at the list's edges: under 40 px and touching
    # y 130 (under the tab bar) or y 625, as (top, bottom). A run still open
    # at y 625 ends there. Measured 2026-10-23: with 5
    # cards the list opened at its end showed a 15 px run at y 130, the
    # top card, which no read reached; at the top there was none.
    out, start = [], None
    for y in range(130, 627):
        hit = y < 626 and row_hit(im, y)
        if hit and start is None:
            start = y
        elif not hit and start is not None:
            if y - start < 40 and (start <= 130 or y >= 625):
                out.append((start, y))
            start = None
    return out


# split_bands() leaves out runs shorter than this. Measured 2026-10-24:
# the scenery's runs were 1 to 11 px and the
# pieces of a split card 15 to 37 px; a 61 px card split in two or three
# leaves a piece of at least 20 px, so a split card is still counted.
SPLIT_MIN = 13


def split_bands(im):
    # The runs bands() drops in the middle of the list: SPLIT_MIN to 39 px,
    # touching neither y 130 nor y 625, as (top, bottom) — the pieces of a
    # card the scenery split (see bands()). cut_bands() has the ones at the
    # edges. Nothing here says whether another page read the card whole.
    out, start = [], None
    for y in range(130, 627):
        hit = y < 626 and row_hit(im, y)
        if hit and start is None:
            start = y
        elif not hit and start is not None:
            if SPLIT_MIN <= y - start < 40 and start > 130 and y < 625:
                out.append((start, y))
            start = None
    return out


def panel_top(im):
    for y in range(130, 420):
        if dark(px(im, 640, y)) and dark(px(im, 500, y)) and dark(px(im, 780, y)):
            return y
    return None


def find_blue(im, x, y0, y1):
    ys = [y for y in range(max(y0, 0), min(y1, 719)) if blue(px(im, x, y))]
    if not ys:
        return None
    s = e = ys[0]
    for y in ys[1:]:
        if y != e + 1:
            break
        e = y
    return (s + e) // 2 if e - s >= 15 else None


def mp_list(im):
    # The Multiplayer list with no panel open: Player Profile's bottom-bar
    # icon at (660..680, 660..680) is teal where the list's is amber.
    b = mean(im, (660, 660, 680, 680))
    return on_multiplayer(im) and panel_top(im) is None and b[0] > b[2] + 50


# An invitation's panel over the Multiplayer Ongoing list.
# The client opens it on its own when an invitation to the seat arrives,
# with no click: seen on 2026-11-10, when an invitation arrived while the
# client sat on the Ongoing list. It is the panel an invitation card opens:
# black, x 466 to 813 and y 195 to 524; red
# DECLINE top right, x 720 to 801, y 207 to 228; grey (204,204,204) BACK
# bottom left, x 477 to 554, y 480 to 514, centre (516,497); blue
# (0,122,204) ACCEPT bottom right, x 698 to 801, y 480 to about 516. Before
# this test recover() knew no such screen, so to_list() stopped
# `unknown_screen` in three runs that day. Escape does not leave it: the
# frame stayed byte for byte as it was. BACK does: clicked once, it left
# the Ongoing list with no panel and the invitations still pending.
#
# The test, measured on that frame and on an earlier invite-panel frame, and
# false on 46 other saved frames (main menu, both lists, the lobby, live,
# resigned, replay, and Invite players panels, PICK YOUR TRIBE and its
# panel, GAME SETUP, in-game Settings and the Resign confirmation, the
# game and end views): the Multiplayer screen with a panel open, black at
# three points inside the panel, blue() at y 484 at x 705, 750, and 795 but
# not at x 690 (a lobby's START GAME reaches x 690; a started game's OPEN
# starts at x 718), and BACK's grey on at least 90 % of the box left of its
# label. Unconfirmed: whether the panel also opens on a client launch with
# an invitation pending; no frame shows it.
INVITE_BACK = (516, 497)


def invite_panel(im):
    if not on_multiplayer(im) or panel_top(im) is None:
        return False
    if not all(dark(px(im, x, y)) for x, y in ((500, 330), (640, 460), (640, 200))):
        return False
    if blue(px(im, 690, 484)) or not all(blue(px(im, x, 484)) for x in (705, 750, 795)):
        return False
    return share(im, (478, 481, 488, 488), (204, 204, 204)) >= 0.9


# The main menu's pink sky box, right of the Weekly Challenge laurel
# (2026-11-06). Until then it was the corner (20,20)-(42,42).
MENU_SKY = (100, 20, 122, 42)
# The main menu's top-right, the NEWS globe over the sky.
MENU_TR = (1180, 0, 1280, 60)


def main_menu(im):
    # Pink sky beside the top-left (the victory screen and the in-game views
    # are dark there, Settings maroon), a lilac top-right with green over
    # 120 (there the pink headers of GAME SETUP, PICK YOUR TRIBE, Player
    # Profile, and Multiplayer read green 71 to 103), and not the
    # Multiplayer screen.
    #
    # Measured 2026-11-06: the main menu
    # gained an animated "Weekly Challenge" laurel at the top left, spanning
    # x 7 to 79 and y 13 to 98, which covers the old corner box: it read
    # (136,184,195) there, so every wait for the main menu timed out
    # (client_no_menu in one run, timeout_main_menu in another). MENU_SKY sits 21 px right of the laurel at the
    # old box's height and read (245,103,169) on all five menu frames, as
    # the old corner did on menus without the laurel. MENU_TR read
    # (197,138,201) on every menu frame with and without the laurel. On the
    # 24 earlier captures of other screens checked (victory, replay and game
    # views, Settings, in-game Settings, Player Profile, GAME SETUP top and
    # both ends, PICK YOUR TRIBE and its panel, the Multiplayer lists and
    # panels), MENU_TR's
    # green was at most 103 and main_menu() false on every one. The green
    # bound is new: GAME SETUP's end page reads red 227 in MENU_TR, 2 over
    # the red bound, and MENU_SKY is pink there, so without it that page
    # rested on 2 px of red once the corner no longer rejected it.
    c, r = mean(im, MENU_SKY), mean(im, MENU_TR)
    return (c[0] > 200 and c[0] > c[1] + 60 and 150 < r[0] < 225 and r[1] > 120
            and r[2] > 170 and not on_multiplayer(im))


def menu_key(im):
    # The main menu's button column, x 520 to 760 and y 260 to 500, as a
    # mask of 4 px cells that are mostly button blue or near-white. Measured
    # on captures of 2026-10-21: identical on every
    # main-menu frame of one client session, and different on the frame
    # taken straight after a client restart, whose column lacks three of
    # the settled menu's blue runs at x 640.
    bits = []
    for by in range(260, 497, 4):
        for bx in range(520, 757, 4):
            n = sum(1 for x in range(bx, bx + 4) for y in range(by, by + 4)
                    if blue(px(im, x, y)) or min(px(im, x, y)) > 200)
            bits.append('1' if n >= 8 else '0')
    return hashlib.sha256(('menu' + ''.join(bits)).encode()).hexdigest()[:16]


# The main menu's Multiplayer badge (2026-10-25): a red
# disc with a white count, on the MULTIPLAYER button's top-right corner.
# Measured on four main-menu frames of one client session with the seat's
# Ongoing list holding 5 cards, all "Your turn", and the badge reading 5:
# its red pixels span x 714 to 731 and y 375 to 392, centre about
# (723,384), fill (253,73,29). BADGE_BOX is that span plus 1 px. In the box,
# pixels with red over 200, green under 120, and blue under 90 were 49 to
# 54 % of the 400 with the badge: with the pointer at three spots,
# MULTIPLAYER hovered or not, and on a frame 8 s after the back arrow whose
# disc edge still read (204,72,51). Without it: absence was not seen live,
# because the badge cannot be cleared without playing a turn. It is measured
# from the plain button's pixels instead: the mirror of BADGE_BOX about the
# button's centre (x 640), the button's top-left corner and the sky beside
# it, read 0 %, as did every other 20 px box of the button column (x 500
# to 780, y 250 to 500), the sky's nearest pixel being 68 off (253,73,29) on
# a channel. BADGE_MIN sits between. The count is the games waiting on the
# seat, those on "Your turn", not every ongoing game. So
# the badge present with both tabs empty confirms a stale list, and its
# absence proves nothing: games on the other players' turn show no badge.
BADGE_BOX = (713, 374, 733, 394)
BADGE_MIN = 0.15
# menu_badge() of the last main menu to_list() or reload_view() left by
# MULTIPLAYER, this session; None until one is read.
BADGE = None


def badge_red(p):
    return p[0] > 200 and p[1] < 120 and p[2] < 90


def menu_badge(im):
    # True when the main menu shows the Multiplayer badge, False when it
    # shows the plain button, None when the frame is not the main menu.
    if not main_menu(im):
        return None
    b = BADGE_BOX
    n = sum(1 for x in range(b[0], b[2]) for y in range(b[1], b[3]) if badge_red(px(im, x, y)))
    return n >= BADGE_MIN * (b[2] - b[0]) * (b[3] - b[1])


def note_badge(im):
    # Record menu_badge() of the frame MULTIPLAYER is about to be clicked
    # from, for the run's output. Sends no input.
    global BADGE
    BADGE = menu_badge(im)
    return BADGE


def settings_screen(im):
    # The Settings screen Escape opens from Multiplayer: a white back arrow
    # at (31,31) over a dark maroon header.
    t = mean(im, (0, 0, 1280, 60))
    return min(px(im, 31, 31)) > 150 and 30 < t[0] < 90 and t[0] > t[1] + 15 and t[0] > t[2]


def key_of(im, band, kind='setup'):
    # White text only, so a card that turns from blue (your turn) to dark
    # (their turn) keeps its key. setup: the card's title line only, as the
    # new-game run; result: the whole row, as the capture run.
    #
    # Measured 2026-10-23 on Ongoing: a started card's
    # timer badge, red "Time Up!" or green "N hours", fills x 746 to 790 and
    # top+7 to top+52, its white text x 752 to 786, so the setup box (x 540
    # to 740) never reaches it. The title's white text runs top+13 to
    # top+34 and ended by x 728 on the longest title seen. Two frames 2 min
    # apart at one scroll position were identical, and so were their keys.
    #
    # Across scroll offsets the exact key does not hold.
    # An earlier measurement found at most 2 of 350 cells between two offsets
    # 23 px apart, which held for that pair only. Measured 2026-10-23,
    # 5 cards on Ongoing at every wheel
    # notch from the list's top to its end (16 to 18 px a notch; the card
    # pitch is about 77.3 px) and again after a client restart: with the earlier
    # cells (8 of 16 pixels over 180) the same card differed by up to 27
    # cells when it moved, 18 with the box tried at dy -3 to +3, while
    # different cards differed by as little as 19. At equal band tops a
    # restart changed at most 1 cell. So the list renders rows at sub-pixel
    # y: the title sits 1 to 2 px off the detected band top, the band's
    # height flips between 60 and 61, and glyph edges cross the threshold
    # differently at every offset. The restart is not a cause.
    #
    # The fix: a cell is set at CELL_MIN (3)
    # of its 16 pixels, so a glyph edge that moves a pixel rarely clears or
    # sets a cell, and match() tries the box at dy -ALIGN_K to +ALIGN_K (2).
    # On the same frames the same card then differed by at most 15 cells,
    # and different cards by at least 62. A caller that must recognise a
    # card matches mask_variants() with match(), within KEY_TOLERANCE, and
    # uses this hash only where an exact value is wanted: directory names,
    # keys a caller recorded before masks existed, list_sig(), and the
    # scroll-end test.
    m = mask_of(im, band, kind)
    if m is None:
        return None
    return hashlib.sha256(m.encode()).hexdigest()[:16]


# A cell is set when at least CELL_MIN of its 16 pixels are over 180 on
# every channel; 8 until 2026-10-23 (see key_of()). MASK_TAG marks a
# mask made under this rule: hamming() never compares masks of different
# tags, so masks recorded under the earlier rule match nothing and each recorded
# game is captured once more (Scoretopia de-duplicates by game id).
CELL_MIN = 3
MASK_TAG = 'c3'


def mask_of(im, band, kind='setup', dy=0):
    # key_of()'s box and 4 px cells as a bit string, prefixed by kind and
    # MASK_TAG; None when no cell is set. dy moves the box down by dy px.
    # A mask is a picture of the card's text, which can be a game's name:
    # callers keep it on the game machine and never print it.
    top, bottom = band
    top, bottom = top + dy, bottom + dy
    box = (540, top + 8, 740, top + 36) if kind == 'setup' else (540, top + 4, 740, bottom - 4)
    bits = []
    for by in range(box[1], box[3] - 3, 4):
        for bx in range(box[0], box[2] - 3, 4):
            n = sum(1 for x in range(bx, bx + 4) for y in range(by, by + 4) if min(px(im, x, y)) > 180)
            bits.append('1' if n >= CELL_MIN else '0')
    if '1' not in bits:
        return None
    return kind + MASK_TAG + ''.join(bits)


def mask_variants(im, band, kind='setup', k=None):
    # The masks of the box at dy 0, then -k to +k, for match(); k defaults
    # to ALIGN_K. Masks that are None are left out.
    k = ALIGN_K if k is None else k
    out = []
    for dy in [0] + [d for d in range(-k, k + 1) if d]:
        m = mask_of(im, band, kind, dy)
        if m is not None:
            out.append(m)
    return out


# Measured 2026-10-23 (the numbers are in key_of()): with CELL_MIN 3 and
# ALIGN_K 2 the same card differed by at most 15 cells across every wheel
# offset and a client restart, different cards by at least 62 on Ongoing.
# KEY_TOLERANCE sits between the two: at
# least the largest same-card distance, below half the smallest
# different-card distance. An earlier tolerance of 4 held only for the one
# pair of offsets it was measured on.
KEY_TOLERANCE = 22
ALIGN_K = 2


def hamming(a, b):
    # The number of cells in which two masks differ; None when they are of
    # different kind, tag, or length, which never match.
    ka, kb = a.rstrip('01'), b.rstrip('01')
    if ka != kb or len(a) != len(b):
        return None
    return sum(1 for x, y in zip(a[len(ka):], b[len(kb):]) if x != y)


def nearest(mask, masks):
    # The mask in masks nearest to mask, and its distance, as (m, d); mask
    # is one mask or mask_variants()' list, whose nearest variant counts.
    # (None, None) when nothing is comparable.
    if mask is None:
        return None, None
    variants = [mask] if isinstance(mask, str) else [v for v in mask if v is not None]
    best, best_d = None, None
    for m in masks:
        for v in variants:
            d = hamming(v, m)
            if d is not None and (best_d is None or d < best_d):
                best, best_d = m, d
    return best, best_d


def match(mask, masks):
    # The recorded mask nearest to mask within KEY_TOLERANCE, or None; mask
    # is one mask or mask_variants()' list.
    best, best_d = nearest(mask, masks)
    return best if best_d is not None and best_d <= KEY_TOLERANCE else None


def list_sig(im, kind='setup'):
    # The list as counts and one hash: the number of cards and a hash of
    # their sorted keys. Neither carries a name.
    bs = bands(im)
    keys = sorted(k for k in (key_of(im, b, kind) for b in bs) if k is not None)
    return {'cards': len(bs),
            'keys_hash': hashlib.sha256(('|'.join(keys)).encode()).hexdigest()[:16]}


def sub_key_of(im, band):
    # A card's subtitle line, the band below the title box key_of() reads,
    # down to 4 px above the card's edge. Measured on the Ongoing and
    # Replays frames up to 2026-10-22: cards are 60
    # to 61 px tall, the title's white text sits 6 to 33 px below the top,
    # and the subtitle's text 29 to 56 px below it, grey: 1 to 8 pixels per
    # row above 180 on every channel against 22 to 91 above 120. So this
    # reads pixels above 120, not key_of()'s 180.
    top, bottom = band
    box = (540, top + 36, 740, bottom - 4)
    bits = []
    for by in range(box[1], box[3] - 3, 4):
        for bx in range(box[0], box[2] - 3, 4):
            n = sum(1 for x in range(bx, bx + 4) for y in range(by, by + 4) if min(px(im, x, y)) > 120)
            bits.append('1' if n >= 8 else '0')
    if '1' not in bits:
        return None
    return hashlib.sha256(('subtitle' + ''.join(bits)).encode()).hexdigest()[:16]


def subtitle_hash(im):
    # The cards' subtitles as one hash of their sorted keys; no name.
    keys = sorted(k for k in (sub_key_of(im, b) for b in bands(im)) if k is not None)
    return hashlib.sha256(('|'.join(keys)).encode()).hexdigest()[:16]


def setup_screen(im):
    # GAME SETUP: a dark arrow glyph at (31,31) inside a white back button,
    # a light grey top-left corner, and the pink header. PICK YOUR TRIBE
    # has all three, so it is excluded by its tribe grid.
    c, h = mean(im, (20, 20, 42, 42)), px(im, 300, 30)
    return (max(px(im, 31, 31)) < 60 and min(c) > 200 and max(c) - min(c) < 15
            and h[0] > 200 and h[0] > h[1] + 100 and not tribe_screen(im))


def tribe_screen(im):
    # The twelve regular-tribe circles, each filled with (0,153,255) on at
    # least 8 % of its 76 px box, under an undimmed pink header.
    return px(im, 300, 30)[0] > 200 and all(
        share(im, (x - 38, y - 38, x + 38, y + 38), (0, 153, 255)) >= 0.08
        for x in TRIBE_X for y in TRIBE_Y)


def pick_ready(im):
    # The tribe's panel with PICK lit, (0,122,204), on a black panel body.
    return (share(im, (749, 573, 789, 585), (0, 122, 204)) >= 0.4
            and max(mean(im, (600, 560, 700, 595))) < 20 and not on_multiplayer(im))


# GAME SETUP's page at its top, found by scanning, not at a fixed y
# (2026-11-06). Until then setup_top() read the band
# (515,85)-(765,88) for the Game Name box's white top edge; the page moved
# down about 16 px, so on the page at its top that band was header pink
# (mean minimum 82) and runs stopped timeout_setup_top.
#
# Measured 2026-11-06: the header is a fade over the page that
# ends, five rows below it identical, at y 83 at x 300 on the page at its
# top, one wheel notch down, and Might's end, and y 85 on Glory's end; it
# does not scroll. The Game Name box, near-white (every channel over 235)
# across x 515 to 765, spans y 96 to 129 at the top and y 78 to 112 one
# notch (about 17 px) down; at the end it is gone. So box top minus fade end
# is 13 at the top and -5 a notch down. SETUP_GAP is 13 +- 4.
SETUP_GAP = (9, 17)
SETUP_HEADER_X = (300, 1000)
SETUP_BOX_X = (515, 766)


def setup_header_bottom(im):
    # The first y from 40 down at which the header's fade has ended: the
    # pixel equals the five below it. The smaller of two columns, so a star
    # behind one column cannot push it down. None if neither column settles
    # above y 200.
    ys = []
    for x in SETUP_HEADER_X:
        for y in range(40, 200):
            p = px(im, x, y)
            if all(px(im, x, y + k) == p for k in range(1, 6)):
                ys.append(y)
                break
    return min(ys) if ys else None


def setup_box_top(im):
    # The first y from 40 down where the Game Name box's white spans x 515 to
    # 765 (every 5th pixel over 235 on every channel); None if none above 200.
    for y in range(40, 200):
        if all(min(px(im, x, y)) > 235 for x in range(SETUP_BOX_X[0], SETUP_BOX_X[1], 5)):
            return y
    return None


def setup_top(im):
    # The page at its top: the Game Name box sits directly under the
    # header, its top edge SETUP_GAP below the header's fade.
    if not setup_screen(im):
        return False
    h, b = setup_header_bottom(im), setup_box_top(im)
    return h is not None and b is not None and SETUP_GAP[0] <= b - h <= SETUP_GAP[1]


def setup_end(im):
    # CONTINUE shows only on the page at the end. Measured
    # 2026-11-06: its blue spans y 607 to 645, x 571 to 708,
    # centre (640,626), on both modes' end pages; this box, its middle band,
    # reads mean (29,165,255) there and (38,105,115) on the page at the top.
    # Until then the box was (600,632)-(680,646) around (640,639).
    m = mean(im, (600, 619, 680, 633))
    return setup_screen(im) and m[2] > 230 and m[0] < 60 and 140 < m[1] < 190


def pill(im, y):
    # The selected option's white pill: the widest run of near-white pixels
    # 15 px above the row's text, at least 40 px wide.
    best, s = None, None
    for x in range(0, 1281):
        w = x < 1280 and min(px(im, x, y - 15)) > 235
        if w and s is None:
            s = x
        if not w and s is not None:
            if x - s >= 40 and (best is None or x - s > best[1] - best[0]):
                best = (s, x)
            s = None
    return best


def centred(p):
    return p is not None and abs((p[0] + p[1]) / 2 - 640) <= 6


def pill_sig(im, p, y):
    bits = ''.join('1' if max(px(im, x, yy)) < 120 else '0'
                   for yy in range(y - 10, y + 11, 2) for x in range(p[0] + 6, p[1] - 6, 2))
    return hashlib.sha256(bits.encode()).hexdigest()[:16]


def ui_blue(im, box):
    # The game's button blue, (0,153,255), on at least 40 % of the box.
    n = sum(1 for x in range(box[0], box[2]) for y in range(box[1], box[3])
            if all(abs(a - b) <= 8 for a, b in zip(px(im, x, y), (0, 153, 255))))
    return n >= 0.4 * (box[2] - box[0]) * (box[3] - box[1])


def game_settings_screen(im):
    # In-game Settings: the white back button over the
    # dimmed game view and the grey rule above EXIT TO MENU at y 540.
    b = mean(im, (20, 20, 42, 42))
    return (min(px(im, 31, 31)) > 240 and 180 <= b[0] <= 205 and max(b) - min(b) < 8
            and share(im, (540, 538, 740, 543), (166, 166, 166), 12) >= 0.15
            and not settings_screen(im) and not on_multiplayer(im))


def exit_ready(im):
    # EXIT TO MENU's (8,131,214) on at least 40 % of its box.
    return game_settings_screen(im) and share(im, (600, 568, 680, 590), (8, 131, 214)) >= 0.4


def finish_green(im):
    # capture: the replay's bottom-right control is Finish Game, a green check.
    m = mean(im, (750, 650, 780, 680))
    return m[1] > 170 and m[1] > m[0] + 60 and m[1] > m[2] + 40 and not on_multiplayer(im)


def exit_blue(im):
    # capture: the same control while the replay still plays, Exit, a blue X.
    m = mean(im, (750, 650, 780, 680))
    return m[2] > 220 and m[2] > m[1] + 30 and m[0] < 160 and not on_multiplayer(im)


def victory(im):
    # capture: blue at both points, under a near-black header.
    return (blue(px(im, 620, 405)) and blue(px(im, 660, 420)) and not on_multiplayer(im)
            and max(mean(im, (0, 0, 1280, 60))) < 40)


# The end screen of a game the other player resigned, which a replay of it
# ends on. Measured 2026-11-07 on
# frames 7 to 23 s after Finish Game in the replay of a resigned test
# game, and on the frame a run left when it stopped
# no_victory_screen: a winner line, "No opponents left", two player rows,
# and one DONE button, under the same near-black header as the victory
# screen (top band mean (9,8,9)). Its DONE sits 23 px lower than the
# victory screen's: blue across x 594 to 685 and y 418 to 455 (at x 600 and
# x 680), centre (640,436). On the victory screen (5 earlier won-game
# frames) DONE spans y 395 to 431, centre (640,413). So
# victory()'s point (620,405) lies above this DONE, reads (22,41,50), and
# victory() is false here; (660,420) is inside it. The two points below
# sit on the button's plain blue, off its label, at x 600 and x 680: both
# inside this DONE, and (680,446) below the victory screen's, so the two
# tests never hold on one frame.
RESIGNED_PTS = ((600, 428), (680, 446))
VICTORY_DONE = (640, 413)
RESIGNED_DONE = (640, 436)


def resigned_over(im):
    # capture: the resigned game's end screen, by its DONE at both points,
    # under a near-black header.
    return (all(blue(px(im, x, y)) for x, y in RESIGNED_PTS) and not on_multiplayer(im)
            and max(mean(im, (0, 0, 1280, 60))) < 40)


def game_over(im):
    # capture: the screen a replay ends on after Finish Game, the victory
    # screen of a game played out or the end screen of a resigned one.
    return victory(im) or resigned_over(im)


def done_point(im):
    # capture: where DONE is on the end screen this frame shows, or None
    # when it shows neither.
    if victory(im):
        return VICTORY_DONE
    if resigned_over(im):
        return RESIGNED_DONE
    return None


# An Ongoing game the other player resigned. Measured
# 2026-11-07 on frames of a resigned test
# game, a 2-player game, and of live games beside it. The seat's client
# keeps such a game on Ongoing as the seat's turn, and the card itself is
# unchanged: its portrait reads like every other card's. The mark is on the
# card's panel, where the resigned player's portrait turns into a light
# grey robot head with red eyes. In the 40 px box (650,382)-(690,422), 196
# to 236 px below the panel's top edge (panel_top() 186), the robot frame
# read 49 % near-grey pixels (channels within 12 of each other, red 100 to
# 230) and 12 red ones (red over 180, green and blue under 80); the live
# opponent's portrait in the same box read 0 % and 0. The robot spans
# (655,384)-(685,429). The client showed the mark 1 to 2 hours after the
# resign on the other side: a client restart before then showed the plain
# portrait (2026-11-07). Since the slot
# moves with the player count, the box is slid across the portrait row
# (x 480 to 800, 4 px steps). Of 42 frames checked, resigned_panel() was
# true on the marked panel only; no other panel read over 11 % grey or any
# red pixel at any position, the same game's panel before the mark showed included. Only
# 2-player games were measured.
RESIGN_ROW = (196, 236)
RESIGN_GREY = 0.3
RESIGN_RED = 5


def _robot(im, box):
    # The near-grey share and the red pixel count in box.
    pxs = list(im.crop(box).getdata())
    grey = sum(1 for p in pxs if max(p) - min(p) < 12 and 100 < p[0] < 230)
    red = sum(1 for p in pxs if p[0] > 180 and p[1] < 80 and p[2] < 80)
    return grey / len(pxs), red


def resigned_panel(im):
    # An Ongoing card's panel whose other player resigned: a grey robot
    # portrait in the panel's portrait row.
    top = panel_top(im)
    if top is None or not on_multiplayer(im):
        return False
    for x0 in range(480, 761, 4):
        grey, red = _robot(im, (x0, top + RESIGN_ROW[0], x0 + 40, top + RESIGN_ROW[1]))
        if grey >= RESIGN_GREY and red >= RESIGN_RED:
            return True
    return False


# The dialog that can open over a resigned game's live view.
# Measured 2026-11-07: on the first OPEN after the
# resign showed, the game view opened under a modal dialog, "You are the last
# standing human player, you win!", with one OK button, and End Turn (not
# Finish Game) behind it. The dialog spans (462,259)-(816,459) on near-black;
# OK is blue across x 613 to 666 and y 410 to 446, centre (640,428). The next
# OPEN of the same game, after a client restart, showed no dialog and Finish
# Game at once. The test: OK's blue at (622,418) and (658,440), and dark at
# (480,300) and (800,300), the dialog's body, and at (600,428), left of OK,
# where the resigned game-over screen's DONE is blue. True on that frame only
# of the 42 checked, the game-over and victory screens among them.
LAST_HUMAN_OK = (640, 428)


def last_human(im):
    return (blue(px(im, 622, 418)) and blue(px(im, 658, 440)) and dark(px(im, 480, 300))
            and dark(px(im, 800, 300)) and dark(px(im, 600, 428)) and not on_multiplayer(im))


def screen_name(im):
    # The screen a frame shows, by the tests above; 'unknown' otherwise.
    if flat(im):
        return 'flat'
    if main_menu(im):
        return 'main_menu'
    if mp_list(im):
        return 'multiplayer_' + selected_tab(im)
    if on_multiplayer(im):
        return 'multiplayer_panel' if panel_top(im) is not None else 'multiplayer_other'
    for name, test in (('settings', settings_screen), ('tribe', tribe_screen),
                       ('game_setup', setup_screen), ('game_settings', game_settings_screen)):
        if test(im):
            return name
    return 'unknown'


# ------------------------------------------------------------ waits

def poll_for(test, timeout):
    # The first frame after a click often still shows the old screen, so a
    # test must hold on two frames in a row; None on timeout.
    end = time.time() + timeout
    hit = False
    while True:
        im = grab()
        if test(im):
            if hit:
                return im
            hit = True
        else:
            hit = False
        if time.time() > end:
            return None
        time.sleep(1)


def wait_for(test, what, timeout=None):
    im = poll_for(test, max(timeout or A['wait'], A['wait']))
    if im is None:
        raise Stop('timeout_' + what)
    return im


def wait_frame(timeout):
    # Bounded wait for a frame that is not flat, before any click.
    end = time.time() + timeout
    while True:
        im = grab()
        if not flat(im):
            return im
        if time.time() > end:
            raise Stop('frame_flat')
        time.sleep(15)


def budget(need):
    if time.time() + need > DEADLINE:
        raise Budget()


# ------------------------------------------------------------ moves

# recover() knows the invitation panel that opens on its own when an
# invitation arrives, and leaves it by BACK INVITE_BACK (516,497), Escape
# not closing it (2026-11-10). That panel stopped three runs
# `unknown_screen`; see the note above invite_panel().
def recover(clicks=True):
    # To the main menu or the Multiplayer list with no panel. From the
    # Settings screen its back arrow leads to the main menu; from GAME SETUP
    # its back arrow to the list; from PICK YOUR TRIBE to GAME SETUP. Escape
    # is never pressed on the list: there it opens Settings. With
    # clicks=False, after a stop: Escape only, except on an invitation's
    # panel. That panel (invite_panel()) opens on its own when an
    # invitation arrives, and Escape does not close it, so recover() clicks
    # its BACK, INVITE_BACK, with clicks=False too; BACK neither accepts
    # nor declines (2026-11-10; the stops it explains are in the
    # note above invite_panel()).
    known = lambda i: (main_menu(i) or mp_list(i) or invite_panel(i) or settings_screen(i)
                       or setup_screen(i) or tribe_screen(i))
    im = poll_for(known, A['wait']) or grab()
    for n in range(A['escapes'] + 1):
        if main_menu(im) or mp_list(im):
            return im
        if invite_panel(im):
            click(*INVITE_BACK)
            return wait_for(mp_list, 'invite_back')
        if clicks and settings_screen(im):
            click(31, 31)
            return wait_for(main_menu, 'main_menu')
        if clicks and tribe_screen(im):
            click(33, 34)
            im = wait_for(setup_screen, 'tribe_back')
        if clicks and setup_screen(im):
            click(33, 34)
            return wait_for(mp_list, 'setup_back')
        if n == A['escapes']:
            break
        xdo('key', 'Escape')
        time.sleep(A['settle'])
        im = poll_for(known, A['wait']) or grab()
    raise Stop('unknown_screen')


def to_list():
    im = recover()
    if main_menu(im):
        note_badge(im)
        click(*A['menu_xy'])
        im = wait_for(mp_list, 'multiplayer', 30)
    return im


def to_main_menu():
    # recover(), then the list's back arrow until the main menu shows. After
    # an accept that arrow can reopen the accept side's PICK YOUR TRIBE
    # (list_back_tribe()): its own back arrow, TRIBE_BACK,
    # is clicked, then the list's again, at most LIST_BACK_HOPS times. Any
    # other screen after either click stops the run on a timeout, as before
    # that test existed: `timeout_main_menu` after the list's arrow,
    # `timeout_list_back_tribe` after the tribe screen's.
    im = recover()
    hops = 0
    while mp_list(im):
        click(31, 31)
        im = wait_for(lambda i: main_menu(i) or list_back_tribe(i), 'main_menu')
        if main_menu(im):
            break
        hops += 1
        if hops > LIST_BACK_HOPS:
            raise Stop('list_back_hops')
        click(*TRIBE_BACK)
        im = wait_for(lambda i: main_menu(i) or mp_list(i), 'list_back_tribe')
    return im


def go_tab(name):
    # Every frame bands() and key_of() read comes from here, so each is
    # taken with the pointer parked off the list.
    park()
    im = poll_for(mp_list, A['wait'])
    if im is None:
        raise Stop('not_on_multiplayer_list')
    if selected_tab(im) != name:
        click(*((765, 100) if name == 'replays' else (513, 100)))
        wait_for(lambda i: mp_list(i) and selected_tab(i) == name, 'tab_' + name)
        park()
        im = wait_for(lambda i: mp_list(i) and selected_tab(i) == name, 'tab_' + name)
    return im


# The most wheel calls of 3 notches one read of a list may take, up in
# to_top() and down in the callers' sweeps, which otherwise go until a call
# moves nothing. 5 until 2026-10-24: by estimate, not
# measured, a list of about 7 cards outgrew 5 pages, leaving a card
# split_bands() counted but no page read whole. Measured 2026-10-24:
# a call moves the list 48 to 52 px, and a 5-card list travels
# 100 px from its top to its end; each card past the 5th adds one pitch,
# about 77 px. So a 20-card list travels about 100 + 15 x 77 = 1260 px, 27
# calls at 48 px; 40 calls at 48 px cover 1920 px, a list of about 28 cards.
# Estimated, not measured past 3 pages: a page costs about 10 to 20 s (the
# wheel, the poll up to scroll_wait, and heal_list()'s check), so 40 pages
# are 7 to 14 minutes of the run's 3300 s budget; the sweeps check the
# budget before each downward wheel.
PAGE_CAP = 40


def to_top(kind, report):
    # Wheel the Multiplayer list up to its top: 3 notches of button 4 at
    # (640,400), parked after each call, until a call leaves the bands and
    # their exact keys unchanged within scroll_wait, at most max_pages
    # calls (PAGE_CAP unless configured; 5 until 2026-10-24). Counts
    # each call that moved the list in report['top_scrolls'] and returns
    # the frame at the top. Measured 2026-10-23: the
    # Ongoing list keeps its scroll position within a client session and
    # opened at its end with 5 cards, the top card under the tab bar; a
    # notch moves it 16 to 18 px, so with 5 cards 7 notches reach the top.
    # After a client restart it opened at its top.
    report.setdefault('top_scrolls', 0)
    park()
    im = poll_for(mp_list, A['wait'])
    if im is None:
        raise Stop('not_on_multiplayer_list')
    for _ in range(A.get('max_pages', PAGE_CAP)):
        bs = bands(im)
        sig = (bs, [key_of(im, b, kind) for b in bs])
        xdo('mousemove', 640, 400, 'click', '--repeat', 3, '--delay', 200, 4)
        park()
        moved = lambda i: (mp_list(i) and
                           (bands(i) != sig[0] or [key_of(i, b, kind) for b in bands(i)] != sig[1]))
        nxt = poll_for(moved, A.get('scroll_wait', 10))
        if nxt is None:
            break
        report['top_scrolls'] += 1
        im = nxt
    # The last frame a moving list showed can still be mid-scroll; read a
    # settled one.
    return wait_for(mp_list, 'list_top')


def restart_client(report=None):
    # Restart the game client and return the settled main menu's frame.
    # Root only: systemctl restart. Then, bounded by client_wait from the
    # restart, frames every client_poll s until main_menu() shows, then
    # every settle_s s until two consecutive frames are both main_menu()
    # with the same menu_key(). Sends no input. Stops client_no_menu when
    # the menu never showed, client_unsettled when it showed but never
    # held still, both within client_wait.
    #
    # A failed grab() is no frame yet (2026-10-25). A run
    # on 2026-10-24 stopped command_failed_runuser with the menu not
    # yet reached: one grab()'s xwd, the only X command here, exited
    # non-zero while the client was starting, and the run ended though the
    # client went on to the main menu. Three restarts after it did not
    # repeat it, and the run kept no stderr, so why xwd
    # failed is not known. Now the loop counts the failure in
    # r['grab_failures'], keeps the last failed_code() in
    # r['grab_failure_last'], breaks the settled pair, and polls again
    # until client_wait. If no grab() at all succeeded by then, the stop is
    # that failed_code(), not client_no_menu, so the tool still shows.
    r = report if report is not None else {}
    r.update({'menu_reached': False, 'settled': False, 'seconds': None,
              'grab_failures': 0, 'grab_failure_last': None})
    if os.geteuid() != 0:
        raise Stop('restart_needs_root')
    t0 = time.time()
    end = t0 + A['client_wait']
    subprocess.run(['systemctl', 'restart', A['unit']], capture_output=True, check=True)
    # The old client's last frame can outlive the restart by the screen's
    # lag; nothing before one settle_s counts.
    time.sleep(A['settle_s'])
    prev = None
    frames = 0
    while True:
        try:
            im = grab()
        except subprocess.CalledProcessError as e:
            r['grab_failures'] += 1
            r['grab_failure_last'] = failed_code(e)
            im = None
        if im is not None:
            frames += 1
        cur = menu_key(im) if im is not None and main_menu(im) else None
        if cur is not None:
            r['menu_reached'] = True
            if cur == prev:
                r['settled'] = True
                r['seconds'] = int(round(time.time() - t0))
                return im
        prev = cur
        if time.time() > end:
            r['seconds'] = int(round(time.time() - t0))
            if frames == 0 and r['grab_failure_last']:
                raise Stop(r['grab_failure_last'])
            raise Stop('client_unsettled' if r['menu_reached'] else 'client_no_menu')
        time.sleep(A['settle_s'] if cur is not None else A['client_poll'])


# ------------------------------------------------------------ stale view
# Found 2026-10-22. The client's lobby panels and Ongoing list do not
# update live with what other players do. After the seat invited a player,
# its own lobby panel still showed one player and "You need at least two
# players", unchanged when reopened and after a held pull, while the
# invitation had reached the other player. The client's log recorded no
# event for the invitation. A non-empty list can therefore be
# stale, which list_check() cannot see. The one reload known to show changes
# made elsewhere is a client restart (2026-10-20); the pull is not
# proven to. reload_view() is that restart, for callers that must read
# others' changes, such as a lobby the invited player has accepted.


def reload_view(report):
    # restart_client(), then MULTIPLAYER, ending on the list with no panel.
    # Root only, like restart_client(); counts report['client_restarts'].
    report.setdefault('client_restarts', 0)
    budget(A['client_wait'] + 120)
    note_badge(restart_client())
    report['client_restarts'] += 1
    click(*A['menu_xy'])
    return wait_for(mp_list, 'multiplayer', 30)


# ------------------------------------------------------------ stale list
# Found 2026-10-22. A run on 2026-10-20 read a stale lobby
# list; after a Replays-Ongoing tab switch both tabs stayed empty for more
# than 30 s while the main menu still showed games, until a client restart
# fixed it.
#
# What counts as stale: the Multiplayer list shows no card on the tab to be
# read and none on the other tab either, whatever the main menu's
# Multiplayer badge showed. The badge is read by menu_badge(), box
# BADGE_BOX (713,374)-(733,394), true at BADGE_MIN (15 %) of its pixels
# badge red (red over 200, green under 120, blue under 90); measured
# 2026-10-25 with the badge present, and its absence from
# the plain button's pixels, not seen live (see menu_badge()). to_list()
# and reload_view() record it in BADGE on the main-menu frame just before
# MULTIPLAYER is clicked, and the capture and new-game runs print it as
# `badge`. It counts the games on "Your turn" only, so with both tabs
# empty: BADGE True (the badge showed games) is stale, confirmed; BADGE
# None (no reading, as when a run started on the list) keeps the
# empty-list rule and is stale; BADGE False is stale too, because games on
# the other players' turn show no badge. list_check() therefore returns the
# same as before the badge was read; the reading
# is evidence in the run's output, not a branch. The other tab is
# looked at only when this one is empty, so that an Ongoing tab with no game
# running (Replays still lists past games) is not taken for stale. The
# second possible signal, the same card keys after a refresh expected to
# change them, reduces to "still empty": the only refresh heal_list()
# expects to change the list starts from an empty one. A stale list that
# still shows old cards is not caught.


def _cards(tab):
    # The tab's frame once it shows a card on two frames in a row, within
    # list_wait, with the pointer parked; None if it shows none.
    go_tab(tab)
    return poll_for(lambda i: mp_list(i) and selected_tab(i) == tab and len(bands(i)) > 0,
                    A['list_wait'])


def list_check(tab):
    # The tab's frame when the list is live, None when it is stale. Must
    # start on the Multiplayer list with no panel open; ends on tab.
    im = _cards(tab)
    if im is not None:
        return im
    if _cards('replays' if tab == 'ongoing' else 'ongoing') is not None:
        return go_tab(tab)   # this tab is empty, the list is not
    return None


def heal_list(tab, report):
    # Run before every read of the Multiplayer list. If list_check() fails:
    # the held pull (pull_refresh at pull_px, pull_ms), settle_s, and check
    # again; still stale, restart_client() (root: systemctl restart; it
    # returns only after a settled main menu, so nothing is clicked before
    # it), MULTIPLAYER, and check once more; still stale stops the run
    # list_stale. The list is never re-entered as a refresh: re-entering
    # once emptied both tabs (seen 2026-10-20). Counts each recovery in
    # report['list_refreshes'] and report['client_restarts'].
    report.setdefault('list_refreshes', 0)
    report.setdefault('client_restarts', 0)
    im = list_check(tab)
    if im is not None:
        return im
    go_tab(tab)
    pull_refresh(A['pull_px'], A['pull_ms'])
    report['list_refreshes'] += 1
    park()
    time.sleep(A['settle_s'])
    im = list_check(tab)
    if im is not None:
        return im
    reload_view(report)
    im = list_check(tab)
    if im is not None:
        return im
    raise Stop('list_stale')


# ------------------------------------------------------------ accept and resign
# Added 2026-11-10, for accepting an invitation and resigning. Measured on
# frames saved that day, against the earlier frames listed with each test.

# PICK YOUR TRIBE after ACCEPT on an invitation. The screen carries four
# lines of game settings under its title, so the grid sits lower on the
# page's text and higher on the screen than the create side's TRIBE_Y: the
# circles' blue (0,153,255)
# ends at y 261, 371, and 481 (481 to 483 at x 792), against 276, 398 to
# 399, and 521 on the create side (two frames, 2026-10-18 and
# 2026-10-20). Columns are 1 px right of TRIBE_X.
ACCEPT_TRIBE_Y = [224, 333, 443]
ACCEPT_TRIBE_X = [489, 590, 691, 792]


def accept_tribe_screen(im):
    # The accept side's grid: under an undimmed pink header, each of the
    # twelve circles shows its blue in a 60 x 8 px band 26 to 34 px below
    # its centre, and none in the band 42 to 48 px below it, where the
    # create side's lower circles still are. Measured on a
    # frame of this screen: inner share 0.158 at least, lower band 0 at most. On the
    # two create-side tribe frames above: inner 0.060 at least, lower band
    # up to 0.764, so this is false there, while tribe_screen() is true on
    # all three. False on the other 29 frames checked (lists, panels, game
    # views, Settings, the resign confirmation, end screens; 2026-10-18 to
    # 2026-11-10). Measured on a Glory game only: a mode whose settings take
    # another number of lines may move the rows again, and then this test
    # fails and the run stops timeout_accept_tribe_screen.
    return px(im, 300, 30)[0] > 200 and all(
        share(im, (x - 30, y + 26, x + 30, y + 34), (0, 153, 255)) >= 0.08
        and share(im, (x - 30, y + 42, x + 30, y + 48), (0, 153, 255)) <= 0.05
        for x in ACCEPT_TRIBE_X for y in ACCEPT_TRIBE_Y)


# The accept side's PICK YOUR TRIBE, reopened by the Multiplayer list's back
# arrow after an accept (2026-11-10). The list's back arrow
# returns to the screen before the list in the client's own navigation, and
# after ACCEPT, a tribe, and PICK that is this screen, not the main menu: a
# list opened from the main menu with no accept between goes back to the
# main menu. to_main_menu()'s
# click at (31,31) opened it and its wait_for(main_menu) stopped
# `timeout_main_menu` in two runs that accepted invitations; the second
# was run to reproduce the first and its frame kept.
#
# Measured on that frame: accept_tribe_screen() true
# (inner band share 0.158 at least, lower band 0.000), and tribe_screen()
# true as well (create grid 0.149 at least), so recover() takes it for the
# create side's screen, whose back arrow leads to GAME SETUP. The back
# button lit: the box (20,20)-(42,42) reads mean (229,231,234) with the
# pointer on it, against (175,175,175), dimmed, on a frame
# with a tribe panel open, where accept_tribe_screen() is false too.
# main_menu() is false on it (MENU_TR green 88), and so are
# on_multiplayer(), settings_screen(), setup_screen(), and invite_panel().
# Escape does not leave it: a run's stop recovery pressed Escape up to
# three times and then had to restart the client.
#
# Its exit is the back arrow, TRIBE_BACK (33,34), the create side's back
# point and the only control on the screen that opens no tribe panel. The
# tribe was already picked, so it is not known to leave or cancel anything.
# Unconfirmed: no frame shows the arrow clicked on this screen (the
# restart cleared it first), so where it leads is inferred, not seen: back
# to the Multiplayer list by the same back-navigation, whose arrow then
# leads on. LIST_BACK_HOPS bounds the round trips; one per invitation
# accepted in the run is the expectation, also unconfirmed. Of 76 saved
# frames from 2026-10-18 to 2026-11-10, the test is true on that frame and
# on a frame of the same screen as ACCEPT first opens it, which it cannot
# tell apart, and false on the other 74 (lists, panels, both tribe screens
# with a panel open, the create
# side's tribe screen, GAME SETUP, the main menu, game and end views, and
# flat frames). Only to_main_menu() calls it, after the list's back arrow.
TRIBE_BACK = (33, 34)
LIST_BACK_HOPS = 4


def list_back_tribe(im):
    c = mean(im, (20, 20, 42, 42))
    return accept_tribe_screen(im) and min(c) > 200 and max(c) - min(c) < 15


def bot_turn_card(im, band):
    # An Ongoing card on the seat's turn: the mean of the strip x 540 to 740,
    # 7 to 4 px above the band's bottom (under the subtitle, left of the
    # timer), passes blue(). Measured (2026-11-10): every card under
    # "Your Turn" read (49,138..154,225..231), or (13..27,151..155,239)
    # over the scenery, on six frames (5 cards on two of them, "Time Up!"
    # and "6 days" alike); every card under "Their Turn" read (49,26,23),
    # on two frames. Invitations under "Game
    # Invitations" are blue too, so a caller tells them apart by the panel.
    # Unconfirmed: the "Their Turn" cards measured are all lobbies; no frame
    # shows a started game on the other player's turn, so that such a card
    # is dark as well is inferred, not seen.
    m = mean(im, (540, band[1] - 7, 740, band[1] - 4))
    return blue(m)


# The turn 0 dialog's OK. (640,457) on games the seat created; (640,528)
# on a game the seat accepted (dialog
# "Mighty Leader!", header "Replaying… (6 days)"). No frame shows the
# dialog at (640,528), so its colour is taken to be the game's button blue,
# as at (640,457): unconfirmed. Each box is the button's middle, 40 x 12 px.
# Neither box passes ui_blue() on any of the 32 frames checked, the game
# views without the dialog among them (2026-10-21).
TURN0_OK = ((640, 457), (640, 528))


def turn0_ok(im):
    # The OK point the turn 0 dialog shows, or None.
    for x, y in TURN0_OK:
        if ui_blue(im, (x - 20, y - 6, x + 20, y + 6)):
            return (x, y)
    return None


def resign_ready(im):
    # In-game Settings with RESIGN lit at (1225,30): (204,41,0), within 12,
    # on at least 40 % of (1205,22)-(1245,38). Read 0.64 on two
    # frames of in-game Settings, 0.00
    # on the resign confirmation (RESIGN dimmed) and on the other 30 frames.
    return game_settings_screen(im) and share(im, (1205, 22, 1245, 38), (204, 41, 0), 12) >= 0.4


def resign_confirm(im):
    # The "Resign — Are you sure you want to leave this game permanently?"
    # dialog over in-game Settings:
    # blue OK (0,153,255) across x 751 to 804 and y 384 to 423, and the
    # white BACK left of it at about (513,404). OK's box (757,392)-(797,416)
    # read 0.79 within 8 of the blue, BACK's (495,396)-(531,412) 0.74 white
    # (every channel over 235). On the other 31 frames the OK box read 0.18
    # at most (tribe screens) and the BACK box 0.04 at most.
    n = sum(1 for x in range(495, 531) for y in range(396, 412) if min(px(im, x, y)) > 235)
    return (share(im, (757, 392, 797, 416), (0, 153, 255)) >= 0.4
            and n >= 0.4 * 36 * 16 and not on_multiplayer(im))
