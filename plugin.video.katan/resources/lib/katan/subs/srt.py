"""Reading, writing and repairing subtitle files.

Only SRT is produced. Kodi renders it everywhere, it is trivial to re-time, and
it costs nothing to parse compared with the styling formats.
"""
import io
import os
import re

_TIME = re.compile(
    r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})")

# Presentation-form Arabic and Hebrew letters that most subtitle fonts cannot
# draw. They arrive from badly converted sources and render as empty boxes.
_PRESENTATION_FORMS = re.compile(u"[\uFB1D-\uFDFF\uFE70-\uFEFF]")
_TAGS = re.compile(r"</?[a-zA-Z][^>]*>")
_HI_BRACKETS = re.compile(r"[\[\(][^\]\)]{0,60}[\]\)]")

MIN_DURATION = 0.4
MAX_DURATION = 8.0


class Cue(object):
    """One subtitle entry. Times are seconds as floats."""

    __slots__ = ("index", "start", "end", "text")

    def __init__(self, index, start, end, text):
        self.index = index
        self.start = float(start)
        self.end = float(end)
        self.text = text

    @property
    def duration(self):
        return max(0.0, self.end - self.start)

    def shifted(self, offset, scale=1.0):
        return Cue(self.index, self.start * scale + offset,
                   self.end * scale + offset, self.text)

    def __repr__(self):
        return "Cue(%d, %.3f, %.3f, %r)" % (self.index, self.start, self.end,
                                            self.text[:24])


def _to_seconds(hours, minutes, seconds, fraction):
    fraction = (fraction + "00")[:3]
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction) / 1000.0


def _to_timestamp(value):
    if value < 0:
        value = 0.0
    milliseconds = int(round(value * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return "%02d:%02d:%02d,%03d" % (hours, minutes, seconds, milliseconds)


def parse(text):
    """Parse SRT text into cues, tolerating the usual real-world damage.

    SubStation Alpha is parsed here too, because refusing it threw away real
    subtitles: measured over a survey, **every** download that failed to parse
    was SSA, and half of what OpenSubtitles returns for Overlord is. It is the
    house format for anime, which is the part of the catalogue that can least
    afford to lose a provider.
    """
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and text[0] == u"\ufeff":
        text = text[1:]

    if _looks_like_ssa(text):
        return _parse_ssa(text)

    cues = []
    index = 0
    lines = text.split("\n")
    position = 0
    while position < len(lines):
        match = _TIME.search(lines[position])
        if not match:
            position += 1
            continue
        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        position += 1
        body = []
        while position < len(lines) and lines[position].strip() != "":
            body.append(lines[position])
            position += 1
        index += 1
        cues.append(Cue(index, start, end, "\n".join(body).strip()))
    return cues


_SSA_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})[.,](\d{1,3})$")


def _looks_like_ssa(text):
    head = text[:2000].lower()
    return "[script info]" in head or "dialogue:" in head


def _ssa_seconds(value):
    """SSA writes 0:01:02.34 - one-digit hours and centiseconds."""
    match = _SSA_TIME.match((value or "").strip())
    if not match:
        return None
    hours, minutes, seconds, fraction = match.groups()
    # Two digits is centiseconds, three is milliseconds. Guessing wrong here
    # shifts every cue by up to a second, which is the difference between a
    # subtitle that fits and one that does not.
    divisor = 100.0 if len(fraction) <= 2 else 1000.0
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            + int(fraction) / divisor)


def _parse_ssa(text):
    """SubStation Alpha, which is what anime subtitles usually are.

    The [Events] section declares its own column order in a Format: line, so
    the positions of Start, End and Text are read rather than assumed - some
    files carry extra columns and hard-coding index 9 gets the style name.
    """
    cues = []
    fields = ["marked", "start", "end", "style", "name", "marginl", "marginr",
              "marginv", "effect", "text"]
    index = 0
    for line in text.split("\n"):
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("format:") and cues == [] :
            names = [part.strip().lower()
                     for part in stripped.split(":", 1)[1].split(",")]
            if "start" in names and "end" in names and "text" in names:
                fields = names
            continue
        if not lowered.startswith("dialogue:"):
            continue
        # Text is last and may itself contain commas, so the split is capped
        # at the number of columns before it.
        parts = stripped.split(":", 1)[1].split(",", len(fields) - 1)
        if len(parts) < len(fields):
            continue
        row = dict(zip(fields, parts))
        start = _ssa_seconds(row.get("start"))
        end = _ssa_seconds(row.get("end"))
        if start is None or end is None:
            continue
        body = _strip_ssa(row.get("text") or "")
        if not body:
            continue
        index += 1
        cues.append(Cue(index, start, end, body))
    cues.sort(key=lambda cue: cue.start)
    return cues


def _strip_ssa(text):
    """Drop the override tags and turn SSA line breaks into real ones."""
    text = re.sub(r"\{[^}]*\}", "", text)
    # SSA writes a hard line break as a literal backslash-N, and a soft one as
    # backslash-n. Both are two characters in the file, not an escape.
    text = text.replace("\\N", "\n").replace("\\n", "\n")
    text = text.replace("\\h", " ")
    return text.strip()


def dump(cues):
    """Render cues back to SRT text, renumbering as it goes."""
    parts = []
    for number, cue in enumerate(cues, 1):
        parts.append("%d\n%s --> %s\n%s\n\n"
                     % (number, _to_timestamp(cue.start),
                        _to_timestamp(cue.end), cue.text))
    return "".join(parts)


def read(path):
    """Read a subtitle file, guessing its encoding the way real files need."""
    with io.open(path, "rb") as handle:
        raw = handle.read()
    return parse(decode(raw))


def decode(raw):
    """Decode subtitle bytes.

    Hebrew subtitles in the wild are usually cp1255 or UTF-8. Trying UTF-8
    first and cp1255 second gets almost everything; chardet is used only when
    both fail, because it is slow.
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1255", "cp1256", "iso-8859-8"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        import chardet
        guess = chardet.detect(raw)
        if guess and guess.get("encoding"):
            return raw.decode(guess["encoding"], "replace")
    except Exception:
        pass
    return raw.decode("utf-8", "replace")


def write(path, cues):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump(cues))
    return path


# --------------------------------------------------------------------------
# clean-up
# --------------------------------------------------------------------------


def clean(cues, strip_hi=False):
    """Repair the defects that make subtitles look broken on screen."""
    out = []
    for cue in cues:
        text = _PRESENTATION_FORMS.sub("", cue.text)
        text = _TAGS.sub("", text)
        if strip_hi:
            text = _HI_BRACKETS.sub("", text)
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not text:
            continue
        out.append(Cue(cue.index, cue.start, cue.end, text))
    return clamp_durations(out)


def clamp_durations(cues):
    """Keep every cue on screen long enough to read and short enough to leave.

    Badly converted files contain zero-length cues and cues that last minutes;
    both look like a bug to the viewer.
    """
    out = []
    ordered = sorted(cues, key=lambda c: c.start)
    for position, cue in enumerate(ordered):
        start = max(0.0, cue.start)
        end = cue.end
        if end <= start:
            end = start + MIN_DURATION
        if end - start > MAX_DURATION:
            end = start + MAX_DURATION
        if position + 1 < len(ordered):
            next_start = ordered[position + 1].start
            if end > next_start > start:
                end = next_start - 0.01
        out.append(Cue(position + 1, start, end, cue.text))
    return out


# Where each script lives in Unicode. Latin is last and deliberately widest,
# because it is the fallback rather than a claim: a file with no Hebrew, no
# Cyrillic and no kana is probably English, and "probably English" is all
# anybody needs from it.
_SCRIPTS = (
    ("he", ((0x0590, 0x05F4),)),
    ("ar", ((0x0600, 0x06FF), (0x0750, 0x077F))),
    ("ru", ((0x0400, 0x04FF),)),
    ("el", ((0x0370, 0x03FF),)),
    # Kana is what separates Japanese from Chinese; both share the Han block,
    # so Han alone is not evidence of either.
    ("ja", ((0x3040, 0x30FF),)),
    ("ko", ((0xAC00, 0xD7A3), (0x1100, 0x11FF))),
    ("zh", ((0x4E00, 0x9FFF),)),
    ("en", ((0x0041, 0x024F),)),
)


def detect_script(cues, sample=200, threshold=0.15):
    """Which script this subtitle is actually written in, or "".

    Not which language - a script cannot tell French from Italian - but it can
    tell Hebrew from English, which is the mistake that matters here: a
    mislabelled upload is common, and applying one silently gives the viewer
    the wrong language with no clue why.

    Returns the first script holding at least `threshold` of the letters, in
    the order above, so Latin only wins when nothing else did.
    """
    if not cues:
        return ""
    counts = {}
    total = 0
    for cue in cues[:sample]:
        for char in cue.text:
            if not char.isalpha():
                continue
            total += 1
            point = ord(char)
            for code, ranges in _SCRIPTS:
                if any(low <= point <= high for low, high in ranges):
                    counts[code] = counts.get(code, 0) + 1
                    break
    if not total:
        return ""
    for code, _ranges in _SCRIPTS:
        if float(counts.get(code, 0)) / total >= threshold:
            return code
    return ""


def looks_hebrew(cues, threshold=0.15):
    """Is this actually Hebrew, or an English file mislabelled as Hebrew?"""
    return detect_script(cues, threshold=threshold) == "he"


def duration(cues):
    return cues[-1].end - cues[0].start if cues else 0.0
