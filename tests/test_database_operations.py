from types import SimpleNamespace
from unittest.mock import MagicMock

import app as app_module
from endpoint import model as endpoint_model


def _mock_inspection_client(existing_rows, write_rows=None):
    client = MagicMock()
    table = client.table.return_value

    session_lookup = MagicMock()
    session_lookup.eq.return_value = session_lookup
    session_lookup.limit.return_value = session_lookup
    session_lookup.execute.return_value = SimpleNamespace(
        data=[{"id": "session-uuid", "waktu_selesai": None}]
    )

    lookup = MagicMock()
    lookup.eq.return_value = lookup
    lookup.limit.return_value = lookup
    lookup.execute.return_value = SimpleNamespace(data=existing_rows)
    table.select.side_effect = [session_lookup, lookup]

    write = MagicMock()
    write.eq.return_value = write
    write.execute.return_value = SimpleNamespace(data=write_rows or [{
        "sesi_id": "session-uuid",
        "kategori_id": "category-uuid",
        "jumlah": 3,
    }])
    table.update.return_value = write
    table.insert.return_value = MagicMock(
        execute=MagicMock(return_value=SimpleNamespace(data=[{
            "sesi_id": "session-uuid",
            "kategori_id": "category-uuid",
            "jumlah": 1,
        }]))
    )
    return client, table, lookup, write


def _configure_inspection(monkeypatch, client):
    monkeypatch.setattr(endpoint_model, "get_supabase", lambda: client)
    monkeypatch.setattr(
        endpoint_model,
        "get_detail_kategori_map",
        lambda: {"Healthy": "category-uuid"},
    )


def test_save_inspection_updates_by_composite_primary_key(monkeypatch):
    client, table, lookup, update = _mock_inspection_client([{"jumlah": 2}])
    _configure_inspection(monkeypatch, client)

    row = endpoint_model.save_inspection(
        grade="SEHAT",
        sesi_id="session-uuid",
        ai_class="Healthy",
    )

    assert table.select.call_args.args == ("jumlah",)
    assert lookup.eq.call_args_list[0].args == ("sesi_id", "session-uuid")
    assert lookup.eq.call_args_list[1].args == ("kategori_id", "category-uuid")
    assert update.eq.call_args_list[0].args == ("sesi_id", "session-uuid")
    assert update.eq.call_args_list[1].args == ("kategori_id", "category-uuid")
    assert row["jumlah"] == 3
    assert row["sesi_id"] == "session-uuid"
    assert row["kategori_id"] == "category-uuid"


def test_save_inspection_inserts_required_foreign_keys(monkeypatch):
    client, table, _, _ = _mock_inspection_client([])
    _configure_inspection(monkeypatch, client)
    inserted_payload = {}

    def capture_insert(payload):
        inserted_payload.update(payload)
        return MagicMock(
            execute=MagicMock(return_value=SimpleNamespace(data=[{
                "sesi_id": "session-uuid",
                "kategori_id": "category-uuid",
                "jumlah": 1,
            }]))
        )

    table.insert.side_effect = capture_insert

    row = endpoint_model.save_inspection(
        grade="SEHAT",
        sesi_id="session-uuid",
        ai_class="Healthy",
    )

    assert inserted_payload == {
        "sesi_id": "session-uuid",
        "kategori_id": "category-uuid",
        "jumlah": 1,
    }
    assert row["jumlah"] == 1


def test_fetch_latest_inspections_builds_rows_from_session_relation(monkeypatch):
    client = MagicMock()
    query = client.table.return_value
    query.select.return_value = query
    query.order.return_value = query
    query.limit.return_value = query
    query.execute.return_value = SimpleNamespace(
        data=[
            {
                "id": "session-uuid",
                "tanggal": "2026-11-26",
                "nomor_sesi": 3,
                "waktu_mulai": "2026-11-26T10:00:00+00:00",
                "waktu_selesai": None,
                "inspeksi": [
                    {
                        "kategori": "Healthy",
                        "jumlah": 2,
                        "sesi_id": "session-uuid",
                        "kategori_id": "category-uuid",
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(app_module, "get_supabase", lambda: client)

    rows = app_module.fetch_latest_inspections(limit=5)

    assert rows[0]["id"] == "session--cate"
    assert rows[0]["sesi"]["nomor_sesi"] == 3
    assert query.select.call_args.args[0] == (
        "id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai, "
        "inspeksi(sesi_id, kategori_id, jumlah, detail_kategori(id, nama_kategori))"
    )
    query.limit.assert_called_once_with(5)


def test_stop_active_session_returns_updated_session(monkeypatch):
    client = MagicMock()
    query = client.table.return_value.update.return_value
    query.eq.return_value = query
    query.is_.return_value = query
    query.select.return_value = query
    updated_session = {
        "id": "session-uuid",
        "tanggal": "2026-11-28",
        "nomor_sesi": 3,
        "waktu_mulai": "2026-11-28T02:13:46+00:00",
        "waktu_selesai": "2026-11-28T02:20:00+00:00",
    }
    query.execute.return_value = SimpleNamespace(data=[updated_session])
    monkeypatch.setattr(app_module, "get_supabase", lambda: client)

    result = app_module.stop_active_session("session-uuid")

    assert result == updated_session
    query.eq.assert_called_once_with("id", "session-uuid")
    query.is_.assert_called_once_with("waktu_selesai", "null")
    query.select.assert_called_once_with(
        "id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai"
    )


def test_stop_active_session_fails_when_no_row_was_updated(monkeypatch):
    client = MagicMock()
    query = client.table.return_value.update.return_value
    query.eq.return_value = query
    query.is_.return_value = query
    query.select.return_value = query
    query.execute.return_value = SimpleNamespace(data=[])
    monkeypatch.setattr(app_module, "get_supabase", lambda: client)

    result = app_module.stop_active_session("session-uuid")

    assert result is None
