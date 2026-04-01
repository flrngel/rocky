from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Lane(str, Enum):
    META = 'meta'
    DIRECT = 'direct'
    STANDARD = 'standard'
    DEEP = 'deep'


class TaskClass(str, Enum):
    CONVERSATION = 'conversation'
    META = 'meta'
    REPO = 'repo'
    EXTRACTION = 'extraction'
    SITE = 'site'
    DATA = 'data'
    LIVE_COMPARE = 'live_compare'
    AUTOMATION = 'automation'
    RESEARCH = 'research'


@dataclass(slots=True)
class RouteDecision:
    lane: Lane
    task_class: TaskClass
    risk: str
    reasoning: str
    tool_families: list[str] = field(default_factory=list)
    task_signature: str = 'conversation/general'


class Router:
    META_TRIGGERS = [
        'what tools', 'what skills', 'show config', 'status', 'help', 'permissions', 'memory', 'sessions',
        '/help', '/tools', '/skills', '/status', '/config', '/memory', '/permissions',
    ]

    def route(self, prompt: str) -> RouteDecision:
        text = prompt.strip()
        lowered = text.lower()
        if not text:
            return RouteDecision(Lane.META, TaskClass.META, 'low', 'Empty prompt', [], 'meta/empty')
        if any(trigger in lowered for trigger in self.META_TRIGGERS):
            return RouteDecision(
                lane=Lane.META,
                task_class=TaskClass.META,
                risk='low',
                reasoning='Deterministic runtime/meta question',
                tool_families=[],
                task_signature='meta/runtime',
            )
        if any(word in lowered for word in ['spreadsheet', 'excel', '.xlsx', '.csv', 'dataframe', 'analyze sheet']):
            lane = Lane.DEEP if len(text) > 120 else Lane.STANDARD
            return RouteDecision(lane, TaskClass.DATA, 'medium', 'Structured data task', ['filesystem', 'data', 'python'], 'data/spreadsheet/analysis')
        if any(word in lowered for word in ['crawl', 'website', 'browser', 'click', 'scrape', 'site']):
            return RouteDecision(Lane.STANDARD, TaskClass.SITE, 'medium', 'Site/browser task', ['web', 'browser', 'filesystem'], 'site/understanding/general')
        if any(word in lowered for word in ['compare sources', 'compare', 'forecast', 'probability', 'market', 'weather', 'latest', 'research']):
            return RouteDecision(Lane.STANDARD, TaskClass.RESEARCH, 'medium', 'Research or live-source task', ['web', 'browser'], 'research/live_compare/general')
        if any(word in lowered for word in ['extract', 'normalize', 'classify', 'label', 'schema', 'json']):
            return RouteDecision(Lane.STANDARD, TaskClass.EXTRACTION, 'low', 'Extraction or structured-output task', ['filesystem', 'python', 'data'], 'extract/general')
        if any(word in lowered for word in ['test', 'repo', 'code', 'bug', 'refactor', 'git', 'file', 'directory', 'module']):
            lane = Lane.DEEP if len(text) > 180 else Lane.STANDARD
            return RouteDecision(lane, TaskClass.REPO, 'medium', 'Repo or file task', ['filesystem', 'shell', 'git', 'python'], 'repo/general')
        if any(word in lowered for word in ['automate', 'workflow', 'repeat', 'script']):
            return RouteDecision(Lane.STANDARD, TaskClass.AUTOMATION, 'medium', 'Automation task', ['filesystem', 'shell', 'python'], 'automation/general')
        if len(text) < 80:
            return RouteDecision(Lane.DIRECT, TaskClass.CONVERSATION, 'low', 'Short direct prompt', [], 'conversation/general')
        return RouteDecision(Lane.STANDARD, TaskClass.CONVERSATION, 'low', 'General conversation', [], 'conversation/general')
