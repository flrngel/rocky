from __future__ import annotations

from rocky.skills.loader import SkillLoader


def test_skill_loader_ignores_learned_policy_and_legacy_learning_dirs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    project_skill = workspace / ".rocky" / "skills" / "project" / "repo-helper" / "SKILL.md"
    learned_policy = workspace / ".rocky" / "policies" / "learned" / "catalog-contract" / "POLICY.md"
    legacy_learned = workspace / ".rocky" / "skills" / "learned" / "legacy-contract" / "SKILL.md"

    project_skill.parent.mkdir(parents=True, exist_ok=True)
    learned_policy.parent.mkdir(parents=True, exist_ok=True)
    legacy_learned.parent.mkdir(parents=True, exist_ok=True)

    project_skill.write_text("---\nname: repo-helper\n---\n\n# Repo helper\n", encoding="utf-8")
    learned_policy.write_text("---\npolicy_id: catalog-contract\n---\n\n# Learned policy\n", encoding="utf-8")
    legacy_learned.write_text("---\nname: legacy-contract\n---\n\n# Legacy learned artifact\n", encoding="utf-8")

    loader = SkillLoader(workspace, tmp_path / "global", tmp_path / "bundled")
    skills = loader.load_all()

    names = [skill.name for skill in skills]

    assert "repo-helper" in names
    assert "catalog-contract" not in names
    assert "legacy-contract" not in names
