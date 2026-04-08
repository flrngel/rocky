from __future__ import annotations

from pathlib import Path

from rocky.config.models import LearningConfig
from rocky.learning.episodes import EpisodeStore
from rocky.learning.manager import LearningManager


def test_learning_manager_publishes_skill(tmp_path: Path) -> None:
    manager = LearningManager(
        support_dir=tmp_path / 'support',
        query_dir=tmp_path / 'query',
        learned_root=tmp_path / 'learned',
        artifacts_dir=tmp_path / 'artifacts',
        policies_dir=tmp_path / 'policies',
        config=LearningConfig(),
    )
    result = manager.learn_from_feedback(
        task_signature='extract/whiskey_ner/batch',
        prompt='extract entities',
        answer='bad answer',
        feedback='include bottle size and distillery',
        trace={'selected_tools': ['read_file']},
    )
    assert result['published'] is True
    assert Path(result['skill_path']).exists()
    assert Path(result["reflection_path"]).exists()
    learned = manager.list_learned()
    assert learned

    skill_text = Path(result["skill_path"]).read_text(encoding="utf-8")
    assert "include bottle size and distillery" in skill_text
    assert "Operational guidance" in skill_text
    assert "Workspace hints" in skill_text


def test_episode_store_recreates_query_directory_on_write(tmp_path: Path) -> None:
    store = EpisodeStore(
        support_dir=tmp_path / "support",
        query_dir=tmp_path / "query",
        generation_file=tmp_path / "learned" / "generation.json",
    )

    store.query_dir.rmdir()
    result = store.record_query({"task_signature": "automation/general"})

    assert Path(result["path"]).exists()
