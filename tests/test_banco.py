import sqlite3

import pytest

from ted_search import banco


def _criar(caminho, linhas=4000):
    conn = sqlite3.connect(caminho)
    conn.execute("CREATE TABLE julgados (id TEXT PRIMARY KEY, texto TEXT)")
    conn.executemany("INSERT INTO julgados VALUES (?, ?)", ((f"j{i}", f"ementa {i} " * 40) for i in range(linhas)))
    conn.commit()
    conn.close()


def test_ida_e_volta_em_varias_partes(tmp_path, monkeypatch):
    db = tmp_path / "ted.db"
    _criar(db)
    original = db.read_bytes()
    monkeypatch.setattr(banco, "TAMANHO_DA_PARTE", 3000)  # força várias partes
    manifesto = banco.empacotar(db, nivel=1)
    assert len(manifesto["partes"]) > 2 and manifesto["julgados"] == 4000
    assert all(p["bytes"] <= 3000 for p in manifesto["partes"])
    db.unlink()
    resultado = banco.desempacotar(db)
    assert db.read_bytes() == original
    assert resultado["sha256"] == manifesto["sha256"]


def test_parte_faltando_e_recusada(tmp_path, monkeypatch):
    db = tmp_path / "ted.db"
    _criar(db)
    monkeypatch.setattr(banco, "TAMANHO_DA_PARTE", 3000)
    banco.empacotar(db, nivel=1)
    db.unlink()
    sorted(tmp_path.glob("ted.db.xz.parte-*"))[-1].unlink()
    with pytest.raises(ValueError):
        banco.desempacotar(db)
    assert not db.exists()


def test_parte_adulterada_e_recusada(tmp_path, monkeypatch):
    db = tmp_path / "ted.db"
    _criar(db)
    monkeypatch.setattr(banco, "TAMANHO_DA_PARTE", 10**9)
    banco.empacotar(db, nivel=1)
    db.unlink()
    manifesto = tmp_path / "ted.db.manifesto.json"
    manifesto.write_text(manifesto.read_text().replace('"bytes": ', '"bytes": 1'))
    with pytest.raises(ValueError):
        banco.desempacotar(db)


def test_get_connection_recompoe_o_banco(tmp_path, monkeypatch):
    from ted_search.db import get_connection

    db = tmp_path / "ted.db"
    _criar(db, linhas=50)
    banco.empacotar(db, nivel=1)
    db.unlink()
    with get_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM julgados").fetchone()[0] == 50


def test_servico_abre_o_banco_sem_escrita(tmp_path, monkeypatch):
    """No Cloud Run o banco vai na imagem: abrir para escrita copiaria o arquivo inteiro para a memória."""
    import sqlite3

    import pytest

    from ted_search import db

    caminho = tmp_path / "b.db"
    with sqlite3.connect(caminho) as c:
        c.execute("create table t (x)")
        c.execute("insert into t values (1)")
    monkeypatch.setenv("TED_DB_READONLY", "1")
    db.init_db(caminho)  # não toca no arquivo
    conn = db.get_connection(caminho)
    assert conn.execute("select x from t").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("insert into t values (2)")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.db"]  # nem -wal nem -journal
