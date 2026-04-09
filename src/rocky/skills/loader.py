from __future__ import annotations

from pathlib import Path

from rocky.skills.models import Skill
from rocky.util.io import read_text
from rocky.util.yamlx import split_frontmatter


class SkillLoader:
    def __init__(self, workspace_root: Path, global_root: Path, bundled_root: Path) -> None:
        self.workspace_root = workspace_root
        self.global_root = global_root
        self.bundled_root = bundled_root

    def _scan(self, root: Path, scope: str, origin: str) -> list[Skill]:
        skills: list[Skill] = []
        if not root.exists():
            return skills
        for path in sorted(root.rglob('SKILL.md')):
            try:
                raw = read_text(path)
                metadata, body = split_frontmatter(raw)
                name = str(metadata.get('name') or path.parent.name)
                skills.append(Skill(name=name, scope=scope, path=path, body=body, metadata=metadata, origin=origin))
            except Exception:
                continue
        return skills

    def load_all(self) -> list[Skill]:
        project = self.workspace_root / '.rocky' / 'skills'
        compat_project = [self.workspace_root / '.claude' / 'skills', self.workspace_root / '.agents' / 'skills']
        compat_global = [Path.home() / '.claude' / 'skills', Path.home() / '.agents' / 'skills']
        items: list[Skill] = []
        items += self._scan(self.bundled_root, 'bundled', 'bundled')
        items += self._scan(self.global_root / 'skills', 'global', 'global')
        items += self._scan(project / 'bundled', 'project', 'project_bundled')
        items += self._scan(project / 'project', 'project', 'project')
        for root in compat_project:
            items += self._scan(root, 'project', 'compat')
        for root in compat_global:
            items += self._scan(root, 'global', 'compat')
        return items
