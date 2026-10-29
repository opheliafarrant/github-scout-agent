"""Deterministic evidence rules: (ledger, final text) -> findings.

Everything here is pure: no network, no model, no tool access. The rules answer
one question — does the report claim something this session never fetched? — and
they answer it from the evidence ledger plus the cheaper structural heuristics
that free-form prose still needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict

from .evidence_ledger import README_FETCHED_STATUSES, EvidenceLedger


_GROUNDING_GAP_PREFIX = "unverified candidate claimed as match or recommendation: "
_UNATTRIBUTED_GAP_PREFIX = "verdict asserted without an investigated repository: "
_ZERO_RESULT_GAP_PREFIX = (
    "declared zero qualifying candidates without fetching a single README: "
)
_ZERO_RESULT_RE = re.compile(
    r"零个\s*verified match|没有符合条件|无合格候选|没有任何仓库符合|未找到符合条件"
    r"|no qualifying candidates?|no candidates? qualif(?:y|ies)"
    r"|no repositor(?:y|ies) qualifies?|zero verified",
    re.IGNORECASE,
)
_IMMATURE_GAP_PREFIX = "immature repository recommended without a maturity caveat: "
_UNVERIFIED_RECOMMENDATION_GAP_PREFIX = (
    "recommended from search metadata without README evidence: "
)
_README_CLAIM_GAP_PREFIX = "claimed README evidence that was never fetched in this session"
# A mention of "README" is not a claim: live reports said "需 README 验证确认是否
# 应排除" (a to-do) and "而非 README 验证内容" (a contrast) and were flagged as
# fabrication. Only an assertion about README *content* or *possession* counts.
_README_CLAIM_RE = re.compile(
    r"readme[^.\n]{0,40}?"
    r"(?:shows?|states?|says?|indicates?|confirms?|mentions?|documents?|describes?|lists?"
    r"|contains?|claims?|notes?|includes?|content|text|excerpt|section|return(?:s|ed)"
    r"|(?:was|is|were)\s+(?:fetched|retrieved|read|available|present))"
    r"|(?:per|according\s+to|from|in)\s+the\s+readme"
    r"|README[^。\n]{0,25}?(?:显示|表明|指出|说明|写道|写明|提到|提及|记录|声明|确认|证实|内容|文本|正文|返回)"
    r"|(?:根据|依据|按照|来自)\s*README"
    r"|README\s*(?:已|均)\s*(?:获取|抓取|下载|读取)",
    re.IGNORECASE,
)
# Honest statements that a README was NOT obtained must not be read as a claim
# that it was: the gate exists to catch invented tool output, not confessions.
_README_DISCLOSURE_RE = re.compile(
    r"not\s+(?:been\s+)?(?:checked|fetched|available|retrieved|read)"
    r"|could\s+not|couldn't|unable\s+to|cannot|can't|no\s+readme"
    r"|readme\s+(?:is\s+)?(?:missing|unavailable|unknown)"
    r"|尚未|未能|未按|未做|未进行|未完成|未通过|未经|均未|而非|而不是|自行"
    r"|没有任何|没有(?:完成|做|进行|获取|抓取|读取|检查)"
    r"|not\s+obtained|not\s+executed|no\s+[^.\n]{0,60}?calls?\s+(?:were|was|have\s+been)"
    r"|未取得|未获得|未调用|未执行|没有调用"
    r"|未(?:获取|抓取|检查|读取)|无法(?:获取|读取|访问)|需|须|待",
    re.IGNORECASE,
)
# "对每个通过 README 验证的仓库，查询 /contributors" is next-step advice, not a
# claim that the check already happened: plan and endpoint lines are not evidence.
_README_PLAN_CONTEXT_RE = re.compile(
    r"https?://|next\s+step|to\s+do|action\s+item|端点|建议|下一步|后续|恢复|解除|步骤|行动|计划|自行|分别|再执行|重新执行"
    r"|manuall?y|manual\s+review|action\s+required|hand[- ]?review|手动|请自行|人工"
    r"|建议|下一步|后续|恢复|解除|步骤|行动|计划|自行|分别|再执行|重新执行"
    # "Re-run this investigation with instructions to fetch READMEs" is advice
    # about a future run, not a claim about output this session received.
    r"|re-?run|retry|try\s+again|next\s+steps?|follow[- ]?up|will\s+consume"
    r"|outside\s+(?:this|the)\s+session|重新|再跑|下一轮|需要你|请你",
    re.IGNORECASE,
)
# A claim sitting under "导致无法 / could not / unable:" inherits the negation.
_README_BLOCKED_CONTEXT_RE = re.compile(
    r"无法|未能|不能|没有|could\s+not|unable\s+to|failed\s+to", re.IGNORECASE
)
_EXCLUDED_LINE_RE = re.compile(
    r"exclud|reject|disqualif|not\s+recommended|排除|淘汰|不符合|不适用|落选|未通过",
    re.IGNORECASE,
)
_EXCLUDED_SECTION_RE = re.compile(
    r"exclud|reject|disqualif|not recommended|out of scope|ruled out"
    r"|evidence\s+gaps?|unknowns?|unverified|not\s+verified|pending\s+verification"
    r"|排除|淘汰|不符合|不适用|落选|未通过|无法验证|未验证|证据缺口|未知项|待核实",
    re.IGNORECASE,
)

_IMMATURE_BUCKET_RE = re.compile(
    r"immature options|不建议生产|not recommended for production", re.IGNORECASE
)
_CAVEAT_LABEL_RE = re.compile(
    r"^(?:[-*+]\s*)?(?:\*\*)?(?:maturity|caveat|note|warning|risk|注意|说明|风险|成熟度)",
    re.IGNORECASE,
)
_MATURITY_CAVEAT_RE = re.compile(
    r"immature|早期|新项目|experimental|young|低星|toy|玩具"
    r"|不成熟|未成熟|不满足成熟度|成熟度标准|stars?\s*(?:仅|只|only)|仅\s*\d+\s*stars?|too\s+new|not\s+mature",
    re.IGNORECASE,
)
# One token shape decides what can name a repository anywhere in this module:
# an owner/repo slug or a bare project name. Keeping it in one place means a fix
# to the boundary cannot leave five extractors disagreeing about the same line.
_SUBJECT_TOKEN = r"[A-Za-z][A-Za-z0-9_.-]{1,40}(?:/[A-Za-z0-9_.-]{1,40})?"
_SUBJECT_END = r"(?!://)(?![A-Za-z0-9_.-])"

_IMPERATIVE_CLAIM_RE = re.compile(
    r"^\s{0,3}(?:[-*+]|\d+[.)])\s*\**\s*"
    r"(?:use|try|adopt|consider|check\s+out|look\s+into|switch\s+to|采用|使用|试试|考虑)\b\s*\**\s*"
    rf"({_SUBJECT_TOKEN}){_SUBJECT_END}(?![=`'\"])",
    re.IGNORECASE,
)

_DISCLOSURE_RE = re.compile(
    r"未(?:调查|核实|抓取|审查)|not\s+(?:investigated|inspected|fetched|verified)"
    r"|uninvestigated|unverified",
    re.IGNORECASE,
)

_RECOMMENDATION_SUBJECT_RE = re.compile(
    r"(?<![A-Za-z])(?:推荐|建议|recommend|prefer|adopt)(?![A-Za-z])\s*"
    r"(?:使用|采用|use|using|the|a|an)?\s*\**\s*"
    rf"({_SUBJECT_TOKEN}){_SUBJECT_END}(?!\s*=)",
    re.IGNORECASE,
)

_NEGATED_RECOMMENDATION_RE = re.compile(
    r"(?:不|未|勿)\s*(?:推荐|建议)|(?:never|not)\s+(?:recommend|prefer|adopt)",
    re.IGNORECASE,
)

_GITHUB_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+|\d+[.)]\s+|\*\*[^*]+\*\*\s*$)")
_NUMBERED_IMPERATIVE_RE = re.compile(
    r"^\s{0,3}\d+[.)]\s+\**\s*"
    r"(?:use|try|adopt|consider|check\s+out|look\s+into|switch\s+to|采用|使用|试试|考虑)\b",
    re.IGNORECASE,
)

_RECOMMENDATION_WORD_RE = re.compile(
    r"(?<![不未勿])推荐|(?<![不未勿])建议|(?<![Nn]ever )(?<![Nn]ot )recommend"
    r"|(?<![Nn]ever )(?<![Nn]ot )prefer|(?<![Nn]ever )(?<![Nn]ot )adopt",
    re.IGNORECASE,
)

_RECOMMENDATION_CLAIM_RE = re.compile(
    r"^\s{0,3}(?:[-*+]\s*|\d+[.)]\s*)?(?:\*\*)?\s*(?:最终|综上)?\s*"
    r"(?:推荐|建议|recommend|prefer|adopt)\b",
    re.IGNORECASE,
)

_VERDICT_RE = re.compile(
    r"(?:(?:qualification\s+)?verdict|status|judgement|judgment|判定|结论|状态|资格|评级)"
    r"\s*[:：]?\s*\**\s*"
    r"(?:verified(?:\s+match)?|partial(?:\s+match)?|已验证(?:匹配)?|完全匹配|部分匹配)"
    r"|\b(?:verified|partial)\s+match\b"
    r"|(?<![不未])完全匹配|(?<![不未])部分匹配",
    re.IGNORECASE,
)

_BULLET_SUBJECT_RE = re.compile(
    rf"^\s{{0,3}}(?:[-*+]|\d+[.)])\s*\**\s*({_SUBJECT_TOKEN}){_SUBJECT_END}\**\s*[:：]"
)

_INLINE_SUBJECT_RE = re.compile(
    rf"(?<![A-Za-z0-9_.-])({_SUBJECT_TOKEN}){_SUBJECT_END}\**\s*(?:[:：]\s*)?"
    r"(?:verified\s+match|partial\s+match|完全匹配|部分匹配)",
    re.IGNORECASE,
)

# Structural labels are never repository names: without this, the nested
# "Status: Verified Match" line the prompt itself asks for would be reported as
# a candidate called "Status".
_SUBJECT_LABELS = frozenset({
    "status", "verdict", "qualification", "state", "result", "reason", "note",
    "summary", "conclusion", "judgement", "judgment", "evidence", "analysis",
    "recommendation", "candidate", "name", "repo", "repository", "url", "stars",
    "forks", "language", "license", "public", "archived", "missing", "strengths",
    "limitations", "unknown", "gaps", "budget", "use", "using", "the", "a", "an",
    "this", "that", "it", "these", "those", "candidates", "repositories", "projects",
    "tools", "libraries", "frameworks", "match", "matches", "one", "them",
    # "I recommend the following next steps" names a list, not a repository.
    "following", "below", "above", "next", "such", "further", "step", "steps",
    "options", "choices", "alternatives", "list", "table", "outline", "approach",
})


@dataclass(frozen=True)
class GroundingAudit:
    """What the final text claims that the session never evidenced."""

    ungrounded_repositories: tuple[str, ...] = ()
    unattributed_verdicts: tuple[str, ...] = ()
    # Repositories this session's search returned but never inspected: real
    # metadata, no README. Recommending one is a weaker finding than inventing a
    # project from training memory, so it gets its own channel and its own text.
    unverified_recommendations: tuple[str, ...] = ()

    @property
    def has_findings(self) -> bool:
        return bool(
            self.ungrounded_repositories
            or self.unattributed_verdicts
            or self.unverified_recommendations
        )

    def evidence_gaps(self) -> list[str]:
        return [
            f"{_GROUNDING_GAP_PREFIX}{slug}" for slug in self.ungrounded_repositories
        ] + [
            f"{_UNATTRIBUTED_GAP_PREFIX}{subject}"
            for subject in self.unattributed_verdicts
        ] + [
            f"{_UNVERIFIED_RECOMMENDATION_GAP_PREFIX}{slug}"
            for slug in self.unverified_recommendations
        ]


def _github_slugs(text: str) -> dict[str, str]:
    """Map casefolded `owner/repo` to the slug as written.

    Repository identity comes from GitHub URLs only. Bare `word/word` prose is
    never a slug: treating `GPU/CPU`, `CI/CD` or `REST/GraphQL` as candidates
    downgraded correct reports to partial.
    """

    slugs: dict[str, str] = {}
    for match in _GITHUB_URL_RE.finditer(text or ""):
        slug = f"{match.group(1)}/{match.group(2)}".rstrip(".")
        slugs.setdefault(slug.casefold(), slug)
    return slugs


def _is_heading(line: str) -> bool:
    """Numbered list items that start with a recommendation verb are not headings.

    '1. Use PaddleOCR for tables' opens a numbered recommendation list, while
    '4) Final Recommendation' titles a section; the previous regex treated both
    as headings and skipped the numbered claim entirely.
    """

    return _HEADING_RE.match(line) is not None and _NUMBERED_IMPERATIVE_RE.match(line) is None


def _section_flags(lines: list[str]) -> list[bool]:
    """Mark lines inside a recommendation section, in any language.

    Every heading closes the previous section and a heading that mentions a
    recommendation opens a new one. This is deliberately language agnostic: the
    previous English-only regex let an entire Chinese report pass unaudited.
    """

    flags: list[bool] = []
    in_section = False
    for line in lines:
        if _is_heading(line):
            in_section = bool(_RECOMMENDATION_WORD_RE.search(line))
        flags.append(in_section)
    return flags


def _first_table_cell(line: str) -> str | None:
    cells = line.strip().strip("|").split("|")
    first = cells[0].strip(" *`_") if cells else ""
    return first or None


_ACRONYM_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
# Every pair here was produced by a real report and is prose, not a repository.
_PROSE_PAIRS = frozenset(
    {
        "gpu/cpu",
        "cpu/gpu",
        "ci/cd",
        "cd/ci",
        "rest/graphql",
        "graphql/rest",
        "tcp/udp",
        "udp/tcp",
        "http/https",
        "https/http",
        "client/server",
        "server/client",
        "read/write",
        "write/read",
        "pros/cons",
        "cons/pros",
        "sql/nosql",
        "nosql/sql",
        "json/yaml",
        "yaml/json",
        "html/css",
        "css/html",
        "frontend/backend",
        "backend/frontend",
        "dev/prod",
        "prod/dev",
    }
)


def _looks_like_repository_slug(token: str) -> bool:
    """A slash token that reads as owner/repo rather than as an acronym pair.

    'GPU/CPU', 'CI/CD' and 'REST/GraphQL' are prose; flagging them downgraded
    correct reports. A real slug has at least one part that is not shouting.
    """

    owner, _, repo = token.partition("/")
    if not owner or not repo:
        return False
    if token.casefold() in _PROSE_PAIRS:
        return False
    return not (_ACRONYM_RE.match(owner) and _ACRONYM_RE.match(repo))


def _is_repository_subject(token: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,59}(?:/[A-Za-z0-9_.-]{1,40})?", token) is None:
        return False
    return all(part.casefold() not in _SUBJECT_LABELS for part in token.split("/"))


def _recommendation_subject(line: str) -> str | None:
    for match in _RECOMMENDATION_SUBJECT_RE.finditer(line):
        token = match.group(1)
        if _is_repository_subject(token):
            return token
    return None


def _looks_like_repository_brand(token: str) -> bool:
    """True when a bare word is shaped like a project name rather than a noun.

    A live report closed with "willing to adopt **Nix** as a hard dependency":
    the recommendation verb plus a capitalised word produced an uninvestigated
    repository called "Nix". Prose-only subjects must carry a slash, an inner
    capital, a digit or a separator before they count as repository claims;
    verdict-marked lines keep the looser rule, because there the verdict itself
    is what makes the claim.
    """

    if not token:
        return False
    if any(char in token for char in "-_./0123456789"):
        return True
    return sum(1 for char in token if char.isupper()) >= 2


def _verdict_subject(block: str) -> str | None:
    for line in block.splitlines():
        for pattern in (_BULLET_SUBJECT_RE, _INLINE_SUBJECT_RE):
            match = pattern.search(line)
            if match and _is_repository_subject(match.group(1)):
                return match.group(1)
        if "|" in line and _VERDICT_RE.search(line):
            cell = _first_table_cell(line)
            if cell and _is_repository_subject(cell):
                return cell
    return None


def _starts_list_item(line: str) -> bool:
    """True for a top level markdown bullet, false for an indented child."""

    return bool(line) and line[0] in "-*+" and line[1:2] == " "


def _attached_block(lines: list[str], index: int) -> str:
    """Text of the paragraph/table/cell the line belongs to.

    Attribution follows blank-line boundaries rather than a fixed line window:
    a verdict and the repository it judges live in the same block, while a gap
    note after an empty line is a separate statement and must not inherit the
    verdict above it.
    """

    if lines[index].lstrip().startswith("|"):
        # A markdown table carries no blank line inside it, so paragraph
        # attribution made one "Partial Match" cell turn all twelve rows of a
        # comparison table into verdict claims and reported every repository in
        # the table as an unverified model assumption.
        return lines[index]
    if _starts_list_item(lines[index]) or lines[index].startswith(" "):
        # A candidate list has no blank lines between items either. Each top
        # level bullet owns the indented lines below it and nothing else, so a
        # verdict on one candidate never lends evidence to its siblings.
        block_start = index
        while (
            block_start > 0
            and lines[block_start - 1].strip()
            and not _starts_list_item(lines[block_start - 1])
        ):
            block_start -= 1
        if (
            block_start > 0
            and lines[block_start].startswith(" ")
            and _starts_list_item(lines[block_start - 1])
        ):
            block_start -= 1
        block_end = index
        while (
            block_end + 1 < len(lines)
            and lines[block_end + 1].strip()
            and not _starts_list_item(lines[block_end + 1])
        ):
            block_end += 1
        return "\n".join(lines[block_start : block_end + 1])
    start = index
    while start > 0 and lines[start - 1].strip():
        start -= 1
    end = index
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    return "\n".join(lines[start : end + 1])


def audit_maturity(
    text: str | None,
    metadata: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    """Repositories recommended although the evidence shows an immature project.

    A repository with fewer than 50 stars created less than six months ago is
    not production-ready evidence. Recommending one without saying so is the
    failure a 0-star toy project in Final Recommendation exposed.

    The candidate set is the repositories this session holds tool metadata for,
    not only the ones whose README was fetched: a search result already proves
    stars and creation date, which is exactly what this audit grades.
    """

    if not text:
        return ()
    if _IMMATURE_BUCKET_RE.search(text):
        return ()
    lines = text.splitlines()
    now = datetime.now(timezone.utc)
    flagged: list[str] = []
    for key, info in metadata.items():
        stars = info.get("stars")
        created = info.get("created_at")
        if stars is None or stars >= 50 or created is None:
            continue
        if not isinstance(created, datetime):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (now - created).days >= 183:
            continue
        short = key.split("/")[-1]
        caveated = False
        considered = False
        for index, line in enumerate(lines):
            lowered = line.casefold()
            if key not in lowered and short not in lowered:
                continue
            if _EXCLUDED_SECTION_RE.search(_enclosing_heading(lines, index)):
                # A candidate listed under "Excluded" / "已排除" was rejected, not
                # recommended; grading its maturity raised a false alert on a real
                # report and downgraded an otherwise complete run.
                continue
            considered = True
            block = _caveat_region(lines, index).casefold()
            block = block.replace(key, " ").replace(short, " ")
            if _MATURITY_CAVEAT_RE.search(block):
                caveated = True
                break
        if caveated or not considered:
            continue
        flagged.append(str(info.get("name") or key))
    return tuple(sorted(flagged))


def _enclosing_heading(lines: list[str], index: int) -> str:
    """Nearest heading at or above the line, so section semantics apply to it."""

    for cursor in range(index, -1, -1):
        if _is_heading(lines[cursor]):
            return lines[cursor].strip()
    return ""


def _caveat_region(lines: list[str], index: int) -> str:
    """Paragraph holding a candidate plus an explicitly labelled caveat block.

    A maturity caveat is often written as its own labelled paragraph
    ("Maturity: immature ...") directly after the recommendation bullet, so the
    label makes the attribution explicit without accepting any caveat word that
    happens to appear elsewhere in the report.
    """

    start = index
    while start > 0 and lines[start - 1].strip():
        start -= 1
    end = index
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    parts = ["\n".join(lines[start : end + 1])]
    nxt = end + 1
    while nxt < len(lines) and not lines[nxt].strip():
        nxt += 1
    if nxt < len(lines) and _CAVEAT_LABEL_RE.match(lines[nxt].strip()):
        tail = nxt
        while tail + 1 < len(lines) and lines[tail + 1].strip():
            tail += 1
        parts.append("\n".join(lines[nxt : tail + 1]))
    return "\n".join(parts)


def _resolves_to_investigated(
    subject: str,
    investigated_repos: Iterable[str],
) -> str | None:
    """Map a bare project name onto an inspected repository, if one matches.

    The report calls langchain-ai/langchain "LangChain"; treating that name as
    ungrounded would accuse a fully evidenced report of fabrication.
    """

    key = subject.strip().casefold()
    if not key:
        return None
    for repo in investigated_repos:
        name = repo.strip()
        if not name:
            continue
        if name.casefold() == key or name.rsplit("/", 1)[-1].casefold() == key:
            return name
    return None


def _claimed_readme_evidence(text: str | None) -> bool:
    """True when the report asserts README evidence it never obtained."""

    if not text:
        return False
    previous = ""
    for line in text.splitlines():
        if (
            _README_CLAIM_RE.search(line)
            and not _README_DISCLOSURE_RE.search(line)
            and not _README_PLAN_CONTEXT_RE.search(line)
            and not _README_BLOCKED_CONTEXT_RE.search(previous)
        ):
            return True
        if line.strip():
            previous = line
    return False


def _declared_zero_result(text: str | None) -> str | None:
    """Return the phrase in which the report claims nothing qualifies, if any."""

    if not text:
        return None
    match = _ZERO_RESULT_RE.search(text)
    return match.group(0) if match else None


def excluded_repositories(
    text: str | None,
    candidates: Iterable[str],
) -> set[str]:
    """Casefolded candidates the finished report itself judges out.

    A live report correctly rejected two accepted candidates on their metadata
    ("not a test framework", "experimental") and was still marked partial because
    their READMEs were never fetched. Judging a candidate out and reading its
    README are alternatives, not two obligations.
    """

    if not text:
        return set()
    lines = text.splitlines()
    excluded: set[str] = set()
    for name in candidates:
        key = name.casefold()
        short = key.rsplit("/", 1)[-1]
        for index, line in enumerate(lines):
            lowered = line.casefold()
            if key not in lowered and short not in lowered:
                continue
            if _EXCLUDED_SECTION_RE.search(_enclosing_heading(lines, index)):
                excluded.add(key)
                break
            if _EXCLUDED_LINE_RE.search(line):
                excluded.add(key)
                break
    return excluded


def _imperative_subject(line: str) -> str | None:
    match = _IMPERATIVE_CLAIM_RE.match(line)
    if match is None:
        return None
    token = match.group(1)
    return token if _is_repository_subject(token) else None


def audit_grounding(
    text: str | None,
    investigated_repos: Iterable[str],
    *,
    known_repositories: Iterable[str] = (),
) -> GroundingAudit:
    """Report the repositories and verdicts the final text claims without evidence.

    A claim is grounded when the paragraph or table row that carries it cites a
    repository this session investigated, so the nested `Status: Verified Match`
    line the prompt itself asks for stays silent under its inspected repository.
    A verdict with neither an investigated repository nor a nameable subject is
    left alone: an unnamed verdict would only produce noise.

    known_repositories separates a repository this session's own search returned
    -- real metadata, README unknown -- from one that exists only in the model's
    memory. The first is an evidence-level finding, the second a fabrication
    claim, and the two must never share an alert.
    """

    if not text:
        return GroundingAudit()
    investigated = {repo.casefold() for repo in investigated_repos if repo}
    known_names = {repo.casefold(): repo for repo in known_repositories if repo}
    known = set(known_names)
    lines = text.splitlines()
    sections = _section_flags(lines)
    ungrounded: list[str] = []
    unattributed: list[str] = []
    unverified: list[str] = []
    seen_slugs: set[str] = set()

    def record(slug: str, *, recommended: bool) -> None:
        """Route one uncited repository to the channel its evidence supports."""

        key = slug.casefold()
        if key in seen_slugs:
            return
        if key in known:
            if not recommended:
                # A verdict on a repository whose search metadata this session
                # holds is a metadata-level statement the report may make;
                # verified-level claims are graded by audit_verdict_evidence.
                # Only a recommendation needs a README behind it.
                return
            seen_slugs.add(key)
            unverified.append(known_names.get(key, slug))
            return
        seen_slugs.add(key)
        ungrounded.append(slug)

    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if _is_heading(line):
            continue
        if _DISCLOSURE_RE.search(line) and _RECOMMENDATION_WORD_RE.search(line) is None:
            continue
        attached = _attached_block(lines, index)
        verdict_claim = _VERDICT_RE.search(attached) is not None
        section_claim = (
            sections[index] is True
            and _NEGATED_RECOMMENDATION_RE.search(line) is None
        )
        imperative_subject = (
            _imperative_subject(line)
            if section_claim and _NEGATED_RECOMMENDATION_RE.search(line) is None
            else None
        )
        if not (
            verdict_claim
            or section_claim
            or imperative_subject
            or _RECOMMENDATION_CLAIM_RE.match(line)
        ):
            continue
        recommended_claim = (
            imperative_subject is not None
            or _RECOMMENDATION_CLAIM_RE.match(line) is not None
            or (section_claim and not verdict_claim)
        )
        scope = attached if verdict_claim else line
        cited = any(repo in scope.casefold() for repo in investigated)
        foreign = {
            key: slug for key, slug in _github_slugs(scope).items() if key not in investigated
        }
        if cited:
            # The claim names an inspected repository, so only a repository
            # drive-by on the claim line itself is ungrounded.
            foreign = {
                key: slug for key, slug in _github_slugs(line).items() if key not in investigated
            }
        new = [slug for key, slug in foreign.items() if key not in seen_slugs]
        if new:
            for slug in new:
                record(slug, recommended=recommended_claim)
            continue
        if cited:
            continue
        subject = (
            _verdict_subject(attached)
            if verdict_claim
            else imperative_subject or _recommendation_subject(line)
        )
        if subject is None:
            continue
        if "/" in subject:
            # An owner/repo spelled out in the claim is a repository claim even
            # without a URL behind it, so it belongs with the ungrounded ones
            # rather than with the unnamed verdicts. Slash prose stays silent.
            if not _looks_like_repository_slug(subject):
                continue
            record(subject, recommended=recommended_claim)
            continue
        if (
            not verdict_claim
            and subject.casefold() not in known
            and not _looks_like_repository_brand(subject)
        ):
            # A toolchain or platform word in recommendation prose ("adopt Nix")
            # names a dependency, not a repository to investigate.
            continue
        if _resolves_to_investigated(subject, investigated) is not None:
            # The claim names an inspected repository by its brand rather than by
            # owner/repo ("LangChain" for langchain-ai/langchain). It is grounded.
            continue
        if subject not in unattributed:
            unattributed.append(subject)

    return GroundingAudit(tuple(ungrounded), tuple(unattributed), tuple(unverified))

_METADATA_ONLY_GAP_PREFIX = (
    "verified match claimed for a repository whose README was never requested: "
)

_VERIFIED_LEVEL_RE = re.compile(
    r"verified\s+match|(?<![不未])完全匹配|已验证",
    re.IGNORECASE,
)
_NEGATED_VERDICT_RE = re.compile(
    r"(?:not|never|no)\s+(?:been\s+)?(?:verified|confirmed)"
    r"|(?:cannot|can\s+not|could\s+not|must\s+not|should\s+not|is\s+not|are\s+not"
    r"|was\s+not|were\s+not|fails?\s+to|not\s+(?:been\s+)?(?:declared|treated|counted|labelled|labeled))"
    r"[^.\n]{0,40}?(?:verified|confirmed|match)"
    r"|(?:未|不|无法|不能|不得|并非)(?:验证|证实|确认|匹配|标)",
    re.IGNORECASE,
)


def audit_verdict_evidence(
    text: str | None,
    readme_attempted: Iterable[str],
    investigated_repos: Iterable[str] = (),
) -> tuple[str, ...]:
    """Repositories declared 'Verified Match' although no README was ever requested.

    A live report labelled PaddleOCR 'Verified Match (based on metadata
    confirmation)' while its own Evidence Gaps said 'README not checked: '
    'paddlepaddle/paddleocr' -- the verdict and the gap contradict each other.
    Metadata proves a licence and a star count; it does not prove that a library
    emits cell coordinates. When the task asked for README evidence, a verdict
    that strong needs a README request behind it.
    """

    if not text:
        return ()
    attempted = {value.casefold() for value in readme_attempted if value}
    investigated = [repo for repo in investigated_repos if repo]
    lines = text.splitlines()
    flagged: list[str] = []
    for repo in investigated:
        key = repo.casefold()
        if key in attempted or key in flagged:
            continue
        readme_backed = {other.casefold() for other in investigated if other.casefold() in attempted}
        for index, line in enumerate(lines):
            if key not in line.casefold():
                continue
            block = _attached_block(lines, index)
            if _VERDICT_RE.search(block) is None:
                continue
            if _VERIFIED_LEVEL_RE.search(block) is None:
                continue
            if _NEGATED_VERDICT_RE.search(block) is not None:
                continue
            if any(
                other != key and other in block.casefold()
                for other in readme_backed
            ):
                # The block compares a README-backed candidate with this one;
                # its verdict belongs to the candidate that was actually read.
                continue
            flagged.append(key)
            break
    return tuple(flagged)


CODE_UNGROUNDED_REPOSITORY = "ungrounded_repository"
CODE_UNATTRIBUTED_VERDICT = "unattributed_verdict"
CODE_METADATA_ONLY_VERDICT = "metadata_only_verdict"
CODE_IMMATURE_RECOMMENDATION = "immature_recommendation"
CODE_FABRICATED_README = "fabricated_readme_evidence"
CODE_ZERO_RESULT_WITHOUT_README = "zero_result_without_readme"
CODE_UNVERIFIED_RECOMMENDATION = "unverified_recommendation"

_GAP_PREFIXES = {
    CODE_UNGROUNDED_REPOSITORY: _GROUNDING_GAP_PREFIX,
    CODE_UNATTRIBUTED_VERDICT: _UNATTRIBUTED_GAP_PREFIX,
    CODE_IMMATURE_RECOMMENDATION: _IMMATURE_GAP_PREFIX,
    CODE_METADATA_ONLY_VERDICT: _METADATA_ONLY_GAP_PREFIX,
    CODE_UNVERIFIED_RECOMMENDATION: _UNVERIFIED_RECOMMENDATION_GAP_PREFIX,
}


class Finding(BaseModel):
    """One evidence violation, naming the repositories it is about."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    subjects: tuple[str, ...] = ()
    detail: str = ""

    @property
    def message(self) -> str:
        """The evidence-gap sentence this finding contributes to a run."""

        if self.code == CODE_FABRICATED_README:
            return _README_CLAIM_GAP_PREFIX
        if self.code == CODE_ZERO_RESULT_WITHOUT_README:
            return f"{_ZERO_RESULT_GAP_PREFIX}{self.detail}"
        prefix = _GAP_PREFIXES.get(self.code)
        if prefix is None:
            return self.code
        return prefix + ", ".join(self.subjects)


class ValidationReport(BaseModel):
    """The complete set of findings for one finished run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[Finding, ...] = ()

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def by_code(self, code: str) -> Finding | None:
        for finding in self.findings:
            if finding.code == code:
                return finding
        return None

    def subjects(self, code: str) -> tuple[str, ...]:
        finding = self.by_code(code)
        return finding.subjects if finding is not None else ()

    def gap_messages(self) -> list[str]:
        return [finding.message for finding in self.findings]


def validate_report(
    text: str | None,
    ledger: EvidenceLedger,
    *,
    require_verdict_evidence: bool = False,
) -> ValidationReport:
    """Every way the final text can claim more than the session evidenced.

    The ledger supplies the facts; each rule maps one fact to one finding. No
    rule re-parses tool results: a rule that needs to know whether a README was
    fetched asks the ledger.
    """

    investigated = ledger.investigated()
    findings: list[Finding] = []

    grounding = audit_grounding(
        text, investigated, known_repositories=ledger.known()
    )
    if grounding.ungrounded_repositories:
        findings.append(
            Finding(
                code=CODE_UNGROUNDED_REPOSITORY,
                subjects=grounding.ungrounded_repositories,
            )
        )
    if grounding.unattributed_verdicts:
        findings.append(
            Finding(
                code=CODE_UNATTRIBUTED_VERDICT,
                subjects=grounding.unattributed_verdicts,
            )
        )
    if grounding.unverified_recommendations:
        findings.append(
            Finding(
                code=CODE_UNVERIFIED_RECOMMENDATION,
                subjects=grounding.unverified_recommendations,
            )
        )

    lowered_text = (text or "").casefold()
    named_investigated = [
        name
        for name in investigated
        if name.casefold() in lowered_text
        or name.casefold().rsplit("/", 1)[-1] in lowered_text
    ]
    readme_seen = ledger.readme_fetched()
    if not readme_seen and _claimed_readme_evidence(text):
        # A live report stated that the candidates' README fields "returned
        # null" in search results. Search results carry no README field:
        # inventing tool output is fabrication, not a summarisation slip.
        findings.append(Finding(code=CODE_FABRICATED_README))

    zero_claim = _declared_zero_result(text)
    if zero_claim is not None and not readme_seen and not named_investigated:
        # "Nothing qualifies" is only credible once the run tested something. A
        # report that names the candidate it inspected and grades it Partial
        # Match (metadata only) has tested the requirement as far as this
        # session allowed, so only a claim that names no inspected repository at
        # all is the unbacked version.
        findings.append(
            Finding(code=CODE_ZERO_RESULT_WITHOUT_README, detail=zero_claim)
        )

    immature = audit_maturity(text, ledger.maturity_metadata())
    if immature:
        findings.append(
            Finding(code=CODE_IMMATURE_RECOMMENDATION, subjects=immature)
        )

    if require_verdict_evidence:
        metadata_only = audit_verdict_evidence(
            text, ledger.readme_attempted(), investigated
        )
        if metadata_only:
            findings.append(
                Finding(code=CODE_METADATA_ONLY_VERDICT, subjects=metadata_only)
            )

    return ValidationReport(findings=tuple(findings))


@dataclass(frozen=True)
class Completeness:
    """Whether a finished run may be labelled complete, and why not.

    Alerts answer "did the report claim more than the session proved". This
    answers a different question -- "did the session actually cover what was
    asked" -- so a run can be alarm-free and still partial, which is what a
    metadata-only shortlist and a candidate-count shortfall both are.
    """

    complete: bool
    reasons: tuple[str, ...] = ()


def assess_completeness(
    ledger: EvidenceLedger,
    *,
    findings: Iterable[Finding] = (),
    accepted_candidates: Iterable[str] = (),
    requested_options: int | None = None,
    max_candidates: int | None = None,
    status: str = "completed",
    evidence_complete: bool = True,
) -> Completeness:
    """Grade coverage from the ledger, never from the model's prose.

    "complete" used to mean "no alert fired and one candidate was accepted",
    so a run that read no READMEs and answered one twelfth of the request still
    printed complete. Coverage is what the reader assumes that word means: the
    request was answerable with the evidence actually gathered.
    """

    reasons: list[str] = []
    if status != "completed":
        reasons.append(f"run ended as {status}")
    if evidence_complete is not True:
        reasons.append("evidence gathering reported a gap")
    findings = tuple(findings)
    if findings:
        codes = sorted({finding.code for finding in findings})
        reasons.append("evidence findings: " + ", ".join(codes))

    accepted = [name for name in accepted_candidates if name]
    if not accepted:
        reasons.append("no candidate was accepted for comparison")
    if (
        requested_options is not None
        and max_candidates is not None
        and requested_options > max_candidates
    ):
        reasons.append(
            f"the task asked for {requested_options} options while the candidate "
            f"budget compares at most {max_candidates} (system limit)"
        )
    verified = [
        name for name in accepted if ledger.readme_status(name) in README_FETCHED_STATUSES
    ]
    if accepted and not verified:
        reasons.append(
            "no accepted candidate was checked against its README, so every verdict "
            "rests on metadata alone"
        )
    return Completeness(complete=not reasons, reasons=tuple(reasons))
