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
    META_EXACT = {
        'status',
        'help',
        'permissions',
        'memory',
        'sessions',
        'show config',
        'show permissions',
        'show memory',
        'show sessions',
        'what tools',
        'what tools do you have',
        'what skills',
        'what skills do you have',
    }
    META_PREFIXES = ('/help', '/tools', '/skills', '/status', '/config', '/memory', '/permissions')
    COMMAND_VERBS = ('run', 'execute', 'exec', 'launch', 'check', 'inspect')
    RUNTIME_TARGET_PATTERNS = (
        re.compile(r"\b(?:what|which)\s+versions?\s+(?:of\s+)?(?P<target>[a-z0-9_.+-]+)\b", re.I),
        re.compile(r"\b(?:what|which)\s+version\s+of\s+(?P<target>[a-z0-9_.+-]+)\b", re.I),
        re.compile(r"\bdo\s+i\s+have\s+(?P<target>[a-z0-9_.+-]+)\b", re.I),
        re.compile(r"\bis\s+(?P<target>[a-z0-9_.+-]+)\s+installed\b", re.I),
        re.compile(r"\bwhere\s+is\s+(?P<target>[a-z0-9_.+-]+)\b", re.I),
        re.compile(r"\bwhich\s+(?P<target>[a-z0-9_.+-]+)\b", re.I),
    )
    RUNTIME_TARGET_STOPWORDS = {
        'what',
        'which',
        'are',
        'is',
        'the',
        'my',
        'system',
        'machine',
        'installed',
        'available',
        'latest',
        'current',
        'in',
        'on',
        'this',
        'do',
        'i',
        'have',
        'of',
        'list',
        'it',
        'lit',
        'show',
        'me',
        'file',
        'files',
        'module',
        'modules',
        'class',
        'classes',
        'function',
        'functions',
        'parser',
        'path',
        'paths',
        'directory',
        'directories',
        'repo',
        'code',
        'command',
        'commands',
        'alias',
        'aliases',
        'cli',
    }
    SHELL_FENCE_RE = re.compile(r"```(?:bash|sh|zsh|shell)?\s*\n(?P<body>.*?)```", re.I | re.S)
    SHELL_TOKEN_RE = re.compile(r"(^|\s)(?:[a-z0-9_./-]+)(?:\s+[-\\w./:=@]+)*(?:\s*(?:&&|\|\||\||;|>|>>)\s*.+)+", re.I | re.M)
    PATH_HINTS = ('.py', '.ts', '.tsx', '.js', '.jsx', '.rb', '.go', '.rs', '.java', '.json', '.yaml', '.yml', '.toml', '.md')
    SHELL_INFO_PHRASES = (
        'current shell',
        'shell history',
        'last history',
        'last command',
        'latest shell command',
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
    BUILD_VERBS = ('build', 'create', 'write', 'make', 'scaffold', 'bootstrap', 'generate')
    BUILD_TARGET_HINTS = (
        'mini project',
        'small project',
        'tiny project',
        'script project',
        'cli project',
        'empty workspace',
        'empty directory',
        'from scratch',
        'create exactly these files',
        'create these files',
        'write these files',
    )
    BUILD_ARTIFACT_HINTS = (
        '.py',
        '.sh',
        '.md',
        '.txt',
        '.json',
        '.jsonl',
        '.csv',
        'readme',
        'script',
        'project',
        'cli',
        'app',
        'tool',
        'utility',
    )

    def _tokens(self, lowered: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_.+-]+", lowered))

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
        if self._looks_like_repo_task(lowered) and any(
            word in lowered
            for word in (
                'repo',
                'code',
                'codebase',
                'file',
                'function',
                'module',
                'class',
                'implement',
                'implemented',
                'line',
            )
        ):
            return False
        if any(phrase in lowered for phrase in self.SHELL_INFO_PHRASES):
            return True
        if any(token in lowered for token in self.SHELL_INFO_TOKENS):
            return True
        return (
            ('shell' in lowered or 'terminal' in lowered)
            and any(word in lowered for word in ('show me', 'tell me', 'what is', 'what are', 'last 10', 'last ten'))
        )

    def _looks_like_repo_task(self, lowered: str) -> bool:
        tokens = self._tokens(lowered)
        return bool(tokens & {'test', 'repo', 'code', 'bug', 'refactor', 'git', 'file', 'directory', 'module'}) or any(
            hint in lowered for hint in self.PATH_HINTS
        )

    def extract_runtime_targets(self, prompt: str) -> list[str]:
        lowered = prompt.lower().strip()
        targets: list[str] = []
        for pattern in self.RUNTIME_TARGET_PATTERNS:
            for match in pattern.finditer(lowered):
                target = (match.groupdict().get("target") or "").strip("`'\" ")
                if not target:
                    continue
                if target in {"provider", "model", "tools", "skills", "config", "status"}:
                    continue
                if target not in targets:
                    targets.append(target)
        words = re.findall(r"[a-z0-9_.+-]+", lowered)
        for index, word in enumerate(words):
            if word not in {"version", "versions"} or index == 0:
                continue
            candidate = words[index - 1]
            if candidate in self.RUNTIME_TARGET_STOPWORDS:
                continue
            if candidate not in targets:
                targets.append(candidate)
        return targets

    def _looks_like_runtime_inspection_task(self, prompt: str) -> bool:
        lowered = prompt.lower()
        if self._looks_like_repo_task(lowered) and any(
            word in lowered
            for word in (
                'repo',
                'code',
                'file',
                'files',
                'module',
                'modules',
                'class',
                'function',
                'parser',
                'implementation',
                'wizard',
                'alias',
                'aliases',
            )
        ):
            return False
        return bool(self.extract_runtime_targets(prompt))

    def _looks_like_build_automation_task(self, lowered: str) -> bool:
        has_build_verb = any(
            re.search(rf"\b{re.escape(verb)}\b", lowered)
            for verb in self.BUILD_VERBS
        )
        if not has_build_verb:
            return False
        if not any(hint in lowered for hint in self.BUILD_TARGET_HINTS):
            return False
        return any(hint in lowered for hint in self.BUILD_ARTIFACT_HINTS) or any(
            phrase in lowered
            for phrase in (
                'then run',
                'verify it works',
                'verify it',
                'run it',
                'exact output',
                'usage example',
            )
        )

    def _looks_like_research_task(self, lowered: str, prompt: str) -> bool:
        if self._looks_like_repo_task(lowered):
            return False
        if self._looks_like_shell_inspection_task(lowered):
            return False
        if self._looks_like_runtime_inspection_task(prompt):
            return False
        tokens = self._tokens(lowered)
        if 'compare sources' in lowered:
            return True
        if tokens & {'forecast', 'probability', 'market', 'weather', 'research'}:
            return True
        return 'latest' in tokens and bool(tokens & {'news', 'price', 'prices', 'market', 'weather'})

    def _looks_like_meta_request(self, lowered: str) -> bool:
        if lowered in self.META_EXACT:
            return True
        if any(lowered.startswith(prefix) for prefix in self.META_PREFIXES):
            return True
        if self._looks_like_repo_task(lowered):
            return False
        return any(
            phrase in lowered
            for phrase in (
                'what tools',
                'what skills',
                'show config',
                'what provider',
                'which provider',
                'active provider',
                'what model',
                'which model',
                'active model',
                'provider am i using',
                'model am i using',
                'show permissions',
                'show memory',
                'show sessions',
                'permission mode',
                'session id',
            )
        )

    def route(self, prompt: str) -> RouteDecision:
        text = prompt.strip()
        lowered = text.lower()
        if not text:
            return RouteDecision(Lane.META, TaskClass.META, 'low', 'Empty prompt', [], 'meta/empty')
        if self._looks_like_meta_request(lowered):
            return RouteDecision(
                lane=Lane.META,
                task_class=TaskClass.META,
                risk='low',
                reasoning='Deterministic runtime/meta question',
                tool_families=[],
                task_signature='meta/runtime',
            )
        if self._looks_like_build_automation_task(lowered):
            lane = Lane.DEEP if len(text) > 180 else Lane.STANDARD
            return RouteDecision(
                lane,
                TaskClass.AUTOMATION,
                'medium',
                'Workspace build or scaffold request that should create files and verify them',
                ['filesystem', 'shell', 'python'],
                'automation/general',
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
        if self._looks_like_runtime_inspection_task(text):
            return RouteDecision(
                Lane.STANDARD,
                TaskClass.REPO,
                'medium',
                'Local runtime or installed software inspection request',
                ['shell'],
                'local/runtime_inspection',
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
        if self._looks_like_research_task(lowered, text):
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
