from __future__ import annotations

import re
import unicodedata
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping


_FULL_NAME = re.compile(
    r"\b[A-Z][A-Za-z'’\-]+"
    r"(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z'’\-]+))*"
    r"\s+[A-Z][A-Za-z'’\-]+(?:\s+(?:Jr|Sr)\.?)?"
)
_HONORIFIC = re.compile(r"^(?:Messrs|Mses|Mr|Ms|Mrs|Dr)\.\s+", re.IGNORECASE)
LEGACY_SURNAME_OVERRIDES = {
    "barron": "Patrick K. Barron",
    "santomero": "Anthony M. Santomero",
    "stone": "William H. Stone Jr.",
}


def participant_id_for_name(display_name: str) -> str:
    normalized = unicodedata.normalize("NFKD", display_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    identifier = re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")
    if not identifier:
        raise ValueError(f"Could not derive participant_id from {display_name!r}")
    return identifier


def _attendance_start(block: list[str]) -> bool:
    if not block:
        return False
    heading = block[0].strip().rstrip(":").casefold()
    return heading in {"present", "attendance"}


def _resolve_display_name(
    display_name: str,
    surname_resolver: Mapping[str, str] | None,
) -> str:
    cleaned = _HONORIFIC.sub("", display_name.strip()).strip()
    if " " not in cleaned:
        resolved = (surname_resolver or {}).get(cleaned.rstrip(".").casefold())
        if not resolved:
            raise ValueError(f"Unresolved attendance surname: {display_name}")
        return resolved
    surname = cleaned.rstrip(".").split()[-1].casefold()
    resolved = (surname_resolver or {}).get(surname)
    if resolved:
        same_first = cleaned.split()[0].casefold() == resolved.split()[0].casefold()
        same_last = (
            cleaned.rstrip(".").split()[-1].casefold()
            == resolved.rstrip(".").split()[-1].casefold()
        )
        if same_first and same_last:
            return resolved
    return cleaned


def _named_participant(
    display_name: str,
    role: str,
    surname_resolver: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    display_name = _resolve_display_name(display_name, surname_resolver)
    if not _FULL_NAME.fullmatch(display_name):
        raise ValueError(f"Unsupported attendance name: {display_name}")
    return {
        "participant_id": participant_id_for_name(display_name),
        "display_name": display_name,
        "role": role,
        "is_chair": role == "chair",
    }


def parse_policy_attendance(
    blocks: Iterable[list[str]],
    *,
    surname_resolver: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    block_list = list(blocks)
    start_index = next(
        (index for index, block in enumerate(block_list) if _attendance_start(block)),
        None,
    )
    if start_index is None:
        raise ValueError("Minutes contain no PRESENT or Attendance block")
    member_block = block_list[start_index]
    if len(member_block) == 1:
        if start_index + 1 >= len(block_list):
            raise ValueError("Attendance heading has no participant block")
        member_lines = block_list[start_index + 1]
        following_index = start_index + 2
    else:
        member_lines = member_block[1:]
        following_index = start_index + 1
    participants = []
    for line in member_lines:
        lowered_line = line.casefold()
        if "alternate members of" in lowered_line:
            prefix = re.split(
                r",\s*\d*\s*Alternate Members of",
                line,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            participants.extend(
                _named_participant(name, "alternate_member", surname_resolver)
                for name in _names_from_group(prefix, surname_resolver)
            )
            continue
        if "presidents of the federal reserve banks" in lowered_line:
            prefix = re.split(
                r",\s*\d*\s*Presidents of the Federal Reserve Banks",
                line,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            participants.extend(
                _named_participant(
                    name,
                    "reserve_bank_president",
                    surname_resolver,
                )
                for name in _names_from_group(prefix, surname_resolver)
            )
            continue
        display_name, separator, title = line.partition(",")
        normalized_title = title.strip().casefold() if separator else ""
        if normalized_title in {"chair", "chairman"}:
            role = "chair"
        elif normalized_title == "vice chair":
            role = "vice_chair"
        else:
            role = "member"
        participants.append(
            _named_participant(display_name.strip(), role, surname_resolver)
        )

    for block in block_list[following_index:]:
        text = " ".join(block)
        lowered = text.casefold()
        if any(
            marker in lowered
            for marker in (", secretary", ", general counsel", ", economist")
        ):
            break
        if "alternate members of" in lowered:
            prefix = re.split(
                r",\s*\d*\s*Alternate Members of",
                text,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            participants.extend(
                _named_participant(name, "alternate_member", surname_resolver)
                for name in _names_from_group(prefix, surname_resolver)
            )
        elif "presidents of the federal reserve banks" in lowered:
            prefix = re.split(
                r",\s*\d*\s*Presidents of the Federal Reserve Banks",
                text,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            participants.extend(
                _named_participant(name, "reserve_bank_president", surname_resolver)
                for name in _names_from_group(prefix, surname_resolver)
            )

    unique = {}
    for participant in participants:
        participant_id = participant["participant_id"]
        if participant_id in unique:
            raise ValueError(
                f"Duplicate policy participant in attendance: {participant['display_name']}"
            )
        unique[participant_id] = participant
    chair_count = sum(item["is_chair"] for item in unique.values())
    if chair_count != 1:
        raise ValueError(f"Attendance must contain exactly one Chair, got {chair_count}")
    return list(unique.values())


def _names_from_group(
    value: str,
    surname_resolver: Mapping[str, str] | None,
) -> list[str]:
    cleaned = re.sub(
        r"\b(?:Messrs|Mses|Mr|Ms|Mrs|Dr)\.\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r",?\s*\d+\b", "", cleaned)
    cleaned = re.sub(r",?\s+and\s+", ",", cleaned, flags=re.IGNORECASE)
    names = []
    for token in cleaned.split(","):
        candidate = token.strip()
        if not candidate:
            continue
        try:
            names.append(_resolve_display_name(candidate, surname_resolver))
        except ValueError:
            matches = _FULL_NAME.findall(candidate)
            if len(matches) == 1:
                names.append(matches[0])
            else:
                raise
    if not names:
        raise ValueError(f"No attendance names in group: {value}")
    return names


class _AttendanceVisibleLineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active = False
        self.parts: list[str] = []
        self.lines: list[str] = []

    def _flush(self) -> None:
        normalized = " ".join("".join(self.parts).replace("\xa0", " ").split())
        if normalized:
            self.lines.append(normalized)
        self.parts = []

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if not self.active and normalized.rstrip(":").casefold() in {
            "present",
            "attendance",
        }:
            self.active = True
            self.lines.append(normalized)
            return
        if self.active:
            self.parts.append(data)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.active and tag.lower() == "br":
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if self.active and tag.lower() in {"p", "td", "tr"}:
            self._flush()


def extract_legacy_attendance_blocks(content: bytes) -> list[list[str]]:
    parser = _AttendanceVisibleLineParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    parser._flush()
    if not parser.lines:
        raise ValueError("Minutes contain no visible PRESENT or Attendance marker")
    member_lines = []
    following = []
    for line in parser.lines[1:]:
        lowered = line.casefold()
        if any(
            marker in lowered
            for marker in (", secretary", ", general counsel", ", economist")
        ):
            break
        if "alternate members of" in lowered or "presidents of the federal reserve banks" in lowered:
            following.append([line])
        elif line.startswith(("Mr.", "Ms.", "Mrs.", "Dr.")):
            member_lines.append(line)
    if not member_lines:
        raise ValueError("Visible attendance marker has no policy member lines")
    return [[parser.lines[0]], member_lines, *following]


def parse_policy_attendance_html(
    content: bytes,
    *,
    surname_resolver: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    from decision_memory.fed_documents import extract_html_paragraph_line_blocks

    blocks = extract_html_paragraph_line_blocks(content)
    if any(_attendance_start(block) for block in blocks):
        return parse_policy_attendance(blocks, surname_resolver=surname_resolver)
    return parse_policy_attendance(
        extract_legacy_attendance_blocks(content),
        surname_resolver=surname_resolver,
    )


def audit_roster_manifest(manifest_path: Path) -> dict[str, Any]:
    from decision_memory.fed_documents import extract_html_paragraph_line_blocks

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    minute_documents = [
        item
        for item in manifest.get("documents", [])
        if item.get("document_type") == "minutes"
    ]
    parsed = []
    errors = []
    for item in minute_documents:
        try:
            participants = parse_policy_attendance(
                extract_html_paragraph_line_blocks(
                    Path(item["local_path"]).read_bytes()
                )
            )
            parsed.append((item["meeting_id"], participants))
        except Exception as error:
            errors.append(
                {
                    "meeting_id": item.get("meeting_id"),
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    return {
        "minute_document_count": len(minute_documents),
        "parsed_meeting_count": len(parsed),
        "meeting_participant_count": sum(len(items) for _, items in parsed),
        "unique_participant_count": len(
            {
                item["participant_id"]
                for _, participants in parsed
                for item in participants
            }
        ),
        "chairs": sorted(
            {
                item["display_name"]
                for _, participants in parsed
                for item in participants
                if item["is_chair"]
            }
        ),
        "errors": errors,
    }
