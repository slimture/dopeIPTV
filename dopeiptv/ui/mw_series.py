"""Series recordings in the window: "Record series…" on a programme, the
rules looked through the guide when it loads and with the guide's own
20-minute check, and what they find scheduled with the recorder - or, for a
rule that reminds ("Remind me" in the same dialog), set as reminders, which
fire as every reminder does (mw_reminders).

The matching, the padding and the no-duplicates rules are core/series_rules.py;
this is the glue: the dialog, the work on the pool, the jobs and reminders
added on the window's thread.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QRadioButton,
)

from ..core import series_rules
from ..core.log import log
from ..core.privacy import for_log
from ..core.workers import run_async
from ..i18n import tr
from .theme import P


def _find(client, guide, rules: list[dict], keep, now: float,
          shift: float, source: str = "") -> list:
    """(pool thread) The airings the rules ask for, each with its address.
    The whole line-up only when a rule is for any channel; a one-channel
    rule finds its channel as the rule keeps it. A client whose links are
    slow to get and short-lived (a portal's create_link lasts seconds) is
    not asked for any here: such a recording asks for its address when it
    starts (RecordingManager.LATER)."""
    chans = series_rules.rule_channels(rules)
    if keep is not None:
        chans = [c for c in client.live_streams(None) or [] if keep(c)] + chans
    found = series_rules.find_airings(rules, chans, guide.programmes_in, now,
                                      shift=shift, source=source)
    if links_later(client):
        return found
    # A reminder needs no address.
    remind = series_rules.of_action(rules, series_rules.REMIND)
    for a in found:
        if a.rule in remind:
            continue
        try:
            a.url = client.live_url(a.channel.get("stream_id"), "ts") or ""
        except Exception as e:
            log.info("[series] no address for %s: %s",
                     for_log(a.channel.get("name")), e)
            a.url = ""
    return found


def links_later(client) -> bool:
    """Whether a recording from *client* asks for its address when it
    starts, not when it is scheduled: a portal's links (and a box's, whose
    list may not be in yet) are slow to get, and a portal's last seconds."""
    return bool(getattr(client, "slow_links", False))


class _SeriesMixin:
    """MainWindow mixin: series recording rules."""

    def _record_series(self, ch: dict | None, title: str = "",
                       word: bool = False, parent=None,
                       remind: bool = False) -> dict | None:
        """Ask how to record a series - the exact title or a word in it, on
        *ch* only or on any channel, each airing recorded or (*remind*, the
        dialog's other choice) reminded of - keep the rule and schedule what
        the guide has of it. The rule, or None when nothing was added."""
        d = QDialog(parent or self)
        d.setMinimumWidth(420)
        f = QFormLayout(d)
        f.setSpacing(10)
        text = QLineEdit(" ".join((title or "").split()))
        kind = QComboBox()
        kind.addItem(tr("series_match_title"), series_rules.EXACT)
        kind.addItem(tr("series_match_word"), series_rules.WORD)
        kind.setCurrentIndex(1 if word else 0)
        where = QComboBox()
        if ch is not None and series_rules.stream_key(ch):
            where.addItem(tr("series_where_this",
                             channel=self._series_channel_name(ch)),
                          series_rules.THIS)
        where.addItem(tr("series_where_any"), series_rules.ANY)
        # Record, or remind: the same rule, another thing done with each
        # airing it finds.
        record_btn = QRadioButton(tr("series_do_record"))
        remind_btn = QRadioButton(tr("series_do_remind"))
        group = QButtonGroup(d)
        group.addButton(record_btn)
        group.addButton(remind_btn)
        (remind_btn if remind else record_btn).setChecked(True)
        choice = QHBoxLayout()
        choice.setSpacing(16)
        choice.addWidget(record_btn)
        choice.addWidget(remind_btn)
        choice.addStretch(1)
        f.addRow(tr("series_do"), choice)
        f.addRow(tr("series_text"), text)
        f.addRow(tr("series_match"), kind)
        f.addRow(tr("series_where"), where)
        hint = QLabel()
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        f.addRow(hint)

        def say(_on: bool = False) -> None:
            on = remind_btn.isChecked()
            d.setWindowTitle(tr("series_remind_title" if on else "series_title"))
            hint.setText(tr("series_remind_hint" if on else "series_hint"))

        remind_btn.toggled.connect(say)
        say()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if not d.exec():
            return None
        what = series_rules.REMIND if remind_btn.isChecked() else series_rules.RECORD
        rule = series_rules.make_rule(
            text.text(), kind.currentData(),
            ch if where.currentData() == series_rules.THIS else None, what)
        if rule is None:
            return None
        kept = self.series_rules.add(rule)
        if kept is None:
            return None
        log.info("[series] rule: %s %s %r on %s", series_rules.action(kept),
                 kept["kind"], for_log(kept["text"]),
                 series_rules.rule_key(kept) or "any channel")
        self._show_toast(tr("series_remind_added" if what == series_rules.REMIND
                            else "series_added", title=kept["text"]), 4000)
        self._check_series_rules()
        return kept

    def _series_channel_name(self, ch: dict) -> str:
        """A channel's name as the live list shows it (a rename included),
        whatever list is on screen."""
        base = str(ch.get("name") or ch.get("title") or "?")
        key = self._item_key(ch)
        if key is None:
            return base
        try:
            return self.channel_ov.display_name("live", key, base)
        except Exception:
            return base

    def _series_rule_label(self, rule: dict) -> str:
        """A rule as the Settings list says it."""
        word = rule.get("kind") == series_rules.WORD
        text = rule.get("text") or ""
        if rule.get("scope") == series_rules.THIS:
            name = self._series_channel_name(rule.get("channel") or {})
            label = (tr("series_row_word_on", word=text, channel=name) if word
                     else tr("series_row_title_on", title=text, channel=name))
        else:
            label = (tr("series_row_word_any", word=text) if word
                     else tr("series_row_title_any", title=text))
        if series_rules.action(rule) == series_rules.REMIND:
            return tr("series_row_remind", rule=label)
        return label

    def _delete_series_rule(self, rule_id: str) -> None:
        """Drop a rule, and the recordings it scheduled that have not
        started: they were its, nobody asked for them one by one."""
        if not self.series_rules.remove(rule_id):
            return
        for j in list(self.rec.jobs):
            if j.get("rule") == rule_id and j.get("status") == "scheduled":
                self.rec.cancel(j["id"])
        # A reminder rule's reminders still to come go with it.
        reminders = getattr(self, "reminders", None)
        if reminders is not None:
            reminders.remove_rule(rule_id, time.time())
        log.info("[series] rule %s deleted", rule_id)

    # -- the check --------------------------------------------------------

    def _check_series_rules(self) -> None:
        """Look through the guide for what the rules ask for. Runs when the
        guide is in, with the guide's own check and after a rule is added;
        one look at a time (another asked meanwhile runs after it)."""
        store = getattr(self, "series_rules", None)
        guide = getattr(self, "xmltv", None)
        if store is None or guide is None or not store.rules():
            return
        if not guide.is_loaded():
            # Its load runs this again when the guide is in.
            ensure = getattr(self, "_ensure_xmltv_loaded", None)
            if ensure is not None:
                ensure()
            return
        if getattr(self, "_series_busy", False):
            self._series_again = True
            return
        rules = store.rules()
        keep = (self._catalog_filter("live")
                if any(r.get("scope") == series_rules.ANY for r in rules)
                else None)
        self._series_busy = True
        self._series_again = False
        self._series_ctx = (self.client, guide, store)
        # The guide offset (Settings, and a source's own with Nerd), as the
        # detail panel's times have it: a programme airs that much later.
        shift = self._epg_delay_minutes() * 60
        run_async(self.pool, _find, self._series_found, self._series_failed,
                  self.client, guide, rules, keep, time.time(), shift,
                  str(self._rec_source().get("source") or ""))

    def _series_failed(self, err) -> None:
        log.info("[series] the look through the guide failed: %s", err)
        self._series_next()

    def _series_found(self, found) -> None:
        try:
            client, guide, store = self._series_ctx
            if (client is self.client and guide is self.xmltv
                    and store is self.series_rules):
                self._series_schedule(found or [])
            else:
                self._series_again = True      # another source meanwhile
        except Exception as e:
            log.warning("[series] scheduling failed: %s", e)
        self._series_next()

    def _series_next(self) -> None:
        self._series_busy = False
        if getattr(self, "_series_again", False):
            self._series_again = False
            QTimer.singleShot(0, self._check_series_rules)

    def _series_remind(self, found: list, now: float) -> int:
        """Set a reminder for each airing a reminder rule found that has
        none yet - at the programme's start, no padding; one the guide moved
        follows it. The number set."""
        store, reminders = self.series_rules, getattr(self, "reminders", None)
        if reminders is None:
            return 0
        rules = store.rules()
        remind = series_rules.of_action(rules, series_rules.REMIND)
        found = [a for a in found if a.rule in remind]
        moved = []
        for r, a in series_rules.remind_retimed(found, reminders.all(), now):
            reminders.move(r.get("stream_id"), r.get("start"), a.start, a.stop)
            moved.append(series_rules.Plan(a, a.start, a.stop))
        taken = series_rules.taken_for(store.taken(), remind)
        new = series_rules.remind_plan(found, reminders.all(), taken, now)
        for a in new:
            reminders.add(a.channel, a.title, a.start, rule=a.rule, stop=a.stop)
        if moved or new:
            store.remember(moved + [series_rules.Plan(a, a.start, a.stop) for a in new], now)
        if new:
            log.info("[series] %d reminder(s) set", len(new))
            self._flash_status(tr("series_reminded_n", n=len(new)))
        return len(new)

    def _series_schedule(self, found: list) -> int:
        """Schedule what the rules found and is not recorded yet, and set
        the reminders the reminder rules ask for; the number of recordings
        added."""
        store = self.series_rules
        now = time.time()
        try:
            self._series_remind(found, now)
        except Exception as e:
            log.warning("[series] reminders not set: %s", e)
        rules = store.rules()
        ids = series_rules.of_action(rules, series_rules.RECORD)
        later = links_later(self.client)
        # A rule deleted meanwhile asks for nothing; a channel without an
        # address cannot be recorded (one whose address is asked for when
        # the recording starts has none yet).
        found = [a for a in found if a.rule in ids and (a.url or later)]
        before, after = series_rules.padding(self.settings)
        moved = []
        for job, a in series_rules.retimed(found, self.rec.jobs, now):
            start, stop = series_rules.padded(a.start, a.stop, before, after, now)
            log.info("[series] %s moved in the guide - recording it at its new time",
                     for_log(a.title))
            self.rec.update_job_times(job["id"], start, stop,
                                      extra={"prog_start": a.start,
                                             "prog_stop": a.stop})
            moved.append(series_rules.Plan(a, start, stop))
        # A keyword on any channel records so many at one time, and no
        # rule more at one time than the account has connections.
        wide = {r["id"] for r in rules if r.get("scope") == series_rules.ANY
                and r.get("kind") == series_rules.WORD}
        maxc = getattr(self.client, "max_connections", None)
        left_out: list = []
        plans = series_rules.plan(
            found, self.rec.jobs, series_rules.taken_for(store.taken(), ids, legacy=True),
            now, before, after, wide=wide, connections=maxc, left_out=left_out)
        if left_out:
            at_once = sum(1 for _a, why in left_out if why == series_rules.AT_ONCE)
            log.info("[series] %d airing(s) not scheduled: %d past %d at one time "
                     "for a keyword rule, %d past the account's %s connection(s)",
                     len(left_out), at_once, series_rules.MAX_AT_ONCE,
                     len(left_out) - at_once, maxc or "?")
        if not plans:
            store.remember(moved, now)
            return 0
        if not self.rec.recorder()[1]:
            log.info("[series] %d airing(s) to record, but neither ffmpeg nor "
                     "mpv is there", len(plans))
            store.remember(moved, now)
            return 0
        if self.rec.total_cap_exceeded():
            log.info("[series] %d airing(s) to record, but the recordings "
                     "folder is at its size limit", len(plans))
            store.remember(moved, now)
            return 0
        for p in plans:
            a = p.airing
            extra = {"rule": a.rule, "stream_key": a.key,
                     "prog_start": a.start, "prog_stop": a.stop}
            extra.update(self._rec_extra(a.channel, later=later))
            self.rec.add_job(
                "" if later else a.url, a.title, p.start, p.stop,
                icon=a.channel.get("stream_icon"),
                channel=self._series_channel_name(a.channel),
                extra=extra)
        store.remember(moved + plans, now)
        log.info("[series] %d recording(s) scheduled", len(plans))
        self._flash_status(tr("series_scheduled_n", n=len(plans)))
        return len(plans)
