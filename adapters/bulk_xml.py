"""Phase 4b/4c: Grants.gov's daily full-database bulk XML export.

Phase 0b confirmed (docs/PROBES.md) that `https://www.grants.gov/xml-extract`
is itself a JS-rendered page, but its rendered HTML embeds direct S3 links
to the last several days of dated `.zip` extracts, e.g.:

    https://prod-grants-gov-chatbot.s3.amazonaws.com/extracts/GrantsDBExtract20260905v2.zip

Confirmed live: ~74.5 MiB compressed, ~306 MiB uncompressed single XML file
of `<OpportunitySynopsisDetail_1_0>` records. Given that size, this module
never materializes the full parsed document -- it streams the ZIP entry
directly into `xml.etree.ElementTree.iterparse` and calls `elem.clear()`
after reading each record, so peak memory stays proportional to one record,
not the whole file. Non-matching records are discarded immediately.

Expected yield is low (most Grants.gov opportunities are institutional, not
something a solo undergraduate can apply to directly) but the download is
cheap relative to what it is -- and the eligibility columns (lib/eligibility.py)
filter out what shouldn't have been surfaced without needing a perfectly
precise keyword filter here.

Download the current day's URL by re-fetching the xml-extract page and
taking the most recent dated `.zip` link found there, rather than
constructing today's date client-side -- the page lists the last several
days, which tolerates this job running a little early/late relative to
when GSA actually publishes a given day's file.
"""

import io
import re
import zipfile
from datetime import date, datetime
from xml.etree import ElementTree as ET

from ._http import get

XML_EXTRACT_PAGE = "https://www.grants.gov/xml-extract"
_ZIP_LINK = re.compile(r"https?://[^\s\"'<>]*GrantsDBExtract(\d{8})v\d+\.zip")

# Keyword filter over each opportunity's own title+description -- kept
# broad (this source's own trust:low / low-yield-by-design framing, plus
# the eligibility columns downstream, do the precision work) but still a
# real filter, not "return everything."
ROBOTICS_KEYWORDS = (
    "robot", "robotics", "robotic", "unmanned", "autonomous vehicle", "uav",
    "drone", "mechatronic", "prosthetic", "exoskeleton", "manipulator arm",
)


def _discover_latest_zip_url():
    resp = get(XML_EXTRACT_PAGE)
    matches = _ZIP_LINK.findall(resp.text)
    if not matches:
        return None
    # Pick the URL with the latest YYYYMMDD date token.
    urls = _ZIP_LINK.finditer(resp.text)
    best_url, best_date = None, None
    for m in urls:
        date_str = m.group(1)
        try:
            d = datetime.strptime(date_str, "%Y%m%d")
        except ValueError:
            continue
        if best_date is None or d > best_date:
            best_date, best_url = d, m.group(0)
    return best_url


def _strip_ns(tag):
    return tag.split("}")[-1]


def _mmddyyyy_to_iso(raw):
    if not raw or len(raw) != 8:
        return None
    try:
        return datetime.strptime(raw, "%m%d%Y").date().isoformat()
    except ValueError:
        return None


def _record_to_item(fields: dict, today=None):
    today = today or date.today()
    title = (fields.get("OpportunityTitle") or "").strip()
    opp_id = (fields.get("OpportunityID") or "").strip()
    if not title or not opp_id:
        return None
    description = fields.get("Description") or ""
    haystack = f"{title} {description}".lower()
    if not any(k in haystack for k in ROBOTICS_KEYWORDS):
        return None

    deadline_iso = _mmddyyyy_to_iso(fields.get("CloseDate"))

    # The bulk export is a full historical archive, not just currently-open
    # opportunities -- most CloseDate values are years in the past. Skip
    # anything with a stated deadline that has already passed; a record
    # with no CloseDate at all (rolling) is kept.
    if deadline_iso and deadline_iso < today.isoformat():
        return None

    url = f"https://www.grants.gov/search-results-detail/{opp_id}"
    snippet = description.strip()[:300]
    money = fields.get("AwardCeiling") or fields.get("EstimatedTotalProgramFunding")
    money_raw = f"${money}" if money else None

    prefilled = {
        "deadline_date": deadline_iso,
        "deadline_confidence": "explicit" if deadline_iso else "none",
        "money_raw": money_raw,
    }
    return {"title": title, "url": url, "snippet": snippet, "prefilled": prefilled}


def parse_bulk_xml_stream(fileobj) -> list:
    """Streams `<OpportunitySynopsisDetail_1_0>` records out of a
    file-like object (never loading the whole document), yielding matching
    items only. Malformed individual records are skipped, not raised --
    consistent with this project's "best-effort enrichment" convention."""
    today = date.today()
    items = []
    context = ET.iterparse(fileobj, events=("end",))
    for _event, elem in context:
        if _strip_ns(elem.tag) != "OpportunitySynopsisDetail_1_0":
            continue
        fields = {_strip_ns(child.tag): child.text for child in elem}
        try:
            item = _record_to_item(fields, today=today)
        except Exception:
            item = None
        if item:
            items.append(item)
        elem.clear()
    return items


def fetch(source: dict) -> list:
    zip_url = _discover_latest_zip_url()
    if not zip_url:
        raise ValueError("bulk_xml adapter: could not discover a current GrantsDBExtract zip URL")

    resp = get(zip_url, timeout=source.get("timeout", 120))
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise ValueError("bulk_xml adapter: no .xml file found inside the downloaded zip")
        with zf.open(names[0]) as f:
            return parse_bulk_xml_stream(f)
