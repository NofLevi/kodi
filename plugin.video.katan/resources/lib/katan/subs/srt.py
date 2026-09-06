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
    """Parse SRT text into cues, tolerating the usual real-world damage."""
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and text[0] == u"\ufeff":
        text = text[1:]

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


def looks_hebrew(cues, threshold=0.15):
    """Is this actually Hebrew, or an English file mislabelled as Hebrew?"""
    if not cues:
        return False
    hebrew = total = 0
    for cue in cues[:200]:
        for char in cue.text:
            if char.isalpha():
                total += 1
                if u"\u0590" <= char <= u"\u05EA":
                    hebrew += 1
    return total > 0 and (float(hebrew) / total) >= threshold


def duration(cues):
    return cues[-1].end - cues[0].start if cues else 0.0
