from types import SimpleNamespace
from unittest.mock import MagicMock

from services import supabase_service


def _mock_inspection_client(existing_rows, write_rows=None):
    client = MagicMock()
    table = client.table.return_value

    lookup = MagicMock()
    lookup.eq.return_value = lookup
    lookup.limit.return_value = lookup
    lookup.execute.return_value = SimpleNamespace(data=existing_rows)
    table.select.return_value = lookup

    write = MagicMock()
    write.eq.return_value = write
    write.execute.return_value = SimpleNamespace(data=write_rows or [])
    table.update.return_value = write
    table.insert.return_value = MagicMock(
        execute=MagicMock(return_value=SimpleNamespace(data=write_rows or []))
    )
    return client, table, lookup, write


def _configure_inspection(monkeypatch, client):
    monkeypatch.setattr(supabase_service, "get_supabase", lambda: client)
    monkeypatch.setattr(
        supabase_service,
        "get_detail_kategori_map",
        lambda: {"Healthy": "category-uuid"},
    )


def test_save_inspection_updates_by_composite_primary_key(monkeypatch):
    client, table, lookup, update = _mock_inspection_client([{"jumlah": 2}])
    _configure_inspection(monkeypatch, client)

    row = supabase_service.save_inspection(
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
    assert row["id"] == "session-uuid:category-uuid"


def test_save_inspection_inserts_required_foreign_keys(monkeypatch):
    client, table, _, _ = _mock_inspection_client([])
    _configure_inspection(monkeypatch, client)
    inserted_payload = {}

    def capture_insert(payload):
        inserted_payload.update(payload)
        return MagicMock(
            execute=MagicMock(return_value=SimpleNamespace(data=[]))
        )

    table.insert.side_effect = capture_insert

    row = supabase_service.save_inspection(
        grade="SEHAT",
        sesi_id="session-uuid",
        ai_class="Healthy",
    )

    assert inserted_payload == {
        "kategori": "Healthy",
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
    monkeypatch.setattr(supabase_service, "get_supabase", lambda: client)

    rows = supabase_service.fetch_latest_inspections(limit=5)

    assert rows[0]["id"] == "session-uuid:category-uuid"
    assert rows[0]["sesi"]["nomor_sesi"] == 3
    assert query.select.call_args.args[0].endswith(
        "inspeksi!inner(kategori, jumlah, sesi_id, kategori_id)"
    )
    query.limit.assert_called_once_with(5)
