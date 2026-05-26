from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import json

from .utils import parse_song_id


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def import_xlsx_rows(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as zf:
        shared_strings = read_shared_strings(zf)
        sheet_path = first_sheet_path(zf)
        root = ET.fromstring(zf.read(sheet_path))

    rows: list[dict] = []
    for row in root.findall(".//main:sheetData/main:row", NS):
        row_number = int(row.attrib.get("r", "0") or 0)
        if row_number < 2:
            continue

        values = {}
        for cell in row.findall("main:c", NS):
            ref = cell.attrib.get("r", "")
            column = re.sub(r"\d+", "", ref)
            values[column] = cell_value(cell, shared_strings)

        song_url = values.get("B", "").strip()
        song_id = parse_song_id(song_url)
        if not song_url or not song_id:
            continue

        rows.append({
            "source_row": row_number,
            "song_name": values.get("A", "").strip(),
            "song_url": song_url,
            "singer_name": values.get("C", "").strip(),
            "lyrics": values.get("D", "").strip(),
            "song_id": song_id,
        })

    return rows


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("main:si", NS):
        parts = [node.text or "" for node in item.findall(".//main:t", NS)]
        strings.append("".join(parts))
    return strings

def import_input_rows(path: Path) -> list[dict]:

    suffix = path.suffix.lower()

    if suffix == ".xlsx":
        return import_xlsx_rows(path)

    if suffix == ".json":
        return import_json_rows(path)

    if suffix == ".jsonl":
        return import_jsonl_rows(path)

    raise ValueError(f"Unsupported input format: {suffix}")

def import_json_rows(path: Path) -> list[dict]:

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = []

    for idx, item in enumerate(data, start=1):

        song_url = item.get("song_url", "")
        if not song_url and isinstance(item.get("extra_info"), dict):
            song_url = item["extra_info"].get("page_links", "")
        song_url = song_url.strip()

        song_id = parse_song_id(song_url)

        if not song_url or not song_id:
            continue

        song_name = item.get("song_name", "")
        if not song_name:
            song_name = item.get("music_name", "")
        song_name = song_name.strip()

        singer_name = item.get("singer_name", "")
        if not singer_name and isinstance(item.get("extra_info"), dict):
            singer_name = item["extra_info"].get("artist", "")
        singer_name = singer_name.strip()

        lyrics = item.get("lyrics", "")
        if not lyrics:
            lyrics = item.get("lyric_text", "")
        lyrics = lyrics.strip()

        rows.append({
            "source_row": idx,
            "song_name": song_name,
            "song_url": song_url,
            "singer_name": singer_name,
            "lyrics": lyrics,
            "song_id": song_id,
        })

    return rows

def import_jsonl_rows(path: Path) -> list[dict]:

    rows = []

    with open(path, "r", encoding="utf-8") as f:

        for idx, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception as e:
                print(f"[Warning] Line {idx} has invalid JSON, skipped: {e}")
                continue

            song_url = item.get("song_url", "")
            if not song_url and isinstance(item.get("extra_info"), dict):
                song_url = item["extra_info"].get("page_links", "")
            song_url = song_url.strip()

            song_id = parse_song_id(song_url)

            if not song_url or not song_id:
                continue

            song_name = item.get("song_name", "")
            if not song_name:
                song_name = item.get("music_name", "")
            song_name = song_name.strip()

            singer_name = item.get("singer_name", "")
            if not singer_name and isinstance(item.get("extra_info"), dict):
                singer_name = item["extra_info"].get("artist", "")
            singer_name = singer_name.strip()

            lyrics = item.get("lyrics", "")
            if not lyrics:
                lyrics = item.get("lyric_text", "")
            lyrics = lyrics.strip()

            rows.append({
                "source_row": idx,
                "song_name": song_name,
                "song_url": song_url,
                "singer_name": singer_name,
                "lyrics": lyrics,
                "song_id": song_id,
            })

    return rows

def first_sheet_path(zf: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    first_sheet = workbook.find(".//main:sheets/main:sheet", NS)
    if first_sheet is None:
        raise ValueError("Excel khong co sheet nao.")

    rel_id = first_sheet.attrib.get(f"{{{NS['rel']}}}id")
    for rel in rels:
        if rel.attrib.get("Id") == rel_id:
            target = rel.attrib["Target"]
            return "xl/" + target.lstrip("/")
    return "xl/worksheets/sheet1.xml"


def cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value_node = cell.find("main:v", NS)
    inline_node = cell.find(".//main:is/main:t", NS)

    if inline_node is not None:
        return inline_node.text or ""
    if value_node is None:
        return ""

    raw = value_node.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return ""
    return raw
