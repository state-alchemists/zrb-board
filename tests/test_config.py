from board import config


def test_the_board_lives_under_the_home_folder(monkeypatch):
    monkeypatch.delenv("ZRB_BOARD_HOME", raising=False)
    assert config.home().name == "board"
    assert config.home().parent.name == ".zrb"


def test_the_folders_sit_under_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("ZRB_BOARD_HOME", str(tmp_path))
    monkeypatch.delenv("ZRB_BOARD_DOCS", raising=False)
    monkeypatch.delenv("ZRB_BOARD_META", raising=False)
    assert config.docs_dir() == tmp_path / "docs"
    assert config.meta_dir() == tmp_path / ".board"


def test_a_tilde_in_the_home_folder_is_expanded(monkeypatch):
    monkeypatch.setenv("ZRB_BOARD_HOME", "~/somewhere/board")
    assert "~" not in str(config.home())


def test_either_folder_can_be_moved_on_its_own(monkeypatch, tmp_path):
    monkeypatch.setenv("ZRB_BOARD_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ZRB_BOARD_DOCS", str(tmp_path / "writing"))
    monkeypatch.setenv("ZRB_BOARD_META", str(tmp_path / "notes"))
    assert config.docs_dir() == tmp_path / "writing"
    assert config.meta_dir() == tmp_path / "notes"


def test_the_prefix_comes_from_the_variable_zrb_itself_reads(monkeypatch):
    monkeypatch.setenv("_ZRB_ENV_PREFIX", "ACME")
    assert config.env_name("HOME") == "ACME_BOARD_HOME"
    monkeypatch.setenv("ACME_BOARD_PORT", "9000")
    assert config.port() == 9000


def test_the_address_is_the_desk_and_the_port_is_known(monkeypatch):
    monkeypatch.delenv("ZRB_BOARD_HOST", raising=False)
    monkeypatch.delenv("ZRB_BOARD_PORT", raising=False)
    assert config.host() == "127.0.0.1"
    assert config.port() == 8765


def test_the_server_builds_its_store_from_the_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("ZRB_BOARD_HOME", str(tmp_path))
    from board.app import create_app

    app = create_app()
    store = app.state.store if hasattr(app.state, "store") else None
    assert (tmp_path / "docs").is_dir()
    assert (tmp_path / ".board").is_dir()
    assert store is None or store.docs_dir == tmp_path / "docs"
