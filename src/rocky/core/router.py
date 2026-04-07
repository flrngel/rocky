from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from rocky.core.runtime_state import ActiveTaskThread, continuation_signal_score


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
    confidence: float = 0.55
    source: str = 'lexical'
    continued_thread_id: str | None = None
    continuation_decision: str = 'start_new_thread'


@dataclass(slots=True)
class ContinuationDecision:
    action: str
    confidence: float
    thread_id: str | None = None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


class ContinuationResolver:
    NEW_TASK_MARKERS = (
        'new task',
        'unrelated',
        'different task',
        'switch topics',
        'ignore the previous',
        'separate question',
        'another question',
    )
    CONTINUE_MARKERS = ('continue', 'resume', 'keep going', 'keep working', 'carry on', 'pick up', 'pick back up', 'same task', 'what next', 'next step', 'finish it', 'finish this')

    def resolve(
        self,
        prompt: str,
        *,
        active_threads: list[ActiveTaskThread],
        recent_threads: list[ActiveTaskThread],
        workspace_root: str,
        execution_cwd: str,
    ) -> ContinuationDecision:
        text = prompt.strip()
        lowered = text.lower()
        explicit_continue = any(marker in lowered for marker in self.CONTINUE_MARKERS)
        if not text:
            return ContinuationDecision(action='start_new_thread', confidence=1.0)
        if any(marker in lowered for marker in self.NEW_TASK_MARKERS):
            return ContinuationDecision(action='start_new_thread', confidence=0.95, reasons=['explicit_new_task'])
        candidates = active_threads or recent_threads
        if not candidates:
            return ContinuationDecision(action='start_new_thread', confidence=0.3)
        scored: list[tuple[float, ActiveTaskThread, list[str]]] = []
        only_continuable_thread = len(candidates) == 1
        for thread in candidates:
            score, reasons = continuation_signal_score(
                prompt,
                thread,
                execution_cwd=execution_cwd,
                workspace_root=workspace_root,
            )
            if thread.status == 'active':
                score += 1.0
                reasons.append('thread_active')
            elif thread.status == 'awaiting_user':
                score += 0.8
                reasons.append('thread_awaiting_user')
            elif thread.status == 'needs_repair':
                score += 0.7
                reasons.append('thread_needs_repair')
            if only_continuable_thread and thread.workspace_root == workspace_root:
                score += 0.6
                reasons.append('only_continuable_thread')
            scored.append((score, thread, reasons))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        best_score, best_thread, reasons = scored[0]
        threshold = 4.6 if explicit_continue else 5.5
        if best_score >= threshold:
            return ContinuationDecision(
                action='continue_active_thread' if best_thread.status == 'active' else 'resume_recent_thread',
                thread_id=best_thread.thread_id,
                score=best_score,
                confidence=min(0.98, 0.55 + best_score / 10),
                reasons=reasons,
            )
        return ContinuationDecision(action='start_new_thread', score=best_score, confidence=0.35, reasons=reasons)


class Router:
    TASK_SIGNATURE_PROFILES: dict[str, dict[str, Any]] = {
        'meta/runtime': {
            'lane': Lane.META,
            'task_class': TaskClass.META,
            'risk': 'low',
            'tool_families': [],
        },
        'repo/shell_execution': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.REPO,
            'risk': 'medium',
            'tool_families': ['filesystem', 'shell', 'python', 'git'],
        },
        'repo/shell_inspection': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.REPO,
            'risk': 'medium',
            'tool_families': ['shell', 'filesystem'],
        },
        'local/runtime_inspection': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.REPO,
            'risk': 'medium',
            'tool_families': ['shell'],
        },
        'site/understanding/general': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.SITE,
            'risk': 'medium',
            'tool_families': ['web', 'browser', 'filesystem'],
        },
        'research/live_compare/general': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.RESEARCH,
            'risk': 'medium',
            'tool_families': ['web', 'browser'],
        },
        'extract/general': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.EXTRACTION,
            'risk': 'low',
            'tool_families': ['filesystem', 'python', 'data'],
        },
        'data/spreadsheet/analysis': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.DATA,
            'risk': 'medium',
            'tool_families': ['filesystem', 'data', 'python'],
        },
        'repo/general': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.REPO,
            'risk': 'medium',
            'tool_families': ['filesystem', 'shell', 'git', 'python'],
        },
        'automation/general': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.AUTOMATION,
            'risk': 'medium',
            'tool_families': ['filesystem', 'shell', 'python'],
        },
        'conversation/general': {
            'lane': Lane.STANDARD,
            'task_class': TaskClass.CONVERSATION,
            'risk': 'low',
            'tool_families': [],
        },
    }
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
        'what', 'which', 'are', 'is', 'the', 'my', 'system', 'machine', 'installed', 'available', 'latest', 'current',
        'in', 'on', 'this', 'do', 'i', 'have', 'of', 'list', 'it', 'lit', 'show', 'me', 'file', 'files', 'module', 'modules',
        'class', 'classes', 'function', 'functions', 'parser', 'path', 'paths', 'directory', 'directories', 'repo', 'code',
        'command', 'commands', 'alias', 'aliases', 'cli',
    }
    SHELL_FENCE_RE = re.compile(r"```(?:bash|sh|zsh|shell)?\s*\n(?P<body>.*?)```", re.I | re.S)
    SHELL_TOKEN_RE = re.compile(r"(^|\s)(?:[a-z0-9_./-]+)(?:\s+[-\\w./:=@]+)*(?:\s*(?:&&|\|\||\||;|>|>>)\s*.+)+", re.I | re.M)
    INLINE_COMMAND_RE = re.compile(r"`(?P<body>[^`\n]+)`")
    SCRIPT_PATH_RE = re.compile(r"(?<![\w/])(?:\./)?[a-z0-9_.-]+\.(?:sh|py|rb|js|ts|tsx|pl|php)(?![\w/])", re.I)
    PATH_HINTS = ('.py', '.ts', '.tsx', '.js', '.jsx', '.rb', '.go', '.rs', '.java', '.json', '.yaml', '.yml', '.toml', '.md')
    SHELL_INFO_PHRASES = (
        'current shell', 'shell history', 'last history', 'last command', 'latest shell command', 'command history',
        'what shell am i using', 'what shell am i', 'where am i', 'current directory', 'working directory', 'who am i',
        'home directory', 'environment variable',
    )
    SHELL_INFO_TOKENS = ('history', 'pwd', 'whoami', '$shell', '$home')
    BUILD_VERBS = ('build', 'create', 'write', 'make', 'scaffold', 'bootstrap', 'generate')
    BUILD_TARGET_HINTS = (
        'mini project', 'small project', 'tiny project', 'script project', 'cli project', 'empty workspace',
        'empty directory', 'from scratch', 'create exactly these files', 'create these files', 'write these files',
    )
    BUILD_ARTIFACT_HINTS = (
        '.py', '.sh', '.md', '.txt', '.json', '.jsonl', '.csv', 'readme', 'script', 'project', 'cli', 'app', 'tool', 'utility',
    )

    def __init__(self) -> None:
        self.continuation_resolver = ContinuationResolver()

    def _tokens(self, lowered: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_.+-]+", lowered))

    def _looks_like_shell_task(self, text: str, lowered: str) -> bool:
        has_fenced_shell = bool(self.SHELL_FENCE_RE.search(text))
        has_shell_tokens = bool(self.SHELL_TOKEN_RE.search(text))
        has_inline_command = self._looks_like_inline_command_reference(text)
        mentions_existing_script = any(
            phrase in lowered
            for phrase in (
                'existing script', 'workspace script', 'existing workspace script', 'rerun the script', 're-run the script',
                'rerun the existing script', 're-run the existing script', 'rerun the existing workspace script',
                're-run the existing workspace script',
            )
        )
        asks_to_run = any(phrase in lowered for phrase in (
            'run command', 'execute command', 'use command', 'use a command', 'use shell command', 'use a shell command',
            'run this', 'execute this', 'run the following', 'execute the following', 'run this bash', 'execute this bash',
            'run this shell', 'execute this shell', 'use cli', 'use the cli', 'use command line', 'use the command line',
            'use terminal', 'use the terminal', 'via cli', 'via terminal',
        ))
        mentions_run_verb = bool(re.search(r"\b(?:run|execute|exec|launch|check)\b", lowered))
        starts_with_verb = lowered.startswith(self.COMMAND_VERBS)
        if mentions_run_verb and (has_inline_command or mentions_existing_script):
            return True
        return has_fenced_shell or has_shell_tokens or asks_to_run or starts_with_verb or (mentions_run_verb and has_inline_command)

    def _looks_like_inline_command_reference(self, text: str) -> bool:
        for match in self.INLINE_COMMAND_RE.finditer(text):
            body = (match.group("body") or "").strip()
            if not body:
                continue
            if self.SCRIPT_PATH_RE.fullmatch(body):
                return True
            if body.startswith(("./", "/")):
                return True
        return bool(self.SCRIPT_PATH_RE.search(text))

    def _looks_like_shell_inspection_task(self, lowered: str) -> bool:
        if self._looks_like_repo_task(lowered) and any(
            word in lowered
            for word in ('repo', 'code', 'codebase', 'file', 'function', 'module', 'class', 'implement', 'implemented', 'line')
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
                if not target or target in {'provider', 'model', 'tools', 'skills', 'config', 'status'}:
                    continue
                if target not in targets:
                    targets.append(target)
        words = re.findall(r"[a-z0-9_.+-]+", lowered)
        for index, word in enumerate(words):
            if word not in {'version', 'versions'} or index == 0:
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
            for word in ('repo', 'code', 'file', 'files', 'module', 'modules', 'class', 'function', 'parser', 'implementation', 'wizard', 'alias', 'aliases')
        ):
            return False
        return bool(self.extract_runtime_targets(prompt))

    def _looks_like_build_automation_task(self, lowered: str) -> bool:
        has_build_verb = any(re.search(rf"\b{re.escape(verb)}\b", lowered) for verb in self.BUILD_VERBS)
        if not has_build_verb:
            return False
        if not any(hint in lowered for hint in self.BUILD_TARGET_HINTS):
            return False
        return any(hint in lowered for hint in self.BUILD_ARTIFACT_HINTS) or any(
            phrase in lowered for phrase in ('then run', 'verify it works', 'verify it', 'run it', 'exact output', 'usage example')
        )

    def _looks_like_research_task(self, lowered: str, prompt: str) -> bool:
        if self._looks_like_repo_task(lowered) or self._looks_like_shell_inspection_task(lowered) or self._looks_like_runtime_inspection_task(prompt):
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
                'what tools', 'what skills', 'show config', 'what provider', 'which provider', 'active provider',
                'what model', 'which model', 'active model', 'provider am i using', 'model am i using', 'show permissions',
                'show memory', 'show sessions', 'permission mode', 'session id',
            )
        )

    def decision_for_task_signature(
        self,
        task_signature: str,
        *,
        reasoning: str,
        confidence: float = 0.78,
        source: str = 'project_context',
    ) -> RouteDecision | None:
        profile = self.TASK_SIGNATURE_PROFILES.get(task_signature)
        if profile is None:
            return None
        return RouteDecision(
            lane=profile['lane'],
            task_class=profile['task_class'],
            risk=str(profile['risk']),
            reasoning=reasoning,
            tool_families=list(profile['tool_families']),
            task_signature=task_signature,
            confidence=confidence,
            source=source,
        )

    def _lexical_route(self, prompt: str) -> RouteDecision:
        text = prompt.strip()
        lowered = text.lower()
        if not text:
            return RouteDecision(Lane.META, TaskClass.META, 'low', 'Empty prompt', [], 'meta/empty', 1.0, 'lexical')
        if self._looks_like_meta_request(lowered):
            return RouteDecision(
                lane=Lane.META,
                task_class=TaskClass.META,
                risk='low',
                reasoning='Deterministic runtime/meta question',
                tool_families=[],
                task_signature='meta/runtime',
                confidence=0.95,
                source='lexical',
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
                0.83,
                'lexical',
            )
        if any(word in lowered for word in ['spreadsheet', 'excel', '.xlsx', '.csv', 'dataframe', 'analyze sheet']):
            lane = Lane.DEEP if len(text) > 120 else Lane.STANDARD
            return RouteDecision(lane, TaskClass.DATA, 'medium', 'Structured data task', ['filesystem', 'data', 'python'], 'data/spreadsheet/analysis', 0.8, 'lexical')
        if self._looks_like_shell_task(text, lowered):
            lane = Lane.DEEP if len(text) > 180 else Lane.STANDARD
            return RouteDecision(
                lane,
                TaskClass.REPO,
                'medium',
                'Explicit shell command or execution request',
                ['filesystem', 'shell', 'python', 'git'],
                'repo/shell_execution',
                0.84,
                'lexical',
            )
        if self._looks_like_runtime_inspection_task(text):
            return RouteDecision(
                Lane.STANDARD,
                TaskClass.REPO,
                'medium',
                'Local runtime or installed software inspection request',
                ['shell'],
                'local/runtime_inspection',
                0.82,
                'lexical',
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
                0.8,
                'lexical',
            )
        if any(word in lowered for word in ['crawl', 'website', 'browser', 'click', 'scrape', 'site']):
            return RouteDecision(Lane.STANDARD, TaskClass.SITE, 'medium', 'Site/browser task', ['web', 'browser', 'filesystem'], 'site/understanding/general', 0.75, 'lexical')
        if self._looks_like_research_task(lowered, text):
            return RouteDecision(Lane.STANDARD, TaskClass.RESEARCH, 'medium', 'Research or live-source task', ['web', 'browser'], 'research/live_compare/general', 0.72, 'lexical')
        if any(word in lowered for word in ['extract', 'normalize', 'classify', 'label', 'schema', 'json']):
            return RouteDecision(Lane.STANDARD, TaskClass.EXTRACTION, 'low', 'Extraction or structured-output task', ['filesystem', 'python', 'data'], 'extract/general', 0.74, 'lexical')
        if self._looks_like_repo_task(lowered):
            lane = Lane.DEEP if len(text) > 180 else Lane.STANDARD
            return RouteDecision(lane, TaskClass.REPO, 'medium', 'Repo or file task', ['filesystem', 'shell', 'git', 'python'], 'repo/general', 0.7, 'lexical')
        if any(word in lowered for word in ['automate', 'workflow', 'repeat', 'script']):
            return RouteDecision(Lane.STANDARD, TaskClass.AUTOMATION, 'medium', 'Automation task', ['filesystem', 'shell', 'python'], 'automation/general', 0.68, 'lexical')
        if len(text) < 80:
            return RouteDecision(Lane.DIRECT, TaskClass.CONVERSATION, 'low', 'Short direct prompt', [], 'conversation/general', 0.45, 'lexical')
        return RouteDecision(Lane.STANDARD, TaskClass.CONVERSATION, 'low', 'General conversation', [], 'conversation/general', 0.42, 'lexical')

    def route(self, prompt: str, thread_context: ActiveTaskThread | None = None) -> RouteDecision:
        decision = self._lexical_route(prompt)
        if thread_context is None:
            return decision
        return self._merge_with_thread(prompt, decision, thread_context)

    def _merge_with_thread(self, prompt: str, decision: RouteDecision, thread: ActiveTaskThread) -> RouteDecision:
        lowered = prompt.lower().strip()
        if decision.lane == Lane.META:
            return decision
        if thread.task_signature.startswith('conversation/') and decision.task_signature.startswith('conversation/'):
            return RouteDecision(
                lane=decision.lane,
                task_class=decision.task_class,
                risk=decision.risk,
                reasoning=f"Continued conversation thread: {thread.task_signature}",
                tool_families=decision.tool_families,
                task_signature=thread.task_signature,
                confidence=max(decision.confidence, 0.72),
                source='continuation_inherited',
                continued_thread_id=thread.thread_id,
                continuation_decision='continue_active_thread',
            )
        if decision.task_signature.startswith('conversation/'):
            return RouteDecision(
                lane=Lane.STANDARD if thread.task_signature.startswith(('repo/', 'automation/', 'extract/', 'data/', 'local/', 'research/')) else decision.lane,
                task_class=TaskClass(thread.task_family) if thread.task_family in TaskClass._value2member_map_ else decision.task_class,
                risk=decision.risk,
                reasoning=f"Inherited active thread family {thread.task_signature} for short follow-up",
                tool_families=decision.tool_families or self._lexical_route(thread.summary_text()).tool_families,
                task_signature=thread.task_signature,
                confidence=max(decision.confidence, 0.78),
                source='continuation_inherited',
                continued_thread_id=thread.thread_id,
                continuation_decision='continue_active_thread',
            )
        return RouteDecision(
            lane=decision.lane,
            task_class=decision.task_class,
            risk=decision.risk,
            reasoning=f"Thread-aware route adjusted from active thread {thread.thread_id}: {decision.reasoning}",
            tool_families=decision.tool_families,
            task_signature=decision.task_signature,
            confidence=max(decision.confidence, 0.7),
            source='continuation_adjusted' if decision.task_signature != thread.task_signature else 'continuation_inherited',
            continued_thread_id=thread.thread_id,
            continuation_decision='continue_active_thread',
        )

    def resolve(
        self,
        prompt: str,
        *,
        active_threads: list[ActiveTaskThread] | None = None,
        recent_threads: list[ActiveTaskThread] | None = None,
        workspace_root: str = '',
        execution_cwd: str = '.',
    ) -> tuple[RouteDecision, ContinuationDecision]:
        active_threads = active_threads or []
        recent_threads = recent_threads or []
        continuation = self.continuation_resolver.resolve(
            prompt,
            active_threads=active_threads,
            recent_threads=recent_threads,
            workspace_root=workspace_root,
            execution_cwd=execution_cwd,
        )
        thread_context = None
        if continuation.thread_id:
            thread_context = next(
                (thread for thread in [*active_threads, *recent_threads] if thread.thread_id == continuation.thread_id),
                None,
            )
        decision = self.route(prompt, thread_context=thread_context)
        decision.continuation_decision = continuation.action
        decision.continued_thread_id = continuation.thread_id
        if continuation.action != 'start_new_thread' and thread_context is not None:
            if decision.source == 'lexical':
                decision.source = 'continuation_inherited'
            decision.confidence = max(decision.confidence, continuation.confidence)
        return decision, continuation
