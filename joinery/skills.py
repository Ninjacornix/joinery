"""Past recipes as skills: find the closest accepted recipe for a new request.

Every asset in a project was made by a recipe (``recipes/*.toml`` and its op
log) that the user accepted with the change that made it. Those recipes are the
project's worked examples. This module indexes them by what they made, from the
words of the change summaries the user accepted them under, the recipe's note,
its outputs and the operations and parameters in its op log, and finds the
closest ones for a request by plain lexical scoring (BM25). No model, no network.

An agent asked for "a creaky gate sound" can start from the door-creak patch
and change its numbers instead of writing a patch from nothing. The index is per
project (``.joinery/skills/index.json``); recipes reach another project only as
part of a kit, whose recipes land in that project's ``recipes/``.

Read-only: nothing here changes a recipe or an asset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from joinery.recipe import RECIPE_DIR

SKILLS = ".joinery/skills"
INDEX = "index.json"
VERSION = 1
#: Bytes of an op log read for its words.
SCRIPT_BYTES = 200_000
#: Words kept per recipe from its op log.
OP_WORDS = 60
#: How much a word counts by where it was found.
WEIGHTS = {"summary": 3.0, "note": 2.0, "outputs": 2.0, "tool": 1.5, "ops": 1.0}
#: A kit recipe records its parameters on this line (joinery.kits.MARKER).
KIT_MARKER = "# joinery-kit: "
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_PARAM = re.compile(
    r"^\s*(?:var\s+|const\s+|~)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)\b",
    re.MULTILINE,
)
_KWARG = re.compile(r"[(,]\s*([a-z_][a-z0-9_]*)\s*[=:]\s*(-?\d+(?:\.\d+)?)\b")
#: Words that say nothing about what a recipe made.
STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "or",
        "the",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "from",
        "with",
        "as",
        "is",
        "it",
        "its",
        "be",
        "are",
        "was",
        "this",
        "that",
        "these",
        "those",
        "not",
        "no",
        "yes",
        "if",
        "else",
        "elif",
        "then",
        "def",
        "return",
        "import",
        "class",
        "self",
        "none",
        "null",
        "true",
        "false",
        "var",
        "const",
        "func",
        "let",
        "new",
        "end",
        "do",
        "while",
        "out",
        "arg",
        "args",
        "kwargs",
        "value",
        "values",
        "name",
        "names",
        "type",
        "types",
        "data",
        "path",
        "paths",
        "file",
        "files",
        "get",
        "set",
        "add",
        "make",
        "run",
        "main",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "len",
        "range",
        "print",
        "use",
        "used",
        "using",
        "into",
        "each",
        "all",
        "any",
        "one",
        "two",
        "three",
        "can",
        "will",
        "should",
        "must",
        "may",
        "res",
        "joinery",
        "recipe",
        "recipes",
        "script",
        "py",
        "scd",
        "gd",
        "toml",
        "json",
        "bpy",
        "ops",
        "obj",
        "object",
        "objects",
        "context",
        "scene",
        "co",
        "x",
        "y",
        "z",
        "i",
        "j",
        "k",
        "n",
        "tmp",
        "temp",
        "result",
        "results",
        "item",
        "items",
        "key",
        "keys",
        "sys",
        "os",
        "math",
        "pathlib",
        "argv",
    }
)


def _stem(word: str) -> str:
    """A light suffix fold, the same for requests and recipes: "creaky", "creaks" and
    "creaked" all become "creak"."""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    if len(word) > 6 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 5 and word.endswith("ed"):
        word = word[:-2]
    if len(word) > 5 and word.endswith("y") and word[-2] not in "aeiou":
        word = word[:-1]
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def words(text: str) -> list[str]:
    """Lower-case words of ``text``: snake_case and CamelCase split, plurals folded,
    filler words dropped."""
    out: list[str] = []
    for token in _WORD.findall(text.replace("_", " ")):
        for part in _CAMEL.findall(token) or [token]:
            word = _stem(part.lower())
            if len(word) > 1 and word not in STOP and not word.isdigit():
                out.append(word)
    return out


def _digest(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _read(path: Path, limit: int = SCRIPT_BYTES) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(limit)
    except OSError:
        return b""


def _kit_params(script: str) -> dict[str, Any]:
    for line in script.splitlines():
        if line.startswith(KIT_MARKER):
            try:
                record = json.loads(line[len(KIT_MARKER) :])
            except ValueError:
                return {}
            if isinstance(record, dict):
                params = record.get("params") or record.get("parameters") or {}
                if isinstance(params, dict):
                    return {str(k): v for k, v in list(params.items())[:12]}
    return {}


def _params(script: str) -> dict[str, str]:
    """Named numbers in an op log (constants, assignments, keyword arguments): the
    values a new request most often changes."""
    found: dict[str, str] = {}
    for name, value in [*_PARAM.findall(script), *_KWARG.findall(script)]:
        if name.lower() not in STOP and name not in found:
            found[name] = value
        if len(found) >= 12:
            break
    return found


def _ops(script: str) -> list[str]:
    """The operations an op log calls, most used first."""
    calls = Counter(
        name
        for name in _CALL.findall(script)
        if len(name) > 2 and name.lower() not in STOP and not name.startswith("_")
    )
    return [name for name, _ in calls.most_common(16)]


def _summaries(project: Path) -> dict[str, list[str]]:
    """Project path -> summaries of the accepted changes that touched it."""
    found: dict[str, list[str]] = {}
    for journal in sorted((project / ".joinery/transactions").glob("*/journal.json")):
        try:
            data = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("state") != "accepted":
            continue
        summary = str(data.get("summary", "")).strip()
        if not summary:
            continue
        for change in data.get("changes") or []:
            if isinstance(change, dict) and change.get("path"):
                found.setdefault(str(change["path"]), []).append(summary)
    return found


def _entry(
    project: Path, toml: Path, digest: str, said: list[str]
) -> dict[str, Any] | None:
    try:
        recipe = tomllib.loads(toml.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    script_path = str(recipe.get("script", ""))
    script = (
        _read(project / script_path).decode("utf-8", "replace") if script_path else ""
    )
    outputs = [str(o) for o in recipe.get("outputs", []) if isinstance(o, str)]
    ops = _ops(script)
    op_words = [w for w, _ in Counter(words(script)).most_common(OP_WORDS)]
    params: dict[str, Any] = _kit_params(script) or _params(script)
    return {
        "recipe": toml.relative_to(project).as_posix(),
        "digest": digest,
        "tool": str(recipe.get("tool", "")),
        "script": script_path,
        "outputs": outputs,
        "note": str(recipe.get("note", "")),
        "summaries": list(dict.fromkeys(said))[-6:],
        "ops": ops,
        "params": params,
        "words": {
            "summary": words(" ".join(said)),
            "note": words(str(recipe.get("note", ""))),
            "outputs": words(" ".join(outputs)),
            "tool": words(str(recipe.get("tool", ""))),
            "ops": op_words + words(" ".join(ops)),
        },
    }


def _index_path(project: Path) -> Path:
    return project / SKILLS / INDEX


def load(project: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(_index_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return []
    return [s for s in data.get("skills", []) if isinstance(s, dict)]


def refresh(project: Path) -> list[dict[str, Any]]:
    """Index the project's recipes again where they or their changes are new.

    A recipe in the project's ``recipes/`` was accepted with the change that made
    it (staged changes live in their own folder until then). The summaries of the
    accepted changes that touched it are kept in the index, so they outlive the
    change journals that history retention removes.
    """
    project = project.resolve()
    old = {s.get("recipe"): s for s in load(project)}
    said = _summaries(project)
    skills: list[dict[str, Any]] = []
    for toml in sorted((project / RECIPE_DIR).glob("*.toml")):
        rel = toml.relative_to(project).as_posix()
        raw = _read(toml)
        try:
            recipe = tomllib.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        script = str(recipe.get("script", ""))
        outputs = recipe.get("outputs", [])
        if not isinstance(outputs, list):
            outputs = []
        previous = old.get(rel) or {}
        summaries = list(previous.get("summaries", []))
        for touched in [rel, script, *[str(o) for o in outputs if isinstance(o, str)]]:
            summaries += said.get(touched, [])
        summaries = list(dict.fromkeys(summaries))
        digest = _digest(
            f"skills v{VERSION}".encode(),
            raw,
            _read(project / script) if script else b"",
            json.dumps(summaries).encode(),
        )
        if previous.get("digest") == digest:
            skills.append(previous)
            continue
        entry = _entry(project, toml, digest, summaries)
        if entry is not None:
            skills.append(entry)
    if [s.get("digest") for s in skills] != [s.get("digest") for s in old.values()]:
        path = _index_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"version": VERSION, "skills": skills}, ensure_ascii=False, indent=1
            ),
            encoding="utf-8",
        )
    return skills


def score(
    request: str, skills: list[dict[str, Any]]
) -> list[tuple[float, dict[str, Any], list[str]]]:
    """BM25 over each recipe's weighted words: (score, skill, matched words), best first."""
    query = list(dict.fromkeys(words(request)))
    if not query or not skills:
        return []
    bags: list[dict[str, float]] = []
    for skill in skills:
        bag: dict[str, float] = defaultdict(float)
        for field, weight in WEIGHTS.items():
            for word in skill.get("words", {}).get(field, []):
                bag[word] += weight
        bags.append(bag)
    count = len(bags)
    average = sum(sum(b.values()) for b in bags) / count or 1.0
    k1, b = 1.2, 0.75
    ranked: list[tuple[float, dict[str, Any], list[str]]] = []
    for skill, bag in zip(skills, bags, strict=True):
        length = sum(bag.values()) or 1.0
        total = 0.0
        matched: list[str] = []
        for word in query:
            tf = bag.get(word, 0.0)
            if not tf:
                continue
            having = sum(1 for other in bags if word in other)
            idf = math.log(1 + (count - having + 0.5) / (having + 0.5))
            total += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
            matched.append(word)
        if total > 0:
            ranked.append((round(total, 3), skill, matched))
    ranked.sort(key=lambda item: (-item[0], item[1].get("recipe", "")))
    return ranked


def find(project: Path, request: str, limit: int = 3) -> dict[str, Any]:
    """The project's past recipes closest to ``request``, for the agent to start from."""
    request = request.strip()
    if not request:
        raise ValueError("Say what you want to make, in a few words.")
    skills = refresh(project)
    ranked = score(request, skills)[: max(1, min(limit, 5))]
    return {
        "request": request,
        "indexed": len(skills),
        "recipes": [
            {
                "recipe": skill["recipe"],
                "score": value,
                "matched": matched,
                "tool": skill.get("tool", ""),
                "script": skill.get("script", ""),
                "outputs": skill.get("outputs", [])[:6],
                "note": skill.get("note", "")[:300],
                "made_for": skill.get("summaries", [])[-3:],
                "ops": skill.get("ops", [])[:10],
                "params": skill.get("params", {}),
            }
            for value, skill, matched in ranked
        ],
        "next": (
            "Read the closest recipe's script, then make the new asset with the same "
            "tool's operations and changed parameters; it is recorded as its own recipe. "
            "Edit the existing asset instead when the user asked to change that one."
            if ranked
            else "No past recipe here is close; make it with the owning tool's operations."
        ),
    }


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``skills --project P [--find REQUEST] [--json]``."""
    parser = subparsers.add_parser(
        "skills",
        help="index the project's accepted recipes; find the closest to a request",
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--find", default="", help="a request, in a few words")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="print JSON")


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not project.is_dir():
        message = f"No project at {project}."
        print(json.dumps({"error": message}) if args.json else message)
        return 1
    if args.find:
        found = find(project, args.find, args.limit)
        if args.json:
            print(json.dumps(found))
            return 0
        for item in found["recipes"]:
            print(
                f"{item['score']:6.2f}  {item['recipe']}  ({item['tool']})  "
                f"matched {', '.join(item['matched'])}"
            )
        if not found["recipes"]:
            print("No close recipe.")
        return 0
    indexed = refresh(project)
    if args.json:
        print(
            json.dumps(
                {"indexed": len(indexed), "recipes": [s["recipe"] for s in indexed]}
            )
        )
        return 0
    print(f"{len(indexed)} recipes indexed in {SKILLS}/{INDEX}.")
    for skill in indexed:
        print(f"- {skill['recipe']} ({skill['tool']}): {skill['note'][:80]}")
    return 0
