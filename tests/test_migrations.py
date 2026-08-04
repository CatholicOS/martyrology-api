from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


def test_alembic_tree_has_exactly_one_head():
    # A second head means two migrations claim the same parent — `alembic
    # upgrade head` then fails at deploy time rather than here.
    assert len(_script_directory().get_heads()) == 1


def test_baseline_revision_exists():
    revisions = {r.revision for r in _script_directory().walk_revisions()}
    assert "0001_baseline" in revisions
