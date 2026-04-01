from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re


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
    COMMAND_VERBS = ('run', 'execute', 'exec', 'launch', 'check', 'inspect')
    SHELL_FENCE_RE = re.compile(r"```(?:bash|sh|zsh|shell)?\s*\n(?P<body>.*?)```", re.I | re.S)
    SHELL_TOKEN_RE = re.compile(r"(^|\s)(?:[a-z0-9_./-]+)(?:\s+[-\\w./:=@]+)*(?:\s*(?:&&|\|\||\||;|>|>>)\s*.+)+", re.I | re.M)
    PATH_HINTS = ('.py', '.ts', '.tsx', '.js', '.jsx', '.rb', '.go', '.rs', '.java', '.json', '.yaml', '.yml', '.toml', '.md')
    SHELL_INFO_PHRASES = (
        'current shell',
        'shell history',
        'last history',
        'last command',
        'command history',
        'what shell am i using',
        'what shell am i',
        'where am i',
        'current directory',
        'working directory',
        'who am i',
        'home directory',
        'environment variable',
    )
    SHELL_INFO_TOKENS = ('history', 'pwd', 'whoami', '$shell', '$home')

    def _looks_like_shell_task(self, text: str, lowered: str) -> bool:
        has_fenced_shell = bool(self.SHELL_FENCE_RE.search(text))
        has_shell_tokens = bool(self.SHELL_TOKEN_RE.search(text))
        asks_to_run = any(phrase in lowered for phrase in (
            'run command',
            'execute command',
            'run this',
            'execute this',
            'run the following',
            'execute the following',
            'run this bash',
            'execute this bash',
            'run this shell',
            'execute this shell',
        ))
        starts_with_verb = lowered.startswith(self.COMMAND_VERBS)
        return has_fenced_shell or has_shell_tokens or asks_to_run or starts_with_verb

    def _looks_like_shell_inspection_task(self, lowered: str) -> bool:
        if any(phrase in lowered for phrase in self.SHELL_INFO_PHRASES):
            return True
        if any(token in lowered for token in self.SHELL_INFO_TOKENS):
            return True
        return (
            ('shell' in lowered or 'terminal' in lowered)
            and any(word in lowered for word in ('show me', 'tell me', 'what is', 'what are', 'last 10', 'last ten'))
        )

    def _looks_like_repo_task(self, lowered: str) -> bool:
        return any(word in lowered for word in ['test', 'repo', 'code', 'bug', 'refactor', 'git', 'file', 'directory', 'module']) or any(
            hint in lowered for hint in self.PATH_HINTS
        )

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
        if self._looks_like_shell_task(text, lowered):
            lane = Lane.DEEP if len(text) > 180 else Lane.STANDARD
            return RouteDecision(
                lane,
                TaskClass.REPO,
                'medium',
                'Explicit shell command or execution request',
                ['filesystem', 'shell', 'python', 'git'],
                'repo/shell_execution',
            )
        if self._looks_like_shell_inspection_task(lowered):
            lane = Lane.DEEP if len(text) > 120 else Lane.STANDARD
            return RouteDecision(
                lane,
                TaskClass.REPO,
                'medium',
                'Shell or environment inspection request',
                ['shell', 'filesystem'],
                'repo/shell_inspection',
            )
        if any(word in lowered for word in ['crawl', 'website', 'browser', 'click', 'scrape', 'site']):
            return RouteDecision(Lane.STANDARD, TaskClass.SITE, 'medium', 'Site/browser task', ['web', 'browser', 'filesystem'], 'site/understanding/general')
        if any(word in lowered for word in ['compare sources', 'compare', 'forecast', 'probability', 'market', 'weather', 'latest', 'research']):
            return RouteDecision(Lane.STANDARD, TaskClass.RESEARCH, 'medium', 'Research or live-source task', ['web', 'browser'], 'research/live_compare/general')
        if any(word in lowered for word in ['extract', 'normalize', 'classify', 'label', 'schema', 'json']):
            return RouteDecision(Lane.STANDARD, TaskClass.EXTRACTION, 'low', 'Extraction or structured-output task', ['filesystem', 'python', 'data'], 'extract/general')
        if self._looks_like_repo_task(lowered):
            lane = Lane.DEEP if len(text) > 180 else Lane.STANDARD
            return RouteDecision(lane, TaskClass.REPO, 'medium', 'Repo or file task', ['filesystem', 'shell', 'git', 'python'], 'repo/general')
        if any(word in lowered for word in ['automate', 'workflow', 'repeat', 'script']):
            return RouteDecision(Lane.STANDARD, TaskClass.AUTOMATION, 'medium', 'Automation task', ['filesystem', 'shell', 'python'], 'automation/general')
        if len(text) < 80:
            return RouteDecision(Lane.DIRECT, TaskClass.CONVERSATION, 'low', 'Short direct prompt', [], 'conversation/general')
        return RouteDecision(Lane.STANDARD, TaskClass.CONVERSATION, 'low', 'General conversation', [], 'conversation/general')
