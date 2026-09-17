#!/usr/bin/env python3
"""Build a self-contained trivia game from a WhatsApp chat export.

Usage:
    python3 build.py data/_chat.txt [--out dist/index.html] [--custom data/custom_questions.json]
                     [--rename "toghrul=Toğrul" --rename "Nərgiz ❤︎=Nərgiz"] [--seed 7]

The export is parsed locally, question pools are generated, and everything is
injected into template/index.html. Nothing leaves your machine.
"""
import argparse
import base64
import collections
import os
import datetime as dt
import json
import random
import re
import statistics
from pathlib import Path

LINE_RE = re.compile(
    r"^‎?\[(\d{2})\.(\d{2})\.(\d{2}), (\d{1,2}):(\d{2}):(\d{2})[  ](AM|PM)\] ([^:]+):(?: (.*))?$"
)
INVISIBLE_RE = re.compile(r"[‎‏‪-‮⁦-⁩]")
EDITED_RE = re.compile(r"\s*<This message was edited>\s*")
WORD_RE = re.compile(r"[a-zA-ZəöüğışçƏÖÜĞİŞÇ]+")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U0001F900-\U0001F9FF☀-➿❤]")
URL_RE = re.compile(r"https?://|www\.")

# Azerbaijani letters typed on an English keyboard: "gunaydin" == "günaydın", "chox" == "çox"
_FOLD = str.maketrans({"ə": "e", "ı": "i", "ö": "o", "ü": "u", "ğ": "g", "ş": "s", "ç": "c", "\u0307": ""})
_DIGRAPHS = [("sh", "s"), ("ch", "c"), ("gh", "g")]


def norm(word: str) -> str:
    """Spelling-insensitive key for a word, so both people's spellings count together."""
    w = word.lower().replace("İ", "i").translate(_FOLD)
    for a, b in _DIGRAPHS:
        w = w.replace(a, b)
    return w


def norm_text(text: str) -> str:
    return norm(text)

MEDIA_MARKERS = {
    "audio": "audio omitted",
    "sticker": "sticker omitted",
    "image": "image omitted",
    "video": "video omitted",
    "gif": "GIF omitted",
    "document": "document omitted",
    "contact": "Contact card omitted",
}

AZ_MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avqust",
             "sentyabr", "oktyabr", "noyabr", "dekabr"]
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]
AZ_DAYS = ["Bazar ertəsi", "Çərşənbə axşamı", "Çərşənbə", "Cümə axşamı", "Cümə", "Şənbə", "Bazar"]
EN_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------

def parse(path: Path):
    msgs = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        m = LINE_RE.match(line)
        if m:
            d, mo, y, h, mi, s, ap, sender, text = m.groups()
            text = text or ""
            hour = int(h) % 12 + (12 if ap == "PM" else 0)
            ts = dt.datetime(2000 + int(y), int(mo), int(d), hour, int(mi), int(s))
            sender = INVISIBLE_RE.sub("", sender).strip()
            msgs.append({"ts": ts, "sender": sender, "raw": text})
        elif msgs:
            msgs[-1]["raw"] += "\n" + line
    for m in msgs:
        raw = m["raw"]
        m["edited"] = "<This message was edited>" in raw
        text = EDITED_RE.sub(" ", INVISIBLE_RE.sub("", raw)).strip()
        m["text"] = text
        kind = "text"
        for k, marker in MEDIA_MARKERS.items():
            if marker in text:
                kind = k
                break
        if "Missed voice call" in text:
            kind = "missed_call"
        elif "Missed video call" in text:
            kind = "missed_video"
        elif text.startswith("Voice call"):
            kind = "voice_call"
        elif text.startswith("Video call"):
            kind = "video_call"
        elif text in ("This message was deleted", "You deleted this message"):
            kind = "deleted"
        elif URL_RE.search(text):
            kind = "link"
        elif "location:" in text.lower():
            kind = "location"
        m["kind"] = kind
    return msgs


def call_minutes(text):
    """'Voice call. 1 hr 12 min' -> 72; returns None when there is no duration."""
    total = 0
    found = False
    for n, unit in re.findall(r"(\d+)\s*(hr|min|sec)", text):
        found = True
        n = int(n)
        total += n * 60 if unit == "hr" else n if unit == "min" else n / 60
    return total if found else None


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def az_date(d):
    return f"{d.day} {AZ_MONTHS[d.month - 1]} {d.year}"


def en_date(d):
    return f"{d.day} {EN_MONTHS[d.month - 1]} {d.year}"


def bi(az, en):
    return {"az": az, "en": en}


def date_opt(d):
    return bi(az_date(d), en_date(d))


def fmt_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return bi(f"{seconds} saniyə", f"{seconds} seconds")
    if seconds < 3600:
        return bi(f"{seconds // 60} dəqiqə", f"{seconds // 60} minutes")
    h, rem = divmod(seconds, 3600)
    if h < 24:
        return bi(f"{h} saat {rem // 60} dəq", f"{h} h {rem // 60} min")
    d, rem = divmod(seconds, 86400)
    return bi(f"{d} gün {rem // 3600} saat", f"{d} days {rem // 3600} h")


def number_options(rng, answer, spread=(0.45, 0.7, 1.4, 1.9, 2.6)):
    """Return 4 numeric options including the answer, sorted ascending."""
    answer = int(answer)
    mag = 10 ** max(0, len(str(answer)) - 2)
    opts = {answer}
    factors = list(spread)
    rng.shuffle(factors)
    for f in factors:
        v = int(round(answer * f / mag) * mag) if mag > 1 else int(round(answer * f))
        if v > 0 and v != answer:
            opts.add(v)
        if len(opts) == 4:
            break
    while len(opts) < 4:
        opts.add(answer + len(opts) * max(1, mag))
    return sorted(opts)


def mc(kind, q, options, answer_index, reveal, about="both", context=None, tag=None):
    item = {"kind": kind, "q": q, "options": options, "answer": answer_index, "reveal": reveal, "about": about}
    if context:
        item["context"] = context
    if tag:
        item["tag"] = tag
    return item


def num_question(rng, kind, q, answer, reveal, about="both", suffix=None):
    opts = number_options(rng, answer)
    labels = []
    for o in opts:
        s = f"{o:,}".replace(",", " ")
        labels.append(bi(f"{s} {suffix['az']}" if suffix else s, f"{s} {suffix['en']}" if suffix else s))
    return mc(kind, q, labels, opts.index(int(answer)), reveal, about)


def who_question(kind, q, names, counts, reveal_tpl, about="both"):
    """2-option question: which of the two people has the bigger count."""
    a, b = names
    winner = a if counts[a] >= counts[b] else b
    reveal = bi(
        reveal_tpl["az"].format(a=a, b=b, ca=counts[a], cb=counts[b], w=winner),
        reveal_tpl["en"].format(a=a, b=b, ca=counts[a], cb=counts[b], w=winner),
    )
    return mc(kind, q, [bi(a, a), bi(b, b)], 0 if winner == a else 1, reveal, about)


# ----------------------------------------------------------------------------
# Question generators
# ----------------------------------------------------------------------------

def stats_questions(rng, msgs, names, display):
    a, b = names
    A, B = display[a], display[b]
    D = {a: A, b: B}
    qs = []
    texts = [m for m in msgs if m["kind"] == "text"]
    by = lambda pred: {n: sum(1 for m in msgs if m["sender"] == n and pred(m)) for n in names}

    total = len(msgs)
    first, last = msgs[0]["ts"], msgs[-1]["ts"]
    span_days = (last.date() - first.date()).days + 1

    qs.append(num_question(rng, "stats",
        bi("Bu söhbətdə cəmi neçə mesaj var?", "How many messages are in this chat in total?"),
        total,
        bi(f"{total:,} mesaj, {span_days} gün ərzində. Gündə orta hesabla {total // span_days} mesaj.".replace(",", " "),
           f"{total:,} messages over {span_days} days. That's about {total // span_days} a day.")))

    cnt = by(lambda m: True)
    qs.append(who_question("stats", bi("Kim daha çox mesaj yazıb?", "Who has sent more messages?"),
        (A, B), {A: cnt[a], B: cnt[b]},
        bi("{a}: {ca} mesaj, {b}: {cb} mesaj.", "{a}: {ca} messages, {b}: {cb} messages.")))

    audio = by(lambda m: m["kind"] == "audio")
    qs.append(who_question("stats", bi("Kim daha çox səsli mesaj göndərib?", "Who sends more voice notes?"),
        (A, B), {A: audio[a], B: audio[b]},
        bi("{a}: {ca}, {b}: {cb} səsli mesaj.", "{a}: {ca}, {b}: {cb} voice notes.")))
    qs.append(num_question(rng, "stats",
        bi("Cəmi neçə səsli mesaj göndərmisiniz?", "How many voice notes have you sent in total?"),
        sum(audio.values()),
        bi(f"{sum(audio.values())} səsli mesaj. Hər {total // max(1, sum(audio.values()))} mesajdan biri səslidir.",
           f"{sum(audio.values())} voice notes. One in every {total // max(1, sum(audio.values()))} messages is a voice note.")))

    stick = by(lambda m: m["kind"] == "sticker")
    qs.append(who_question("stats", bi("Kim daha çox stiker göndərib?", "Who sends more stickers?"),
        (A, B), {A: stick[a], B: stick[b]},
        bi("{a}: {ca}, {b}: {cb} stiker.", "{a}: {ca}, {b}: {cb} stickers.")))

    img = by(lambda m: m["kind"] == "image")
    qs.append(who_question("stats", bi("Kim daha çox şəkil göndərib?", "Who sends more photos?"),
        (A, B), {A: img[a], B: img[b]},
        bi("{a}: {ca}, {b}: {cb} şəkil.", "{a}: {ca}, {b}: {cb} photos.")))

    # Conversation starters: first message after 6h silence
    starts = collections.Counter()
    prev = None
    for m in msgs:
        if prev is None or (m["ts"] - prev["ts"]).total_seconds() > 6 * 3600:
            starts[m["sender"]] += 1
        prev = m
    qs.append(who_question("stats",
        bi("Uzun fasilədən sonra söhbətə adətən kim başlayır?", "After a long silence, who usually starts the conversation?"),
        (A, B), {A: starts[a], B: starts[b]},
        bi("{a} {ca} dəfə, {b} {cb} dəfə söhbətə başlayıb (6 saatdan uzun fasilədən sonra).",
           "{a} started {ca} times, {b} started {cb} times (after gaps longer than 6 hours).")))

    # Reply speed
    rt = {n: [] for n in names}
    prev = None
    for m in msgs:
        if prev and prev["sender"] != m["sender"]:
            d = (m["ts"] - prev["ts"]).total_seconds()
            if d < 6 * 3600:
                rt[m["sender"]].append(d)
        prev = m
    med = {n: statistics.median(rt[n]) if rt[n] else 0 for n in names}
    faster = a if med[a] <= med[b] else b
    qs.append(mc("stats", bi("Kim daha tez cavab yazır?", "Who replies faster?"),
        [bi(A, A), bi(B, B)], 0 if faster == a else 1,
        bi(f"Orta cavab vaxtı: {A} {int(med[a])} saniyə, {B} {int(med[b])} saniyə.",
           f"Median reply time: {A} {int(med[a])} seconds, {B} {int(med[b])} seconds.")))

    night = by(lambda m: m["ts"].hour < 6)
    qs.append(who_question("stats", bi("Gecə 00:00–06:00 arası kim daha çox yazır?", "Who texts more between midnight and 6 AM?"),
        (A, B), {A: night[a], B: night[b]},
        bi("{a}: {ca}, {b}: {cb} gecə mesajı.", "{a}: {ca}, {b}: {cb} late-night messages.")))
    pct_night = round(100 * sum(night.values()) / total)
    opts = sorted({pct_night, max(1, pct_night - 15), min(99, pct_night + 12), min(99, pct_night + 27)})
    while len(opts) < 4:
        opts.append(opts[-1] + 9)
    qs.append(mc("stats", bi("Mesajlarınızın neçə faizi gecə yarısı ilə səhər 6 arasında yazılıb?",
                             "What share of your messages were sent between midnight and 6 AM?"),
        [bi(f"{o}%", f"{o}%") for o in opts], opts.index(pct_night),
        bi(f"{pct_night}%. Siz gecə quşusunuz.", f"{pct_night}%. You are night owls.")))

    hours = collections.Counter(m["ts"].hour for m in msgs)
    top_hour = hours.most_common(1)[0][0]
    hour_opts = [top_hour]
    for h in rng.sample(range(24), 24):
        if abs(h - top_hour) >= 3 and h not in hour_opts:
            hour_opts.append(h)
        if len(hour_opts) == 4:
            break
    rng.shuffle(hour_opts)
    qs.append(mc("stats", bi("Günün hansı saatında ən çox yazışırsınız?", "At what hour of the day do you text the most?"),
        [bi(f"{h:02d}:00–{(h + 1) % 24:02d}:00", f"{h:02d}:00–{(h + 1) % 24:02d}:00") for h in hour_opts],
        hour_opts.index(top_hour),
        bi(f"Saat {top_hour:02d}:00–{(top_hour + 1) % 24:02d}:00 arası {hours[top_hour]} mesaj.",
           f"{hours[top_hour]} messages between {top_hour:02d}:00 and {(top_hour + 1) % 24:02d}:00.")))

    wd = collections.Counter(m["ts"].weekday() for m in msgs)
    top_wd = wd.most_common(1)[0][0]
    wd_opts = [top_wd] + [w for w in rng.sample(range(7), 7) if w != top_wd][:3]
    rng.shuffle(wd_opts)
    qs.append(mc("stats", bi("Həftənin hansı günü ən çox yazışırsınız?", "Which day of the week do you text the most?"),
        [bi(AZ_DAYS[w], EN_DAYS[w]) for w in wd_opts], wd_opts.index(top_wd),
        bi(f"{AZ_DAYS[top_wd]}: {wd[top_wd]} mesaj. Ən sakit gün: {AZ_DAYS[wd.most_common()[-1][0]]}.",
           f"{EN_DAYS[top_wd]}: {wd[top_wd]} messages. Quietest day: {EN_DAYS[wd.most_common()[-1][0]]}.")))

    months = collections.Counter((m["ts"].year, m["ts"].month) for m in msgs)
    top_month = months.most_common(1)[0][0]
    others = [k for k in months if k != top_month]
    m_opts = [top_month] + rng.sample(others, min(3, len(others)))
    rng.shuffle(m_opts)
    qs.append(mc("stats", bi("Hansı ayda ən çox yazışmısınız?", "In which month did you text the most?"),
        [bi(f"{AZ_MONTHS[mo - 1]} {y}", f"{EN_MONTHS[mo - 1]} {y}") for y, mo in m_opts], m_opts.index(top_month),
        bi(f"{AZ_MONTHS[top_month[1] - 1]} {top_month[0]}: {months[top_month]} mesaj.",
           f"{EN_MONTHS[top_month[1] - 1]} {top_month[0]}: {months[top_month]} messages.")))

    days = collections.Counter(m["ts"].date() for m in msgs)
    top_day = days.most_common(1)[0][0]
    other_days = [d for d in days if abs((d - top_day).days) > 20]
    d_opts = [top_day] + rng.sample(other_days, 3)
    rng.shuffle(d_opts)
    qs.append(mc("stats", bi("Bir gündə ən çox mesaj yazdığınız tarix hansıdır?", "On which single day did you exchange the most messages?"),
        [date_opt(d) for d in d_opts], d_opts.index(top_day),
        bi(f"{az_date(top_day)}: bir gündə {days[top_day]} mesaj!", f"{en_date(top_day)}: {days[top_day]} messages in one day!")))

    # Days with no messages
    all_days = [first.date() + dt.timedelta(days=i) for i in range(span_days)]
    silent = [d for d in all_days if d not in days]
    if 1 <= len(silent) <= 3:
        target = silent[0]
        d_opts = [target] + rng.sample([d for d in all_days if d in days and abs((d - target).days) > 10], 3)
        rng.shuffle(d_opts)
        qs.append(mc("stats",
            bi(f"{span_days} gündən yalnız {len(silent)} gün heç yazışmamısınız. Hansı gün?",
               f"Out of {span_days} days there {'was only 1 day' if len(silent) == 1 else 'were only ' + str(len(silent)) + ' days'} with no messages. Which one?"),
            [date_opt(d) for d in d_opts], d_opts.index(target),
            bi(f"{az_date(target)} ({AZ_DAYS[target.weekday()]}). O gün nə oldu?",
               f"{en_date(target)} ({EN_DAYS[target.weekday()]}). What happened that day?")))
    else:
        qs.append(num_question(rng, "stats",
            bi(f"{span_days} gündən neçəsində heç yazışmamısınız?", f"Out of {span_days} days, on how many did you not text at all?"),
            max(1, len(silent)),
            bi(f"{len(silent)} gün. {len(days)} gün yazışmısınız.", f"{len(silent)} days. You texted on {len(days)} of them.")))

    # Longest silence
    gaps = [((n["ts"] - p["ts"]).total_seconds(), p, n) for p, n in zip(msgs, msgs[1:])]
    gap, gp, gn = max(gaps, key=lambda g: g[0])
    gap_h = gap / 3600
    cand = sorted({round(gap_h), max(2, round(gap_h * 0.4)), round(gap_h * 1.7), round(gap_h * 2.5)})
    while len(cand) < 4:
        cand.append(cand[-1] + 5)
    qs.append(mc("stats", bi("Ən uzun sükutunuz nə qədər çəkib?", "What is the longest you have gone without texting?"),
        [bi(f"{c} saat", f"{c} hours") for c in cand], cand.index(round(gap_h)),
        bi(f"{fmt_duration(gap)['az']}: {az_date(gp['ts'].date())} → {az_date(gn['ts'].date())}. Sükutu {D[gn['sender']]} pozdu.",
           f"{fmt_duration(gap)['en']}: {en_date(gp['ts'].date())} → {en_date(gn['ts'].date())}. {D[gn['sender']]} broke the silence.")))

    # First message
    fm = msgs[0]
    qs.append(mc("stats", bi("Bu söhbətdə ilk mesajı kim yazıb?", "Who sent the very first message in this chat?"),
        [bi(A, A), bi(B, B)], 0 if fm["sender"] == a else 1,
        bi(f"{D[fm['sender']]}, {az_date(fm['ts'].date())}, saat {fm['ts'].strftime('%H:%M')}.",
           f"{D[fm['sender']]}, on {en_date(fm['ts'].date())} at {fm['ts'].strftime('%H:%M')}.")))
    h = fm["ts"].hour
    t_opts = sorted({h, (h + 9) % 24, (h + 14) % 24, (h + 19) % 24})
    qs.append(mc("stats", bi("İlk mesaj günün hansı saatında yazılıb?", "At what time of day was the first message sent?"),
        [bi(f"{t:02d}:{fm['ts'].minute:02d}", f"{t:02d}:{fm['ts'].minute:02d}") for t in t_opts], t_opts.index(h),
        bi(f"{fm['ts'].strftime('%H:%M')}, {az_date(fm['ts'].date())}.", f"{fm['ts'].strftime('%H:%M')} on {en_date(fm['ts'].date())}.")))

    deleted = by(lambda m: m["kind"] == "deleted")
    if sum(deleted.values()) >= 5:
        qs.append(who_question("stats", bi("Kim daha çox mesaj silib?", "Who has deleted more messages?"),
            (A, B), {A: deleted[a], B: deleted[b]},
            bi("{a}: {ca}, {b}: {cb} silinmiş mesaj. Nə gizlədirsiniz?", "{a}: {ca}, {b}: {cb} deleted messages. What are you hiding?")))

    edited = by(lambda m: m["edited"])
    if sum(edited.values()) >= 10:
        qs.append(who_question("stats", bi("Kim mesajlarını daha çox redaktə edir?", "Who edits their messages more often?"),
            (A, B), {A: edited[a], B: edited[b]},
            bi("{a}: {ca}, {b}: {cb} redaktə olunmuş mesaj.", "{a}: {ca}, {b}: {cb} edited messages.")))

    # Calls
    missed = by(lambda m: m["kind"] == "missed_call")
    for n in names:
        if missed[n] >= 20:
            qs.append(num_question(rng, "stats",
                bi(f"{D[n]} neçə dəfə zəng edib və cavab ala bilməyib?", f"How many of {D[n]}'s voice calls went unanswered?"),
                missed[n],
                bi(f"{missed[n]} cavabsız zəng. Telefonu götürün!", f"{missed[n]} missed calls. Pick up the phone!")))
    calls = [(call_minutes(m["text"]), m) for m in msgs if m["kind"] in ("voice_call", "video_call")]
    calls = [(d, m) for d, m in calls if d]
    if calls:
        dur, cm = max(calls, key=lambda c: c[0])
        c_opts = sorted({int(dur), max(5, int(dur * 0.4)), int(dur * 1.6), int(dur * 2.3)})
        while len(c_opts) < 4:
            c_opts.append(c_opts[-1] + 30)
        qs.append(mc("stats", bi("Ən uzun zənginiz nə qədər çəkib?", "How long was your longest call?"),
            [bi(f"{c // 60} saat {c % 60} dəq" if c >= 60 else f"{c} dəq", f"{c // 60} h {c % 60} min" if c >= 60 else f"{c} min") for c in c_opts],
            c_opts.index(int(dur)),
            bi(f"{int(dur) // 60} saat {int(dur) % 60} dəqiqə, {az_date(cm['ts'].date())}. Zəngi {D[cm['sender']]} edib.",
               f"{int(dur) // 60} h {int(dur) % 60} min on {en_date(cm['ts'].date())}. {D[cm['sender']]} made the call.")))
        total_calls = len([m for m in msgs if m["kind"] in ("voice_call", "video_call", "missed_call", "missed_video")])
        qs.append(num_question(rng, "stats", bi("Cəmi neçə dəfə zəngləşmisiniz (cavabsızlar daxil)?", "How many calls have you made in total (missed ones included)?"),
            total_calls, bi(f"{total_calls} zəng.", f"{total_calls} calls.")))

    # Love words
    for m in texts:
        m["norm"] = norm_text(m["text"])

    def phrase_counts(*variants):
        keys = [norm(v) for v in variants]
        return {n: sum(1 for m in texts if m["sender"] == n and any(k in m["norm"] for k in keys)) for n in names}

    def first_msg(*variants):
        keys = [norm(v) for v in variants]
        return next((m for m in texts if any(k in m["norm"] for k in keys)), None)

    love = phrase_counts("sevirəm")
    if sum(love.values()) >= 10:
        qs.append(who_question("stats", bi("Kim daha çox «sevirəm» yazıb?", "Who has written “sevirəm” (I love you) more times?"),
            (A, B), {A: love[a], B: love[b]},
            bi("{a}: {ca} dəfə, {b}: {cb} dəfə.", "{a}: {ca} times, {b}: {cb} times.")))
        fl = first_msg("sevirəm")
        d_opts = [fl["ts"].date()]
        pool = [d for d in days if abs((d - fl["ts"].date()).days) > 7]
        d_opts += rng.sample(pool, 3)
        rng.shuffle(d_opts)
        qs.append(mc("stats", bi("«Sevirəm» sözü söhbətdə ilk dəfə nə vaxt yazılıb?", "When did “sevirəm” first appear in the chat?"),
            [date_opt(d) for d in d_opts], d_opts.index(fl["ts"].date()),
            bi(f"{az_date(fl['ts'].date())}, saat {fl['ts'].strftime('%H:%M')}. İlk yazan: {D[fl['sender']]}.",
               f"{en_date(fl['ts'].date())} at {fl['ts'].strftime('%H:%M')}. First to write it: {D[fl['sender']]}.")))
        qs.append(mc("stats", bi("«Sevirəm» sözünü ilk kim yazıb?", "Who wrote “sevirəm” first?"),
            [bi(A, A), bi(B, B)], 0 if fl["sender"] == a else 1,
            bi(f"{D[fl['sender']]}, {az_date(fl['ts'].date())}.", f"{D[fl['sender']]}, on {en_date(fl['ts'].date())}.")))

    for word, label_az, label_en, min_total in [
        ("günaydın", "«günaydın»", "“günaydın” (good morning)", 20),
        ("toy", "«toy»", "“toy” (wedding)", 15),
        ("evlən", "«evlənmək» (evlən...)", "“evlən…” (marry)", 8),
        ("darıx", "«darıxmışam» (darıx...)", "“darıx…” (miss you)", 10),
        ("bağışla", "«bağışla»", "“bağışla” (sorry)", 6),
        ("üzr", "«üzr istəyirəm»", "“üzr…” (apologise)", 6),
        ("yatdın", "«yatdın?»", "“yatdın?” (are you asleep?)", 8),
    ]:
        pat = re.compile(r"\b" + re.escape(norm(word)))
        c = {n: sum(1 for m in texts if m["sender"] == n and pat.search(m["norm"])) for n in names}
        if sum(c.values()) >= min_total and c[a] != c[b]:
            qs.append(who_question("stats", bi(f"Kim daha çox {label_az} yazıb?", f"Who has written {label_en} more often?"),
                (A, B), {A: c[a], B: c[b]},
                bi("{a}: {ca} dəfə, {b}: {cb} dəfə.", "{a}: {ca} times, {b}: {cb} times.")))

    # Emoji
    ec = {n: collections.Counter() for n in names}
    for m in texts:
        for e in EMOJI_RE.findall(m["text"]):
            ec[m["sender"]][e] += 1
    all_emoji = collections.Counter()
    for n in names:
        all_emoji.update(ec[n])
    for n in names:
        if not ec[n]:
            continue
        top = ec[n].most_common(1)[0][0]
        distract = [e for e, _ in all_emoji.most_common(20) if e != top]
        e_opts = [top] + rng.sample(distract, min(3, len(distract)))
        rng.shuffle(e_opts)
        qs.append(mc("stats", bi(f"{D[n]} ən çox hansı emojini işlədir?", f"Which emoji does {D[n]} use the most?"),
            [bi(e, e) for e in e_opts], e_opts.index(top),
            bi(f"{top} — {ec[n][top]} dəfə. Sonra: " + " ".join(e for e, _ in ec[n].most_common(4)[1:]),
               f"{top} — {ec[n][top]} times. Runners-up: " + " ".join(e for e, _ in ec[n].most_common(4)[1:])),
            about=n))
    tot_e = {n: sum(ec[n].values()) for n in names}
    qs.append(who_question("stats", bi("Kim daha çox emoji işlədir?", "Who uses more emoji?"),
        (A, B), {A: tot_e[a], B: tot_e[b]},
        bi("{a}: {ca}, {b}: {cb} emoji.", "{a}: {ca}, {b}: {cb} emoji.")))

    # Questions asked
    qc = by(lambda m: m["kind"] == "text" and "?" in m["text"])
    qs.append(who_question("stats", bi("Kim daha çox sual verir?", "Who asks more questions?"),
        (A, B), {A: qc[a], B: qc[b]},
        bi("{a}: {ca}, {b}: {cb} sual işarəli mesaj.", "{a}: {ca}, {b}: {cb} messages with a question mark.")))

    # Longest message
    lm = max(texts, key=lambda m: len(m["text"]))
    qs.append(mc("stats", bi("Ən uzun mesajı kim yazıb?", "Who wrote the longest message ever?"),
        [bi(A, A), bi(B, B)], 0 if lm["sender"] == a else 1,
        bi(f"{D[lm['sender']]}: {len(lm['text'])} simvol, {az_date(lm['ts'].date())}. Başlanğıcı: «{lm['text'][:70]}…»",
           f"{D[lm['sender']]}: {len(lm['text'])} characters on {en_date(lm['ts'].date())}. It starts: “{lm['text'][:70]}…”")))
    avg = {n: statistics.mean(len(m["text"]) for m in texts if m["sender"] == n) for n in names}
    qs.append(who_question("stats", bi("Kimin mesajları orta hesabla daha uzundur?", "Whose messages are longer on average?"),
        (A, B), {A: round(avg[a]), B: round(avg[b])},
        bi("{a}: orta {ca} simvol, {b}: orta {cb} simvol.", "{a}: {ca} characters on average, {b}: {cb}.")))

    # Signature words: how many times did X write their favourite filler
    wc = WordCounts(names)
    for m in texts:
        for w in WORD_RE.findall(m["text"]):
            if len(w) >= 3:
                wc.add(m["sender"], w)
    for n in names:
        other = [o for o in names if o != n][0]
        top = [(k, c) for k, c in wc.by[n].most_common(30) if c >= 3 * max(1, wc.by[other][k]) and c >= 80]
        if top:
            k, c = top[0]
            w = wc.spelling(n, k)
            qs.append(num_question(rng, "stats",
                bi(f"{D[n]} neçə dəfə «{w}» yazıb?", f"How many times has {D[n]} written “{w}”?"),
                c, bi(f"{c} dəfə{wc.variants_note(k, 'az')}. {D[other]} isə cəmi {wc.by[other][k]} dəfə.",
                      f"{c} times{wc.variants_note(k, 'en')}. {D[other]} only {wc.by[other][k]} times."),
                about=n))

    # Who types Azerbaijani letters, who types on an English keyboard
    az_letters = set("əıöüğşçƏİÖÜĞŞÇ")
    ascii_share = {}
    for n in names:
        mine = [m for m in texts if m["sender"] == n and any(ch.isalpha() for ch in m["text"]) and len(m["text"]) >= 15]
        plain = sum(1 for m in mine if not (set(m["text"]) & az_letters))
        ascii_share[n] = round(100 * plain / max(1, len(mine)))
    if abs(ascii_share[a] - ascii_share[b]) >= 10:
        qs.append(who_question("stats",
            bi("Kim daha çox ingilis klaviaturası ilə yazır (ə, ş, ç olmadan)?", "Who types more often without Azerbaijani letters (ə, ş, ç)?"),
            (A, B), {A: ascii_share[a], B: ascii_share[b]},
            bi("{a}: mesajların {ca}%-i, {b}: {cb}%-i Azərbaycan hərfləri olmadan yazılıb.",
               "{a}: {ca}% of messages, {b}: {cb}% written without any Azerbaijani letters.")))
    return qs, wc


class WordCounts:
    """Per-person word counts keyed by normalized spelling, remembering how each person writes it."""

    def __init__(self, names):
        self.names = names
        self.by = {n: collections.Counter() for n in names}        # name -> norm -> count
        self.forms = {n: collections.defaultdict(collections.Counter) for n in names}  # name -> norm -> spelling -> count
        self.total = collections.Counter()

    def add(self, name, word):
        k = norm(word)
        self.by[name][k] += 1
        self.forms[name][k][word.lower()] += 1
        self.total[k] += 1

    def spelling(self, name, k):
        """The way this person most often writes the word."""
        f = self.forms[name].get(k)
        if f:
            return f.most_common(1)[0][0]
        for n in self.names:
            if self.forms[n].get(k):
                return self.forms[n][k].most_common(1)[0][0]
        return k

    def all_spellings(self, k):
        c = collections.Counter()
        for n in self.names:
            c.update(self.forms[n].get(k, {}))
        return [w for w, _ in c.most_common()]

    def variants_note(self, k, lang):
        sp = self.all_spellings(k)
        if len(sp) < 2:
            return ""
        joined = " / ".join(sp[:4])
        return f" ({joined} birlikdə)" if lang == "az" else f" (counting {joined})"


def is_real_word(w):
    """Filter out keyboard-smash laughter like 'djdjd' or 'hshsh'."""
    vowels = sum(1 for ch in w if ch in "aeiouəöüıAEIOUƏÖÜI")
    return len(set(w)) >= 4 and vowels / len(w) >= 0.3


def whose_word_questions(rng, wc, names, display):
    a, b = names
    A, B = display[a], display[b]
    qs = []
    for n in names:
        other = [o for o in names if o != n][0]
        uniq = [(k, c) for k, c in wc.by[n].most_common(600)
                if len(k) >= 4 and c >= 12 and wc.by[other][k] <= max(1, c // 15) and is_real_word(k)]
        for k, c in uniq[:25]:
            w = wc.spelling(n, k)
            qs.append(mc("word",
                bi(f"Bu söz kimin lüğətindəndir: «{w}»?", f"Whose vocabulary does this word belong to: “{w}”?"),
                [bi(A, A), bi(B, B)], 0 if n == a else 1,
                bi(f"{display[n]} bunu {c} dəfə yazıb{wc.variants_note(k, 'az')}, {display[other]} isə {wc.by[other][k]} dəfə.",
                   f"{display[n]} wrote it {c} times{wc.variants_note(k, 'en')}, {display[other]} {wc.by[other][k]} times.")))
    rng.shuffle(qs)
    return qs


EXCLUDE = []  # lower-case substrings; messages containing any of them never appear as quotes


def load_exclude(path):
    p = Path(path)
    if not p.exists():
        return []
    return [l.strip().lower() for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def quotable(m):
    """Is this message safe to show verbatim in the game?"""
    if m["kind"] != "text":
        return False
    low = m["text"].lower()
    return not any(x in low for x in EXCLUDE)


def clean_candidates(msgs, lo=30, hi=120):
    out = []
    for m in msgs:
        t = m["text"]
        if not quotable(m) or "\n" in t or not lo <= len(t) <= hi:
            continue
        if "@" in t or t.count(" ") < 3:
            continue
        out.append(m)
    return out


def who_said_questions(rng, msgs, names, display, n_each=120):
    cands = clean_candidates(msgs, 35, 130)
    qs = []
    a, b = names
    for n in names:
        pool = [m for m in cands if m["sender"] == n]
        # prefer messages with some punctuation or emoji, they are more characterful
        pool.sort(key=lambda m: -(len(set(m["text"])) + 10 * bool(EMOJI_RE.search(m["text"]))))
        pool = pool[: max(n_each * 3, 200)]
        for m in rng.sample(pool, min(n_each, len(pool))):
            qs.append(mc("who",
                bi("Bu mesajı kim yazıb?", "Who wrote this message?"),
                [bi(display[a], display[a]), bi(display[b], display[b])], 0 if n == a else 1,
                bi(f"{display[n]}, {az_date(m['ts'].date())}, saat {m['ts'].strftime('%H:%M')}.",
                   f"{display[n]} on {en_date(m['ts'].date())} at {m['ts'].strftime('%H:%M')}."),
                context=[{"text": m["text"]}]))
    rng.shuffle(qs)
    return qs


def blank_questions(rng, msgs, names, display, wc, n=150):
    cands = clean_candidates(msgs, 30, 120)
    total_wc = wc.total
    common = {k for k, _ in total_wc.most_common(40)}
    qs = []
    rng.shuffle(cands)
    for m in cands:
        words = [w for w in WORD_RE.findall(m["text"]) if len(w) >= 5]
        words = [w for w in words if 3 <= total_wc[norm(w)] <= 400 and norm(w) not in common and is_real_word(norm(w))]
        if not words:
            continue
        w = rng.choice(words)
        lw = w.lower()
        k = norm(w)
        freq = total_wc[k]
        sender = m["sender"]
        distract = [wc.spelling(sender, x) for x, c in wc.by[sender].items()
                    if x != k and abs(len(x) - len(k)) <= 2 and freq / 4 <= c <= freq * 4 and len(x) >= 4 and is_real_word(x)]
        distract = [d for d in distract if d != lw]
        if len(distract) < 3:
            continue
        opts = [lw] + rng.sample(distract, 3)
        rng.shuffle(opts)
        masked = re.sub(re.escape(w), "＿" * min(6, len(w)), m["text"], count=1)
        qs.append(mc("blank",
            bi(f"{display[m['sender']]} burada hansı sözü yazıb?", f"Which word did {display[m['sender']]} write here?"),
            [bi(o, o) for o in opts], opts.index(lw),
            bi(f"«{w}» — {az_date(m['ts'].date())}.", f"“{w}” — {en_date(m['ts'].date())}."),
            about=m["sender"], context=[{"text": masked}]))
        if len(qs) >= n:
            break
    return qs


def reply_questions(rng, msgs, names, display, n=120):
    pairs = []
    for p, q in zip(msgs, msgs[1:]):
        if p["sender"] == q["sender"] or not quotable(p) or not quotable(q):
            continue
        if not (20 <= len(p["text"]) <= 120 and 15 <= len(q["text"]) <= 100):
            continue
        if "\n" in p["text"] or "\n" in q["text"] or q["text"].count(" ") < 2:
            continue
        if (q["ts"] - p["ts"]).total_seconds() > 300:
            continue
        pairs.append((p, q))
    rng.shuffle(pairs)
    qs = []
    replies_by = {nm: [q["text"] for _, q in pairs if q["sender"] == nm] for nm in names}
    for p, q in pairs[: n * 2]:
        pool = [t for t in replies_by[q["sender"]] if t != q["text"] and abs(len(t) - len(q["text"])) <= 25]
        if len(pool) < 3:
            continue
        opts = [q["text"]] + rng.sample(pool, 3)
        rng.shuffle(opts)
        qs.append(mc("reply",
            bi(f"{display[q['sender']]} buna nə cavab verib?", f"How did {display[q['sender']]} reply to this?"),
            [bi(o, o) for o in opts], opts.index(q["text"]),
            bi(f"{az_date(p['ts'].date())}, {(q['ts'] - p['ts']).seconds} saniyə sonra cavab.",
               f"{en_date(p['ts'].date())}, replied after {(q['ts'] - p['ts']).seconds} seconds."),
            about=q["sender"], context=[{"sender": display[p["sender"]], "text": p["text"]}]))
        if len(qs) >= n:
            break
    return qs


def day_questions(rng, msgs, names, display, n=40):
    by_day = collections.defaultdict(list)
    for m in msgs:
        if quotable(m) and 25 <= len(m["text"]) <= 110 and "\n" not in m["text"]:
            by_day[m["ts"].date()].append(m)
    busy = [d for d, ms in by_day.items() if len(ms) >= 40]
    all_days = sorted(by_day)
    rng.shuffle(busy)
    qs = []
    for d in busy[:n]:
        ms = by_day[d]
        start = rng.randrange(0, max(1, len(ms) - 3))
        snippet = ms[start:start + 3]
        if len({m["sender"] for m in snippet}) < 2:
            continue
        distract = [x for x in all_days if abs((x - d).days) >= 25]
        opts = [d] + rng.sample(distract, 3)
        rng.shuffle(opts)
        qs.append(mc("day",
            bi("Bu söhbət hansı gün olub?", "On which day did this exchange happen?"),
            [date_opt(x) for x in opts], opts.index(d),
            bi(f"{az_date(d)}, {AZ_DAYS[d.weekday()]}. O gün {len(by_day[d])} yazılı mesaj olub.",
               f"{en_date(d)}, a {EN_DAYS[d.weekday()]}. {len(by_day[d])} text messages that day."),
            context=[{"sender": display[m["sender"]], "text": m["text"]} for m in snippet]))
    return qs


# ----------------------------------------------------------------------------
# Extra rounds
# ----------------------------------------------------------------------------

EMOJI_ONLY_RE = re.compile(
    "^[\U0001F300-\U0001FAFF\U0001F900-\U0001F9FF\U0001F1E6-\U0001F1FF☀-➿❤⭐⭕️‍⃣\U0001F3FB-\U0001F3FF\s]+$"
)


def emoji_questions(rng, msgs, names, display, n_each=60):
    """Messages that are nothing but emoji: guess the sender."""
    a, b = names
    qs = []
    for n in names:
        pool = [m for m in msgs if m["sender"] == n and m["kind"] == "text" and EMOJI_ONLY_RE.match(m["text"])
                and 1 <= len(EMOJI_RE.findall(m["text"])) <= 12]
        # dedupe identical strings, prefer varied ones
        seen, uniq = set(), []
        for m in pool:
            key = m["text"].replace("️", "")
            if key not in seen:
                seen.add(key)
                uniq.append(m)
        for m in rng.sample(uniq, min(n_each, len(uniq))):
            qs.append(mc("emoji",
                bi("Bu emoji mesajını kim göndərib?", "Who sent this emoji-only message?"),
                [bi(display[a], display[a]), bi(display[b], display[b])], 0 if n == a else 1,
                bi(f"{display[n]}, {az_date(m['ts'].date())}, saat {m['ts'].strftime('%H:%M')}.",
                   f"{display[n]} on {en_date(m['ts'].date())} at {m['ts'].strftime('%H:%M')}."),
                context=[{"text": m["text"]}]))
    rng.shuffle(qs)
    return qs


def voice_questions(rng, msgs, names, display):
    a, b = names
    A, B = display[a], display[b]
    D = {a: A, b: B}
    qs = []
    voice = [m for m in msgs if m["kind"] == "audio"]
    if len(voice) < 50:
        return qs
    per_day = collections.Counter(m["ts"].date() for m in voice)
    top_day, top_n = per_day.most_common(1)[0]
    qs.append(num_question(rng, "voice",
        bi(f"{az_date(top_day)} tarixində neçə səsli mesaj göndərmisiniz? (rekord gün)",
           f"How many voice notes did you send on {en_date(top_day)}, your record day?"),
        top_n, bi(f"{top_n} səsli mesaj bir gündə.", f"{top_n} voice notes in a single day.")))
    others = [d for d in per_day if abs((d - top_day).days) > 20]
    d_opts = [top_day] + rng.sample(others, 3)
    rng.shuffle(d_opts)
    qs.append(mc("voice", bi("Ən çox səsli mesaj göndərdiyiniz gün hansıdır?", "On which day did you send the most voice notes?"),
        [date_opt(d) for d in d_opts], d_opts.index(top_day),
        bi(f"{az_date(top_day)}: {top_n} səsli mesaj.", f"{en_date(top_day)}: {top_n} voice notes.")))

    per_month = collections.Counter((m["ts"].year, m["ts"].month) for m in voice)
    tm = per_month.most_common(1)[0][0]
    m_opts = [tm] + rng.sample([k for k in per_month if k != tm], 3)
    rng.shuffle(m_opts)
    qs.append(mc("voice", bi("Hansı ayda ən çox səsli mesaj göndərmisiniz?", "In which month did you send the most voice notes?"),
        [bi(f"{AZ_MONTHS[mo - 1]} {y}", f"{EN_MONTHS[mo - 1]} {y}") for y, mo in m_opts], m_opts.index(tm),
        bi(f"{AZ_MONTHS[tm[1] - 1]} {tm[0]}: {per_month[tm]} səsli mesaj.", f"{EN_MONTHS[tm[1] - 1]} {tm[0]}: {per_month[tm]} voice notes.")))

    hours = collections.Counter(m["ts"].hour for m in voice)
    th = hours.most_common(1)[0][0]
    h_opts = [th]
    for h in rng.sample(range(24), 24):
        if abs(h - th) >= 3 and h not in h_opts:
            h_opts.append(h)
        if len(h_opts) == 4:
            break
    rng.shuffle(h_opts)
    qs.append(mc("voice", bi("Səsli mesajlar ən çox hansı saatda gedir?", "At what hour do voice notes peak?"),
        [bi(f"{h:02d}:00–{(h + 1) % 24:02d}:00", f"{h:02d}:00–{(h + 1) % 24:02d}:00") for h in h_opts], h_opts.index(th),
        bi(f"{th:02d}:00–{(th + 1) % 24:02d}:00: {hours[th]} səsli mesaj.", f"{th:02d}:00–{(th + 1) % 24:02d}:00: {hours[th]} voice notes.")))

    night = {n: sum(1 for m in voice if m["sender"] == n and m["ts"].hour < 6) for n in names}
    qs.append(who_question("voice", bi("Gecə 00:00–06:00 arası kim daha çox səsli mesaj atır?", "Who sends more voice notes between midnight and 6 AM?"),
        (A, B), {A: night[a], B: night[b]}, bi("{a}: {ca}, {b}: {cb}.", "{a}: {ca}, {b}: {cb}.")))

    years = sorted({m["ts"].year for m in voice})
    for y in years:
        c = {n: sum(1 for m in voice if m["sender"] == n and m["ts"].year == y) for n in names}
        if sum(c.values()) >= 100 and c[a] != c[b]:
            qs.append(who_question("voice", bi(f"{y}-ci ildə kim daha çox səsli mesaj göndərib?", f"Who sent more voice notes in {y}?"),
                (A, B), {A: c[a], B: c[b]}, bi("{a}: {ca}, {b}: {cb} səsli mesaj.", "{a}: {ca}, {b}: {cb} voice notes.")))

    # longest run of consecutive voice notes with no text in between
    best = (0, None, None)
    run, start = 0, None
    for m in msgs:
        if m["kind"] == "audio":
            run += 1
            start = start or m
            if run > best[0]:
                best = (run, start, m)
        elif m["kind"] in ("text", "sticker", "image", "video"):
            run, start = 0, None
    if best[0] >= 5:
        qs.append(num_question(rng, "voice",
            bi("Arada heç yazılı mesaj olmadan ard-arda ən çox neçə səsli mesaj gedib?", "What is the longest run of voice notes with no text message in between?"),
            best[0], bi(f"{best[0]} səsli mesaj ard-arda, {az_date(best[1]['ts'].date())}.", f"{best[0]} voice notes in a row on {en_date(best[1]['ts'].date())}.")))

    share = round(100 * len(voice) / len(msgs))
    opts = sorted({share, max(1, share - 9), min(95, share + 11), min(96, share + 24)})
    while len(opts) < 4:
        opts.append(opts[-1] + 7)
    qs.append(mc("voice", bi("Bütün mesajların neçə faizi səsli mesajdır?", "What share of all messages are voice notes?"),
        [bi(f"{o}%", f"{o}%") for o in opts], opts.index(share),
        bi(f"{share}%: {len(voice)} səsli mesaj.", f"{share}%: {len(voice)} voice notes.")))
    return qs


REPLY_BUCKETS = [
    (30, bi("30 saniyədən az", "Under 30 seconds")),
    (5 * 60, bi("1–5 dəqiqə", "1 to 5 minutes")),
    (60 * 60, bi("10–60 dəqiqə", "10 to 60 minutes")),
    (10 ** 9, bi("3 saatdan çox", "More than 3 hours")),
]


def reply_time_bucket(seconds):
    if seconds < 30:
        return 0
    if 60 <= seconds <= 5 * 60:
        return 1
    if 10 * 60 <= seconds <= 60 * 60:
        return 2
    if seconds > 3 * 3600:
        return 3
    return None  # falls between buckets, skip


def reply_time_questions(rng, msgs, names, display, n=80):
    pairs = []
    for p, q in zip(msgs, msgs[1:]):
        if p["sender"] == q["sender"] or not quotable(p) or not quotable(q):
            continue
        if not (15 <= len(p["text"]) <= 110 and 10 <= len(q["text"]) <= 110) or "\n" in p["text"] or "\n" in q["text"]:
            continue
        # both ends must look like a question and its answer, not two unrelated messages
        secs = (q["ts"] - p["ts"]).total_seconds()
        bucket = reply_time_bucket(secs)
        if bucket is None or secs > 3 * 86400:
            continue
        pairs.append((bucket, secs, p, q))
    rng.shuffle(pairs)
    per_bucket = collections.defaultdict(list)
    for item in pairs:
        per_bucket[item[0]].append(item)
    qs = []
    quota = n // 4
    for bucket in range(4):
        for _, secs, p, q in per_bucket[bucket][:quota]:
            dur = fmt_duration(secs)
            qs.append(mc("speed",
                bi(f"{display[q['sender']]} buna nə qədər vaxtdan sonra cavab verib?", f"How long did {display[q['sender']]} take to reply?"),
                [b[1] for b in REPLY_BUCKETS], bucket,
                bi(f"{dur['az']}. {az_date(p['ts'].date())}, saat {p['ts'].strftime('%H:%M')} → {q['ts'].strftime('%H:%M')}.",
                   f"{dur['en']}. {en_date(p['ts'].date())}, {p['ts'].strftime('%H:%M')} → {q['ts'].strftime('%H:%M')}."),
                about=q["sender"],
                context=[{"sender": display[p["sender"]], "text": p["text"]}, {"sender": display[q["sender"]], "text": q["text"]}]))
    rng.shuffle(qs)
    return qs


# Pet names / endearments: (stem to match after normalisation, label shown)
PET_NAMES = [
    ("tatlım", "tatlım"), ("tatlum", "tatlum"), ("tatlış", "tatlış"), ("baby", "baby"), ("bebek", "bebek"), ("bebeyim", "bebeyim"),
    ("canım", "canım"), ("həyatım", "həyatım"), ("ürəyim", "ürəyim"), ("sevgilim", "sevgilim"), ("əzizim", "əzizim"),
    ("balam", "balam"), ("aşkım", "aşkım"), ("gözəlim", "gözəlim"), ("günəşim", "günəşim"), ("ayım", "ayım"),
    ("ciyərim", "ciyərim"), ("quzum", "quzum"), ("cırtdan", "cırtdan"), ("şəkərim", "şəkərim"), ("ömrüm", "ömrüm"),
    ("nəfəsim", "nəfəsim"), ("meleyim", "mələyim"), ("prensesim", "prensesim"), ("yakışıklı", "yakışıklı"),
]


def nickname_questions(rng, msgs, names, display, wc):
    a, b = names
    A, B = display[a], display[b]
    D = {a: A, b: B}
    texts = [m for m in msgs if m["kind"] == "text"]
    qs = []
    found = []
    for stem, label in PET_NAMES:
        k = norm(stem)
        pat = re.compile(r"\b" + re.escape(k) + r"\w*")
        c = {n: sum(1 for m in texts if m["sender"] == n and pat.search(m.get("norm") or norm_text(m["text"]))) for n in names}
        if sum(c.values()) >= 8:
            first = next(m for m in texts if pat.search(m.get("norm") or norm_text(m["text"])))
            found.append((label, k, c, first))
    if not found:
        return qs
    found.sort(key=lambda f: -sum(f[2].values()))
    for label, k, c, first in found:
        if c[a] != c[b] and sum(c.values()) >= 15:
            qs.append(who_question("nick", bi(f"Kim daha çox «{label}» deyir?", f"Who says “{label}” more?"),
                (A, B), {A: c[a], B: c[b]},
                bi("{a}: {ca} dəfə, {b}: {cb} dəfə.", "{a}: {ca} times, {b}: {cb} times.")))
    # first appearance of the top 3 pet names
    days = sorted({m["ts"].date() for m in msgs})
    for label, k, c, first in found[:3]:
        d = first["ts"].date()
        d_opts = [d] + rng.sample([x for x in days if abs((x - d).days) > 14], 3)
        rng.shuffle(d_opts)
        qs.append(mc("nick", bi(f"«{label}» ilk dəfə nə vaxt yazılıb?", f"When was “{label}” first written?"),
            [date_opt(x) for x in d_opts], d_opts.index(d),
            bi(f"{az_date(d)}, yazan: {D[first['sender']]}. Cəmi {sum(c.values())} dəfə işlənib.",
               f"{en_date(d)}, by {D[first['sender']]}. Used {sum(c.values())} times in total.")))
        qs.append(mc("nick", bi(f"«{label}» sözünü ilk kim yazıb?", f"Who was first to write “{label}”?"),
            [bi(A, A), bi(B, B)], 0 if first["sender"] == a else 1,
            bi(f"{D[first['sender']]}, {az_date(d)}.", f"{D[first['sender']]}, on {en_date(d)}.")))
    # each person's favourite pet name
    for n in names:
        mine = sorted(found, key=lambda f: -f[2][n])
        if mine and mine[0][2][n] >= 10:
            top = mine[0][0]
            distract = [f[0] for f in found if f[0] != top]
            if len(distract) >= 3:
                opts = [top] + rng.sample(distract, 3)
                rng.shuffle(opts)
                qs.append(mc("nick", bi(f"{D[n]} ən çox hansı əzizləmə sözünü işlədir?", f"Which pet name does {D[n]} use the most?"),
                    [bi(o, o) for o in opts], opts.index(top),
                    bi(f"«{top}» — {mine[0][2][n]} dəfə. Sonra: " + ", ".join(f[0] for f in mine[1:4] if f[2][n]),
                       f"“{top}” — {mine[0][2][n]} times. Then: " + ", ".join(f[0] for f in mine[1:4] if f[2][n])),
                    about=n))
    total_pet = sum(sum(f[2].values()) for f in found)
    qs.append(num_question(rng, "nick", bi("Cəmi neçə dəfə bir-birinizə əzizləmə sözü ilə müraciət etmisiniz?", "How many times have you called each other by a pet name?"),
        total_pet, bi(f"{total_pet} dəfə, {len(found)} fərqli sözlə.", f"{total_pet} times, using {len(found)} different pet names.")))
    return qs


def streak_questions(rng, msgs, names, display):
    a, b = names
    A, B = display[a], display[b]
    D = {a: A, b: B}
    qs = []
    # longest monologue: consecutive messages by one person without any reply
    best = {n: (0, None) for n in names}
    run, cur, start = 0, None, None
    for m in msgs:
        if m["sender"] == cur:
            run += 1
        else:
            cur, run, start = m["sender"], 1, m
        if run > best[cur][0]:
            best[cur] = (run, start)
    winner = max(names, key=lambda n: best[n][0])
    qs.append(who_question("streak", bi("Cavab almadan ard-arda ən çox mesajı kim yazıb?", "Who holds the record for consecutive messages without a reply?"),
        (A, B), {A: best[a][0], B: best[b][0]},
        bi("{a}: {ca} mesaj ard-arda, {b}: {cb}.", "{a}: {ca} messages in a row, {b}: {cb}.")))
    n_best, start = best[winner]
    qs.append(num_question(rng, "streak",
        bi(f"{D[winner]} ən uzun monoloqunda ard-arda neçə mesaj yazıb?", f"How many messages in a row did {D[winner]} send in their longest monologue?"),
        n_best, bi(f"{n_best} mesaj, {az_date(start['ts'].date())}, saat {start['ts'].strftime('%H:%M')}-dən başlayaraq.",
                   f"{n_best} messages, starting {en_date(start['ts'].date())} at {start['ts'].strftime('%H:%M')}."),
        about=winner))
    # longest streak of days with messages
    days = sorted({m["ts"].date() for m in msgs})
    best_len, cur_len, best_end = 1, 1, days[0]
    for p, q in zip(days, days[1:]):
        cur_len = cur_len + 1 if (q - p).days == 1 else 1
        if cur_len > best_len:
            best_len, best_end = cur_len, q
    qs.append(num_question(rng, "streak",
        bi("Fasiləsiz ən çox neçə gün ard-arda yazışmısınız?", "What is your longest streak of consecutive days with messages?"),
        best_len, bi(f"{best_len} gün ard-arda, {az_date(best_end - dt.timedelta(days=best_len - 1))} → {az_date(best_end)}.",
                     f"{best_len} days in a row, {en_date(best_end - dt.timedelta(days=best_len - 1))} → {en_date(best_end)}.")))
    # longest sticker volley
    run, start, best_s = 0, None, (0, None)
    for m in msgs:
        if m["kind"] == "sticker":
            run += 1
            start = start or m
            if run > best_s[0]:
                best_s = (run, start)
        else:
            run, start = 0, None
    if best_s[0] >= 4:
        qs.append(num_question(rng, "streak",
            bi("Ard-arda ən çox neçə stiker göndərilib?", "What is the longest run of stickers in a row?"),
            best_s[0], bi(f"{best_s[0]} stiker ard-arda, {az_date(best_s[1]['ts'].date())}.", f"{best_s[0]} stickers in a row on {en_date(best_s[1]['ts'].date())}.")))
    # fastest back-and-forth: most messages in a single 10-minute window
    times = [m["ts"] for m in msgs]
    j, best_w = 0, (0, None)
    for i in range(len(times)):
        while times[i] - times[j] > dt.timedelta(minutes=10):
            j += 1
        if i - j + 1 > best_w[0]:
            best_w = (i - j + 1, times[j])
    qs.append(num_question(rng, "streak",
        bi("10 dəqiqə ərzində ən çox neçə mesaj yazmısınız?", "What is the most messages you have exchanged in a 10-minute window?"),
        best_w[0], bi(f"{best_w[0]} mesaj, {az_date(best_w[1].date())}, saat {best_w[1].strftime('%H:%M')}.",
                      f"{best_w[0]} messages on {en_date(best_w[1].date())} at {best_w[1].strftime('%H:%M')}.")))
    # longest reply wait that still got a reply within a week, per person
    for n in names:
        worst = None
        for p, q in zip(msgs, msgs[1:]):
            if q["sender"] == n and p["sender"] != n:
                d = (q["ts"] - p["ts"]).total_seconds()
                if d < 7 * 86400 and (worst is None or d > worst[0]):
                    worst = (d, p, q)
        if worst and worst[0] > 3600:
            hrs = worst[0] / 3600
            opts = sorted({round(hrs), max(1, round(hrs * 0.4)), round(hrs * 1.6), round(hrs * 2.4)})
            while len(opts) < 4:
                opts.append(opts[-1] + 6)
            qs.append(mc("streak",
                bi(f"{D[n]} bir mesaja ən gec neçə saatdan sonra cavab yazıb?", f"What is the longest {D[n]} has ever taken to reply?"),
                [bi(f"{o} saat", f"{o} hours") for o in opts], opts.index(round(hrs)),
                bi(f"{fmt_duration(worst[0])['az']}, {az_date(worst[1]['ts'].date())}.", f"{fmt_duration(worst[0])['en']}, {en_date(worst[1]['ts'].date())}."),
                about=n))
    return qs


def wrapped_data(msgs, names, display):
    """Aggregated series for the Wrapped page. No message text, only counts."""
    a, b = names
    months = sorted({(m["ts"].year, m["ts"].month) for m in msgs})
    label = [f"{y}-{mo:02d}" for y, mo in months]
    per_month = {n: [sum(1 for m in msgs if m["sender"] == n and (m["ts"].year, m["ts"].month) == k) for k in months] for n in names}
    voice_month = {n: [sum(1 for m in msgs if m["sender"] == n and m["kind"] == "audio" and (m["ts"].year, m["ts"].month) == k) for k in months] for n in names}
    heat = [[0] * 24 for _ in range(7)]
    for m in msgs:
        heat[m["ts"].weekday()][m["ts"].hour] += 1
    love = [sum(1 for m in msgs if m["kind"] == "text" and (m["ts"].year, m["ts"].month) == k and "sevirem" in norm_text(m["text"])) for k in months]
    ec = {n: collections.Counter() for n in names}
    for m in msgs:
        if m["kind"] == "text":
            for e in EMOJI_RE.findall(m["text"]):
                ec[m["sender"]][e] += 1
    kinds = {n: collections.Counter(m["kind"] for m in msgs if m["sender"] == n) for n in names}
    days = collections.Counter(m["ts"].date() for m in msgs)
    return {
        "months": label,
        "messages": {display[n]: per_month[n] for n in names},
        "voice": {display[n]: voice_month[n] for n in names},
        "heat": heat,
        "love": love,
        "emoji": {display[n]: ec[n].most_common(6) for n in names},
        "kinds": {display[n]: {k: kinds[n][k] for k in ("text", "audio", "sticker", "image", "video", "voice_call", "missed_call")} for n in names},
        "topDays": [[d.isoformat(), c] for d, c in days.most_common(5)],
        "activeDays": len(days),
    }


def load_custom(path, display):
    if not path or not Path(path).exists():
        return []
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    qs = []
    for it in items:
        q = it["q"] if isinstance(it["q"], dict) else bi(it["q"], it["q"])
        opts = [o if isinstance(o, dict) else bi(o, o) for o in it["options"]]
        rev = it.get("reveal", "")
        rev = rev if isinstance(rev, dict) else bi(rev, rev)
        about = it.get("about", "both")
        qs.append(mc("custom", q, opts, int(it["answer"]), rev, about=about))
    return qs


# ----------------------------------------------------------------------------
# Encryption (optional): PBKDF2-SHA256 + AES-256-GCM, decrypted in the browser
# ----------------------------------------------------------------------------

PBKDF2_ITERATIONS = 300_000


class AES:
    """Minimal AES-256 block cipher (encrypt only), enough for GCM. Pure stdlib."""
    _sbox = None

    @classmethod
    def _init_tables(cls):
        if cls._sbox:
            return
        sbox = [0] * 256
        p = q = 1
        while True:
            # multiply p by 3, divide q by 3 in GF(2^8)
            p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
            q ^= q << 1; q ^= q << 2; q ^= q << 4; q &= 0xFF
            if q & 0x80:
                q ^= 0x09
            x = q ^ (q << 1) ^ (q << 2) ^ (q << 3) ^ (q << 4)
            sbox[p] = (x ^ (x >> 8) ^ 0x63) & 0xFF
            if p == 1:
                break
        sbox[0] = 0x63
        cls._sbox = sbox
        cls._xtime = [((b << 1) ^ 0x1B) & 0xFF if b & 0x80 else b << 1 for b in range(256)]

    def __init__(self, key: bytes):
        self._init_tables()
        assert len(key) == 32
        nk, rounds = 8, 14
        w = [list(key[i:i + 4]) for i in range(0, 32, 4)]
        rcon = 1
        for i in range(nk, 4 * (rounds + 1)):
            t = list(w[i - 1])
            if i % nk == 0:
                t = t[1:] + t[:1]
                t = [self._sbox[b] for b in t]
                t[0] ^= rcon
                rcon = self._xtime[rcon]
            elif i % nk == 4:
                t = [self._sbox[b] for b in t]
            w.append([w[i - nk][j] ^ t[j] for j in range(4)])
        self._rk = [sum(w[4 * r:4 * r + 4], []) for r in range(rounds + 1)]
        self._rounds = rounds

    def encrypt_block(self, block: bytes) -> bytes:
        sbox, xt = self._sbox, self._xtime
        s = [block[i] ^ self._rk[0][i] for i in range(16)]
        for r in range(1, self._rounds + 1):
            s = [sbox[b] for b in s]
            # shift rows (state is column-major: index = 4*col + row)
            s = [s[(4 * ((i // 4) + (i % 4)) + (i % 4)) % 16] for i in range(16)]
            if r != self._rounds:
                out = []
                for c in range(4):
                    a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                    t = a0 ^ a1 ^ a2 ^ a3
                    out += [a0 ^ t ^ xt[a0 ^ a1], a1 ^ t ^ xt[a1 ^ a2], a2 ^ t ^ xt[a2 ^ a3], a3 ^ t ^ xt[a3 ^ a0]]
                s = out
            rk = self._rk[r]
            s = [s[i] ^ rk[i] for i in range(16)]
        return bytes(s)


def _gf128_mul(x: int, y: int) -> int:
    r = 0
    for i in range(127, -1, -1):
        if (y >> i) & 1:
            r ^= x
        x = (x >> 1) ^ (0xE1 << 120) if x & 1 else x >> 1
    return r


def aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-256-GCM with a 96-bit IV and no AAD. Returns ciphertext || 16-byte tag."""
    aes = AES(key)
    h = int.from_bytes(aes.encrypt_block(bytes(16)), "big")
    j0 = iv + b"\x00\x00\x00\x01"
    counter = int.from_bytes(j0, "big")
    out = bytearray()
    for i in range(0, len(plaintext), 16):
        counter = (counter & ~0xFFFFFFFF) | ((counter + 1) & 0xFFFFFFFF)
        ks = aes.encrypt_block(counter.to_bytes(16, "big"))
        chunk = plaintext[i:i + 16]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
    # GHASH over ciphertext (no AAD), then the length block
    tag = 0
    for i in range(0, len(out), 16):
        block = bytes(out[i:i + 16]).ljust(16, b"\x00")
        tag = _gf128_mul(tag ^ int.from_bytes(block, "big"), h)
    lengths = (0).to_bytes(8, "big") + (len(out) * 8).to_bytes(8, "big")
    tag = _gf128_mul(tag ^ int.from_bytes(lengths, "big"), h)
    tag ^= int.from_bytes(aes.encrypt_block(j0), "big")
    return bytes(out) + tag.to_bytes(16, "big")


def encrypt(plaintext: str, password: str):
    import hashlib
    salt, iv = os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32)
    ct = aes_gcm_encrypt(key, iv, plaintext.encode("utf-8"))
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"enc": True, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct), "iter": PBKDF2_ITERATIONS}


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chat", help="WhatsApp _chat.txt export")
    ap.add_argument("--out", default="dist/index.html")
    ap.add_argument("--template", default="template/index.html")
    ap.add_argument("--custom", default="data/custom_questions.json")
    ap.add_argument("--exclude", default="data/exclude.txt",
                    help="one word/phrase per line; messages containing them are never quoted")
    ap.add_argument("--rename", action="append", default=[], help='"raw name=Display name"')
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--password", default=None,
                    help="encrypt the question data; the page asks for this passphrase on open "
                         "(use when publishing the page anywhere public)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    EXCLUDE.extend(load_exclude(args.exclude))
    msgs = parse(Path(args.chat))
    counts = collections.Counter(m["sender"] for m in msgs)
    names = [n for n, _ in counts.most_common(2)]
    if len(names) != 2:
        raise SystemExit("Expected a chat with exactly two people")
    msgs = [m for m in msgs if m["sender"] in names]

    display = {}
    for n in names:
        clean = re.sub(r"[^\w\s'ʼ-]", "", n, flags=re.UNICODE).strip()
        clean = re.sub(r"\s+", " ", clean) or n
        display[n] = clean[0].upper() + clean[1:]
    for r in args.rename:
        raw, disp = r.split("=", 1)
        for n in names:
            if n == raw.strip() or display[n].lower() == raw.strip().lower():
                display[n] = disp.strip()

    stats, wc = stats_questions(rng, msgs, names, display)
    pools = {
        "stats": stats,
        "word": whose_word_questions(rng, wc, names, display),
        "who": who_said_questions(rng, msgs, names, display),
        "blank": blank_questions(rng, msgs, names, display, wc),
        "reply": reply_questions(rng, msgs, names, display),
        "day": day_questions(rng, msgs, names, display),
        "emoji": emoji_questions(rng, msgs, names, display),
        "voice": voice_questions(rng, msgs, names, display),
        "speed": reply_time_questions(rng, msgs, names, display),
        "nick": nickname_questions(rng, msgs, names, display, wc),
        "streak": streak_questions(rng, msgs, names, display),
        "custom": load_custom(args.custom, display),
    }
    # 'about' uses raw sender names internally; convert to display names for the app
    for pool in pools.values():
        for q in pool:
            if q["about"] in display:
                q["about"] = display[q["about"]]

    first, last = msgs[0]["ts"], msgs[-1]["ts"]
    data = {
        "players": [display[n] for n in names],
        "meta": {
            "total": len(msgs),
            "from": bi(az_date(first.date()), en_date(first.date())),
            "to": bi(az_date(last.date()), en_date(last.date())),
            "days": (last.date() - first.date()).days + 1,
            "built": dt.date.today().isoformat(),
        },
        "pools": pools,
        "wrapped": wrapped_data(msgs, names, display),
    }
    template = Path(args.template).read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False)
    if args.password:
        payload = json.dumps(encrypt(payload, args.password))
    payload = payload.replace("</", "<\\/")
    html = template.replace("/*__QUIZ_DATA__*/null", payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Players: {data['players']}")
    for k, v in pools.items():
        print(f"  {k:7s} {len(v):4d} questions")
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
