"""Tag the finished file and embed the tracklist as lyrics — format-agnostic (mutagen).

The lyrics embed is the whole point: Navidrome reads LRC out of the standard lyrics
tag (USLT / ©lyr / LYRICS) and serves it as synced lyrics when the lines carry
timestamps. Cover art is embedded here too (we can't rely on yt-dlp's EmbedThumbnail
because AtomicParsley isn't installed for m4a).

Tag scheme (SPEC step 4):
  Album Artist = DJ · Artist = DJ · Album = title · Title = title
  Genre = "DJ Mix" (+ optional music genre) · Date = mix date · Comment = URL + event
"""

from __future__ import annotations

from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .models import MixMeta

_PIC_COVER_FRONT = 3  # ID3/FLAC picture type "Cover (front)"


def _read_cover(cover_path: Path | None) -> bytes | None:
    if cover_path and cover_path.exists():
        try:
            return cover_path.read_bytes()
        except OSError:
            return None
    return None


def _tag_mp4(path: Path, meta: MixMeta, lyrics: str, cover: bytes | None) -> None:
    audio = MP4(str(path))
    audio["\xa9nam"] = [meta.title]
    audio["\xa9ART"] = [meta.dj]
    audio["aART"] = [meta.dj]
    audio["\xa9alb"] = [meta.title]
    audio["\xa9gen"] = meta.genres
    audio["\xa9cmt"] = [meta.comment]
    if meta.date:
        audio["\xa9day"] = [meta.date]
    if lyrics:
        audio["\xa9lyr"] = [lyrics]
    if cover:
        audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()


def _tag_id3(path: Path, meta: MixMeta, lyrics: str, cover: bytes | None) -> None:
    audio = MP3(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    tags.setall("TIT2", [TIT2(encoding=3, text=[meta.title])])
    tags.setall("TPE1", [TPE1(encoding=3, text=[meta.dj])])
    tags.setall("TPE2", [TPE2(encoding=3, text=[meta.dj])])
    tags.setall("TALB", [TALB(encoding=3, text=[meta.title])])
    tags.setall("TCON", [TCON(encoding=3, text=meta.genres)])
    tags.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=[meta.comment])])
    if meta.date:
        tags.setall("TDRC", [TDRC(encoding=3, text=[meta.date])])
    if lyrics:
        tags.setall("USLT", [USLT(encoding=3, lang="eng", desc="", text=lyrics)])
    if cover:
        tags.setall(
            "APIC",
            [APIC(encoding=3, mime="image/jpeg", type=_PIC_COVER_FRONT,
                  desc="Cover", data=cover)],
        )
    audio.save()


def _vorbis_fields(meta: MixMeta, lyrics: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {
        "TITLE": [meta.title],
        "ARTIST": [meta.dj],
        "ALBUMARTIST": [meta.dj],
        "ALBUM": [meta.title],
        "GENRE": meta.genres,
        "COMMENT": [meta.comment],
    }
    if meta.date:
        fields["DATE"] = [meta.date]
    if lyrics:
        # Write both keys Navidrome maps to the lyrics field.
        fields["LYRICS"] = [lyrics]
        fields["UNSYNCEDLYRICS"] = [lyrics]
    return fields


def _flac_picture(cover: bytes) -> Picture:
    pic = Picture()
    pic.type = _PIC_COVER_FRONT
    pic.mime = "image/jpeg"
    pic.desc = "Cover"
    pic.data = cover
    return pic


def _tag_flac(path: Path, meta: MixMeta, lyrics: str, cover: bytes | None) -> None:
    audio = FLAC(str(path))
    audio.update(_vorbis_fields(meta, lyrics))
    if cover:
        audio.clear_pictures()
        audio.add_picture(_flac_picture(cover))
    audio.save()


def _tag_ogg(audio: OggOpus | OggVorbis, meta: MixMeta, lyrics: str,
             cover: bytes | None) -> None:
    audio.update(_vorbis_fields(meta, lyrics))
    if cover:
        import base64

        audio["METADATA_BLOCK_PICTURE"] = [
            base64.b64encode(_flac_picture(cover).write()).decode("ascii")
        ]
    audio.save()


def write_tags(
    path: Path,
    meta: MixMeta,
    lyrics: str,
    *,
    cover_path: Path | None = None,
) -> None:
    """Apply the tag scheme and embed ``lyrics`` into ``path``'s lyrics tag.

    Cover embedding is best-effort; a failure there must not lose the text tags.
    Raises if the container type is unsupported or tags can't be written.
    """
    cover = _read_cover(cover_path)

    if path.suffix.lower() in {".m4a", ".mp4", ".m4b", ".aac"}:
        _tag_mp4(path, meta, lyrics, cover)
        return
    if path.suffix.lower() in {".mp3"}:
        _tag_id3(path, meta, lyrics, cover)
        return
    if path.suffix.lower() in {".flac"}:
        _tag_flac(path, meta, lyrics, cover)
        return
    if path.suffix.lower() in {".opus", ".ogg", ".oga"}:
        audio = mutagen.File(str(path))
        if isinstance(audio, (OggOpus, OggVorbis)):
            _tag_ogg(audio, meta, lyrics, cover)
            return

    # Fall back to mutagen's type detection for anything else.
    audio = mutagen.File(str(path))
    if isinstance(audio, MP4):
        _tag_mp4(path, meta, lyrics, cover)
    elif isinstance(audio, MP3):
        _tag_id3(path, meta, lyrics, cover)
    elif isinstance(audio, FLAC):
        _tag_flac(path, meta, lyrics, cover)
    elif isinstance(audio, (OggOpus, OggVorbis)):
        _tag_ogg(audio, meta, lyrics, cover)
    else:
        raise ValueError(f"Unsupported audio container for tagging: {path.name}")
