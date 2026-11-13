"""Detects the features of an application from its entry points (DOC-03), without a model.

A feature is what the application does when something starts it: a batch job and the programs its
steps run, a program that nothing calls (an online transaction, an entry called from another
application), a group of HTTP endpoints defined in one file, or the consumer of a topic. The code
each feature involves is found by following the calls, so the model is only asked to describe it.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field

from stc.docs.facts import AppFacts, Endpoint, Loc, Topic, slugify


@dataclass
class Feature:
    slug: str
    title: str
    kind: str  # job | program | endpoints | consumer
    entry: str  # job or program name, or the file for endpoints
    entry_loc: Loc
    jobs: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)  # reachable from the entry, entry first
    files: list[str] = field(default_factory=list)  # every file the feature involves
    endpoints: list[Endpoint] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.files) * 2 + len(self.programs) + len(self.endpoints) + len(self.topics)


def _reach(facts: AppFacts, starts: list[str]) -> list[str]:
    """Programs of this application reachable by calls from `starts`, in discovery order."""
    seen: dict[str, None] = {}
    queue = deque(s for s in starts if s in facts.programs)
    while queue:
        program = queue.popleft()
        if program in seen:
            continue
        seen[program] = None
        for call in facts.program_calls(program):
            if call.target in facts.programs and call.target not in seen:
                queue.append(call.target)
    return list(seen)


def _unique(slug: str, taken: set[str]) -> str:
    candidate, n = slug, 2
    while candidate in taken:
        candidate, n = f"{slug}-{n}", n + 1
    taken.add(candidate)
    return candidate


def detect_features(facts: AppFacts, limit: int) -> tuple[list[Feature], list[Feature]]:
    """(documented, skipped): the biggest features up to `limit`, then the rest."""
    found: list[Feature] = []
    covered: set[str] = set()

    for job in sorted(facts.jobs):
        steps = [c.target for c in facts.job_calls(job)]
        programs = _reach(facts, steps)
        covered.update(programs)
        files = [facts.file_of_job[job]] + [facts.file_of_program[p] for p in programs]
        found.append(
            Feature(
                "", f"Batch job {job}", "job", job, facts.jobs[job],
                jobs=[job], programs=programs, files=list(dict.fromkeys(files)),
            )
        )  # fmt: skip

    called = {c.target for c in facts.calls}
    for program in sorted(facts.programs):
        if program in called or program in covered:
            continue
        programs = _reach(facts, [program])
        covered.update(programs)
        files = [facts.file_of_program[p] for p in programs]
        found.append(
            Feature(
                "", f"Program {program}", "program", program, facts.programs[program],
                programs=programs, files=list(dict.fromkeys(files)),
            )
        )  # fmt: skip

    by_file: dict[str, list[Endpoint]] = defaultdict(list)
    for endpoint in facts.endpoints:
        by_file[endpoint.loc.path].append(endpoint)
    for path in sorted(by_file):
        found.append(
            Feature(
                "", f"Endpoints in {path}", "endpoints", path, by_file[path][0].loc,
                files=[path], endpoints=by_file[path],
            )
        )  # fmt: skip

    topics: dict[str, list[Topic]] = defaultdict(list)
    for topic in facts.topics:
        if topic.role == "subscribes":
            topics[topic.loc.path].append(topic)
    for path in sorted(topics):
        found.append(
            Feature(
                "", f"Consumer in {path}", "consumer", path, topics[path][0].loc,
                files=[path], topics=topics[path],
            )
        )  # fmt: skip

    found.sort(key=lambda f: (-f.size, f.title))
    taken: set[str] = set()
    for feature in found:
        feature.slug = _unique(slugify(f"{feature.kind}-{feature.entry}"), taken)
    cap = max(0, limit)
    return found[:cap], found[cap:]
